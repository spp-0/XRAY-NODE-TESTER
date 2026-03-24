import base64
import concurrent.futures
import hashlib
import json
import os
import secrets
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

DATA_DIR = os.environ.get("XRAY_WEB_DATA_DIR", "/data/xray1")
VMESS_FILE = os.path.join(DATA_DIR, "vmess.txt")
OTHER_FILE = os.path.join(DATA_DIR, "nodes.json")
XRAY_BIN = os.environ.get("XRAY_BIN", os.path.join(DATA_DIR, "xray"))
TEST_URL_DEFAULT = os.environ.get("XRAY_TEST_URL", "https://www.google.com/generate_204")
MAX_WORKERS = int(os.environ.get("XRAY_TEST_WORKERS", "8"))
CONNECT_TIMEOUT = int(os.environ.get("XRAY_TEST_TIMEOUT", "6"))
CURL_BIN = os.environ.get("CURL_BIN", "curl")

ADMIN_USER = os.environ.get("XRAY_ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("XRAY_ADMIN_PASS", "admin123")
AUTH_SALT = os.environ.get("XRAY_AUTH_SALT", "xray-web")
SESSION_DAYS = int(os.environ.get("XRAY_SESSION_DAYS", "7"))
LOGIN_MAX_ATTEMPTS = int(os.environ.get("XRAY_LOGIN_MAX_ATTEMPTS", "5"))
LOGIN_WINDOW_MIN = int(os.environ.get("XRAY_LOGIN_WINDOW_MIN", "10"))
LOGIN_LOCK_MIN = int(os.environ.get("XRAY_LOGIN_LOCK_MIN", "15"))
AUTO_CHECK_DEFAULT_INTERVAL = int(os.environ.get("XRAY_AUTO_CHECK_INTERVAL", "30"))
DEFAULT_SUB_INTERVAL_MIN = int(os.environ.get("XRAY_DEFAULT_SUB_INTERVAL_MIN", "60"))

DB_PATH = os.path.join(DATA_DIR, "data.db")

APP_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI()
app.mount("/static", StaticFiles(directory=os.path.join(APP_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(APP_DIR, "templates"))

state_lock = threading.Lock()
run_lock = threading.Lock()

test_status = {}


def _now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _get_status(username: str) -> dict:
    if username not in test_status:
        test_status[username] = {
            "running": False,
            "total": 0,
            "done": 0,
            "started_at": None,
            "finished_at": None,
            "last_error": "",
            "cancel": False,
        }
    return test_status[username]


def _db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _column_exists(conn, table: str, column: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table})")
    cols = [r["name"] for r in cur.fetchall()]
    return column in cols


def _migrate_schema(conn):
    # migrate nodes: add owner and composite primary key
    if _column_exists(conn, "nodes", "owner") is False:
        conn.execute("ALTER TABLE nodes RENAME TO nodes_old")
        conn.execute(
            """
            CREATE TABLE nodes (
                id TEXT NOT NULL,
                owner TEXT NOT NULL,
                type TEXT NOT NULL,
                raw TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (owner, id)
            )
            """
        )
        conn.execute(
            "INSERT INTO nodes (id, owner, type, raw, created_at) SELECT id, ?, type, raw, created_at FROM nodes_old",
            (ADMIN_USER,),
        )
        conn.execute("DROP TABLE nodes_old")

    # migrate node_status: add owner and composite primary key
    if _column_exists(conn, "node_status", "owner") is False:
        conn.execute("ALTER TABLE node_status RENAME TO node_status_old")
        conn.execute(
            """
            CREATE TABLE node_status (
                node_id TEXT NOT NULL,
                owner TEXT NOT NULL,
                status TEXT,
                latency_ms INTEGER,
                error TEXT,
                checked_at TEXT,
                PRIMARY KEY (owner, node_id)
            )
            """
        )
        conn.execute(
            "INSERT INTO node_status (node_id, owner, status, latency_ms, error, checked_at) "
            "SELECT node_id, ?, status, latency_ms, error, checked_at FROM node_status_old",
            (ADMIN_USER,),
        )
        conn.execute("DROP TABLE node_status_old")


def _init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(DB_PATH):
        open(DB_PATH, "a", encoding="utf-8").close()
    conn = _db()
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS nodes (
                id TEXT NOT NULL,
                owner TEXT NOT NULL,
                type TEXT NOT NULL,
                raw TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (owner, id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS node_status (
                node_id TEXT NOT NULL,
                owner TEXT NOT NULL,
                status TEXT,
                latency_ms INTEGER,
                error TEXT,
                checked_at TEXT,
                consecutive_fail INTEGER DEFAULT 0,
                PRIMARY KEY (owner, node_id)
            )
            """
        )
        # add blacklist fields to nodes if not exists
        if not _column_exists(conn, "nodes", "disabled"):
            conn.execute("ALTER TABLE nodes ADD COLUMN disabled INTEGER DEFAULT 0")
        if not _column_exists(conn, "nodes", "disabled_reason"):
            conn.execute("ALTER TABLE nodes ADD COLUMN disabled_reason TEXT")
        if not _column_exists(conn, "nodes", "blacklist_until"):
            conn.execute("ALTER TABLE nodes ADD COLUMN blacklist_until TEXT")

        # add status counters
        if not _column_exists(conn, "node_status", "consecutive_fail"):
            conn.execute("ALTER TABLE node_status ADD COLUMN consecutive_fail INTEGER DEFAULT 0")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS login_attempts (
                ip TEXT NOT NULL,
                username TEXT NOT NULL,
                failed_count INTEGER NOT NULL,
                first_failed_at TEXT NOT NULL,
                last_failed_at TEXT NOT NULL,
                locked_until TEXT,
                PRIMARY KEY (ip, username)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_settings (
                username TEXT PRIMARY KEY,
                auto_check_enabled INTEGER NOT NULL,
                interval_min INTEGER NOT NULL,
                next_run_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS subscriptions (
                id TEXT PRIMARY KEY,
                owner TEXT NOT NULL,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                type TEXT NOT NULL,
                enabled INTEGER NOT NULL,
                interval_min INTEGER,
                last_fetch_at TEXT,
                last_status TEXT,
                last_error TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS export_rules (
                id TEXT PRIMARY KEY,
                owner TEXT NOT NULL,
                name TEXT NOT NULL,
                token TEXT NOT NULL,
                format TEXT NOT NULL,
                enabled INTEGER NOT NULL,
                rules_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        _migrate_schema(conn)
        conn.commit()
    finally:
        conn.close()


def _hash_password(pw: str) -> str:
    return hashlib.sha256((AUTH_SALT + pw).encode("utf-8")).hexdigest()


def _ensure_admin():
    conn = _db()
    try:
        cur = conn.execute("SELECT COUNT(*) AS c FROM users")
        count = cur.fetchone()["c"]
        if count == 0:
            conn.execute(
                "INSERT INTO users (username, password_hash, role, created_at) VALUES (?,?,?,?)",
                (ADMIN_USER, _hash_password(ADMIN_PASS), "admin", _now_str())
            )
            conn.commit()
    finally:
        conn.close()


def _get_user_role(username: str) -> str:
    conn = _db()
    try:
        cur = conn.execute("SELECT role FROM users WHERE username=?", (username,))
        row = cur.fetchone()
        return row["role"] if row else "user"
    finally:
        conn.close()


def _get_setting(key: str, default: str = "") -> str:
    conn = _db()
    try:
        cur = conn.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = cur.fetchone()
        if row:
            return row["value"]
        if default != "":
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, default))
            conn.commit()
        return default
    finally:
        conn.close()


def _set_setting(key: str, value: str):
    conn = _db()
    try:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
        conn.commit()
    finally:
        conn.close()


def _get_default_sub_interval() -> int:
    try:
        value = _get_setting("default_sub_interval_min", str(DEFAULT_SUB_INTERVAL_MIN))
        return int(value)
    except Exception:
        return DEFAULT_SUB_INTERVAL_MIN


def _get_due_subscriptions(now: datetime) -> list[sqlite3.Row]:
    conn = _db()
    try:
        cur = conn.execute(
            """
            SELECT id, owner, name, url, type, enabled, interval_min, last_fetch_at, last_status, last_error
            FROM subscriptions
            WHERE enabled=1
            """
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    due = []
    for row in rows:
        interval = row["interval_min"] or _get_default_sub_interval()
        last_fetch = row["last_fetch_at"]
        if not last_fetch:
            due.append(row)
            continue
        try:
            last_dt = datetime.strptime(last_fetch, "%Y-%m-%d %H:%M:%S")
            if now >= last_dt + timedelta(minutes=int(interval)):
                due.append(row)
        except Exception:
            due.append(row)
    return due


def _update_subscription_status(sub_id: str, status: str, error: str | None = None):
    conn = _db()
    try:
        conn.execute(
            "UPDATE subscriptions SET last_fetch_at=?, last_status=?, last_error=? WHERE id=?",
            (_now_str(), status, error or "", sub_id),
        )
        conn.commit()
    finally:
        conn.close()


def _subscription_fetch_loop():
    while True:
        time.sleep(30)
        now = datetime.now()
        for sub in _get_due_subscriptions(now):
            try:
                content = _fetch_subscription(sub["url"])
                links = _parse_subscription_content(content)
                added = 0
                skipped = 0
                for link in links:
                    try:
                        node = parse_node(link)
                        node_id = _node_id(link)
                        if _find_node_by_id(sub["owner"], node_id):
                            skipped += 1
                            continue
                        _upsert_node(sub["owner"], node.get("type", "other"), link)
                        added += 1
                    except Exception:
                        continue
                _update_subscription_status(sub["id"], "ok", "")
            except Exception as e:
                _update_subscription_status(sub["id"], "fail", str(e))


def _ensure_user_settings(username: str):
    conn = _db()
    try:
        cur = conn.execute("SELECT username FROM user_settings WHERE username=?", (username,))
        row = cur.fetchone()
        if not row:
            conn.execute(
                "INSERT INTO user_settings (username, auto_check_enabled, interval_min, next_run_at) VALUES (?,?,?,?)",
                (username, 0, AUTO_CHECK_DEFAULT_INTERVAL, None),
            )
            conn.commit()
    finally:
        conn.close()


def _get_user_settings(username: str) -> dict:
    _ensure_user_settings(username)
    conn = _db()
    try:
        cur = conn.execute(
            "SELECT auto_check_enabled, interval_min, next_run_at FROM user_settings WHERE username=?",
            (username,),
        )
        row = cur.fetchone()
        return {
            "auto_check_enabled": int(row["auto_check_enabled"]),
            "interval_min": int(row["interval_min"]),
            "next_run_at": row["next_run_at"] or "",
        }
    finally:
        conn.close()


def _set_user_settings(username: str, enabled: int, interval_min: int, next_run_at: str | None):
    conn = _db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO user_settings (username, auto_check_enabled, interval_min, next_run_at) VALUES (?,?,?,?)",
            (username, int(enabled), int(interval_min), next_run_at),
        )
        conn.commit()
    finally:
        conn.close()


def _ensure_legacy_files():
    if not os.path.exists(VMESS_FILE):
        open(VMESS_FILE, "a", encoding="utf-8").close()
    if not os.path.exists(OTHER_FILE):
        with open(OTHER_FILE, "w", encoding="utf-8") as f:
            json.dump([], f)


def _load_vmess_lines():
    if not os.path.exists(VMESS_FILE):
        return []
    with open(VMESS_FILE, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f.readlines()]
    return [l for l in lines if l and not l.startswith("#")]


def _load_other_nodes():
    if not os.path.exists(OTHER_FILE):
        return []
    try:
        with open(OTHER_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def _node_id(raw):
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _b64decode(data: str) -> str:
    data = data.strip()
    pad = (4 - len(data) % 4) % 4
    data = data + ("=" * pad)
    return base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", errors="ignore")


def _b64encode(data: str) -> str:
    return base64.urlsafe_b64encode(data.encode("utf-8")).decode("utf-8").rstrip("=")


def _std_b64encode(data: str) -> str:
    return base64.b64encode(data.encode("utf-8")).decode("utf-8")


def _extract_links(text: str) -> list[str]:
    items = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(("vmess://", "vless://", "trojan://", "ss://")):
            items.append(line)
    return items


def _parse_clash_yaml(text: str) -> list[str]:
    try:
        import yaml  # type: ignore
    except Exception:
        return []

    try:
        data = yaml.safe_load(text)
    except Exception:
        return []

    if not isinstance(data, dict):
        return []
    proxies = data.get("proxies")
    if not isinstance(proxies, list):
        return []

    links: list[str] = []
    for p in proxies:
        if not isinstance(p, dict):
            continue
        ptype = str(p.get("type", "")).lower()
        name = str(p.get("name", "")).strip()
        server = p.get("server") or p.get("server_name")
        port = p.get("port")
        if not server or not port:
            continue

        if ptype == "ss":
            method = p.get("cipher") or p.get("method")
            password = p.get("password")
            if not method or not password:
                continue
            userinfo = _b64encode(f"{method}:{password}")
            link = f"ss://{userinfo}@{server}:{port}#{urllib.parse.quote(name)}"
            links.append(link)
            continue

        if ptype == "trojan":
            password = p.get("password")
            if not password:
                continue
            params = {}
            if p.get("sni"):
                params["sni"] = p.get("sni")
            if p.get("alpn"):
                params["alpn"] = ",".join(p.get("alpn")) if isinstance(p.get("alpn"), list) else p.get("alpn")
            if p.get("skip-cert-verify") is True:
                params["allowInsecure"] = "1"
            query = urllib.parse.urlencode(params) if params else ""
            link = f"trojan://{password}@{server}:{port}"
            if query:
                link += f"?{query}"
            if name:
                link += f"#{urllib.parse.quote(name)}"
            links.append(link)
            continue

        if ptype in ("vmess", "vless"):
            uuid = p.get("uuid") or p.get("id")
            if not uuid:
                continue
            network = p.get("network") or p.get("type") or "tcp"
            tls = "tls" if p.get("tls") else ""
            sni = p.get("sni") or p.get("servername") or ""
            host = ""
            path = ""
            if network == "ws":
                ws_opts = p.get("ws-opts") or {}
                path = ws_opts.get("path") or ""
                headers = ws_opts.get("headers") or {}
                host = headers.get("Host") or headers.get("host") or ""
            if ptype == "vmess":
                obj = {
                    "v": "2",
                    "ps": name,
                    "add": server,
                    "port": str(port),
                    "id": uuid,
                    "aid": str(p.get("alterId") or p.get("alter_id") or 0),
                    "net": network,
                    "type": "none",
                    "host": host,
                    "path": path,
                    "tls": "tls" if p.get("tls") else "",
                    "sni": sni,
                }
                link = "vmess://" + _b64encode(json.dumps(obj, ensure_ascii=False))
                links.append(link)
            else:
                params = {"type": network}
                if tls:
                    params["security"] = "tls"
                if sni:
                    params["sni"] = sni
                if host:
                    params["host"] = host
                if path:
                    params["path"] = path
                query = urllib.parse.urlencode(params)
                link = f"vless://{uuid}@{server}:{port}?{query}"
                if name:
                    link += f"#{urllib.parse.quote(name)}"
                links.append(link)
            continue

    return links


def _parse_subscription_content(content: str) -> list[str]:
    content = content.strip()
    if not content:
        return []

    # clash yaml (priority)
    links = _parse_clash_yaml(content)
    if links:
        return links

    # plain text
    links = _extract_links(content)
    if links:
        return links

    # base64 text
    try:
        decoded = _b64decode(content)
        links = _extract_links(decoded)
        if links:
            return links
    except Exception:
        pass

    # sing-box stub (optional)
    return []


def _fetch_subscription(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Xray-Node-Tester)"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = resp.read()
    try:
        return data.decode("utf-8")
    except Exception:
        return data.decode("utf-8", errors="ignore")


def parse_vmess(raw: str):
    payload = raw[len("vmess://"):]
    decoded = _b64decode(payload)
    data = json.loads(decoded)
    return {
        "type": "vmess",
        "name": data.get("ps", ""),
        "address": data.get("add", ""),
        "port": int(data.get("port", 0) or 0),
        "uuid": data.get("id", ""),
        "aid": int(data.get("aid", 0) or 0),
        "net": data.get("net", "tcp"),
        "tls": data.get("tls", ""),
        "host": data.get("host", ""),
        "path": data.get("path", ""),
        "sni": data.get("sni", ""),
        "raw": raw,
        "extra": data
    }


def parse_url_node(raw: str, scheme: str):
    u = urllib.parse.urlparse(raw)
    if u.scheme != scheme:
        raise ValueError("scheme mismatch")
    q = urllib.parse.parse_qs(u.query)

    def q1(k):
        return q.get(k, [""])[0]

    name = urllib.parse.unquote(u.fragment) if u.fragment else ""
    return {
        "type": scheme,
        "name": name,
        "address": u.hostname or "",
        "port": int(u.port or 0),
        "uuid": u.username or "",
        "password": u.username or "",
        "net": q1("type") or q1("net") or "tcp",
        "security": q1("security") or q1("tls") or "",
        "host": q1("host") or "",
        "path": q1("path") or "",
        "sni": q1("sni") or q1("peer") or "",
        "flow": q1("flow") or "",
        "fp": q1("fp") or "",
        "pbk": q1("pbk") or q1("publicKey") or "",
        "sid": q1("sid") or "",
        "serviceName": q1("serviceName") or q1("service") or "",
        "raw": raw,
        "extra": {"query": q}
    }


def parse_ss(raw: str):
    # Minimal SS parser supporting ss://base64(method:pass)@host:port#name
    u = urllib.parse.urlparse(raw)
    if u.scheme != "ss":
        raise ValueError("scheme mismatch")
    name = urllib.parse.unquote(u.fragment) if u.fragment else ""
    method = ""
    password = ""
    host = ""
    port = 0

    if u.netloc and "@" in u.netloc:
        userinfo, hostinfo = u.netloc.split("@", 1)
        decoded = _b64decode(userinfo)
        if ":" in decoded:
            method, password = decoded.split(":", 1)
        if ":" in hostinfo:
            host, port_str = hostinfo.rsplit(":", 1)
            port = int(port_str)
        else:
            host = hostinfo
    else:
        # ss://base64(method:pass@host:port)
        decoded = _b64decode(u.netloc)
        if "@" in decoded:
            userinfo, hostinfo = decoded.split("@", 1)
            if ":" in userinfo:
                method, password = userinfo.split(":", 1)
            if ":" in hostinfo:
                host, port_str = hostinfo.rsplit(":", 1)
                port = int(port_str)
            else:
                host = hostinfo

    return {
        "type": "ss",
        "name": name,
        "address": host,
        "port": port,
        "method": method,
        "password": password,
        "net": "tcp",
        "security": "",
        "raw": raw,
        "extra": {}
    }


def parse_node(raw: str):
    raw = raw.strip()
    if raw.startswith("vmess://"):
        return parse_vmess(raw)
    if raw.startswith("vless://"):
        return parse_url_node(raw, "vless")
    if raw.startswith("trojan://"):
        n = parse_url_node(raw, "trojan")
        if not n.get("security"):
            n["security"] = "tls"
        return n
    if raw.startswith("ss://"):
        return parse_ss(raw)
    raise ValueError("unsupported scheme")


def _build_stream_settings(node):
    net = node.get("net") or "tcp"
    security = node.get("security") or node.get("tls") or ""
    if security == "none":
        security = ""

    stream = {"network": net}
    if security:
        stream["security"] = security

    if net == "ws":
        path = node.get("path") or "/"
        headers = {}
        host = node.get("host") or ""
        if host:
            headers["Host"] = host
        stream["wsSettings"] = {"path": path, "headers": headers}
    elif net == "grpc":
        service = node.get("serviceName") or ""
        stream["grpcSettings"] = {"serviceName": service}

    if security == "tls":
        tls = {"allowInsecure": True}
        sni = node.get("sni") or node.get("host") or ""
        if sni:
            tls["serverName"] = sni
        stream["tlsSettings"] = tls

    if security == "reality":
        pbk = node.get("pbk") or ""
        sid = node.get("sid") or ""
        sni = node.get("sni") or ""
        if not pbk:
            raise ValueError("reality requires pbk/publicKey")
        reality = {
            "show": False,
            "publicKey": pbk,
            "shortId": sid,
            "serverName": sni,
            "fingerprint": node.get("fp") or "chrome"
        }
        stream["security"] = "reality"
        stream["realitySettings"] = reality

    return stream


def build_outbound(node):
    ntype = node.get("type")
    if ntype == "vmess":
        outbound = {
            "tag": "proxy",
            "protocol": "vmess",
            "settings": {
                "vnext": [
                    {
                        "address": node.get("address"),
                        "port": int(node.get("port") or 0),
                        "users": [
                            {
                                "id": node.get("uuid"),
                                "alterId": int(node.get("aid") or 0),
                                "security": "auto"
                            }
                        ]
                    }
                ]
            },
            "streamSettings": _build_stream_settings(node)
        }
        return outbound

    if ntype == "vless":
        outbound = {
            "tag": "proxy",
            "protocol": "vless",
            "settings": {
                "vnext": [
                    {
                        "address": node.get("address"),
                        "port": int(node.get("port") or 0),
                        "users": [
                            {
                                "id": node.get("uuid"),
                                "encryption": "none",
                                "flow": node.get("flow") or ""
                            }
                        ]
                    }
                ]
            },
            "streamSettings": _build_stream_settings(node)
        }
        return outbound

    if ntype == "trojan":
        outbound = {
            "tag": "proxy",
            "protocol": "trojan",
            "settings": {
                "servers": [
                    {
                        "address": node.get("address"),
                        "port": int(node.get("port") or 0),
                        "password": node.get("password")
                    }
                ]
            },
            "streamSettings": _build_stream_settings(node)
        }
        return outbound

    if ntype == "ss":
        outbound = {
            "tag": "proxy",
            "protocol": "shadowsocks",
            "settings": {
                "servers": [
                    {
                        "address": node.get("address"),
                        "port": int(node.get("port") or 0),
                        "method": node.get("method"),
                        "password": node.get("password")
                    }
                ]
            }
        }
        return outbound

    raise ValueError("unsupported node type")


def _pick_free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _run_xray_test(outbound, test_url: str):
    if not os.path.exists(XRAY_BIN):
        return {"success": False, "error": "xray binary not found"}
    if shutil.which(CURL_BIN) is None:
        return {"success": False, "error": "curl not found"}

    socks_port = _pick_free_port()
    config = {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "port": socks_port,
                "listen": "127.0.0.1",
                "protocol": "socks",
                "settings": {"udp": True}
            }
        ],
        "outbounds": [outbound]
    }

    tmp = None
    proc = None
    try:
        tmp = tempfile.NamedTemporaryFile("w", delete=False, dir=DATA_DIR, suffix=".json")
        json.dump(config, tmp, ensure_ascii=False)
        tmp.flush()
        tmp.close()

        env = os.environ.copy()
        env["XRAY_LOCATION_ASSET"] = DATA_DIR

        proc = subprocess.Popen(
            [XRAY_BIN, "run", "-c", tmp.name],
            cwd=DATA_DIR,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True
        )
        time.sleep(0.4)
        if proc.poll() is not None:
            err = proc.stderr.read().strip() if proc.stderr else "xray exited"
            return {"success": False, "error": f"xray exited early: {err}"}

        start = time.time()
        curl = subprocess.run(
            [
                CURL_BIN,
                "-m",
                str(CONNECT_TIMEOUT),
                "-s",
                "-o",
                "/dev/null",
                "-w",
                "%{http_code}",
                "--socks5-hostname",
                f"127.0.0.1:{socks_port}",
                test_url
            ],
            capture_output=True,
            text=True,
            timeout=CONNECT_TIMEOUT + 2
        )
        latency_ms = int((time.time() - start) * 1000)
        code = curl.stdout.strip()

        if curl.returncode == 0 and code.startswith("2"):
            return {"success": True, "latency_ms": latency_ms, "http_code": code}
        error = f"curl rc={curl.returncode} http={code} stderr={curl.stderr.strip()}"
        return {"success": False, "latency_ms": latency_ms, "error": error}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        if tmp and os.path.exists(tmp.name):
            try:
                os.remove(tmp.name)
            except Exception:
                pass


def test_node_raw(raw: str, test_url: str):
    node = parse_node(raw)
    outbound = build_outbound(node)
    return _run_xray_test(outbound, test_url)


def _query_nodes(owner: str, q: str = "", ntype: str = "all", status: str = "all", limit: int | None = None, offset: int = 0):
    q = (q or "").strip()
    ntype = (ntype or "all").strip()
    status = (status or "all").strip()

    where = ["n.owner = ?"]
    params: list = [owner]

    if ntype and ntype != "all":
        where.append("n.type = ?")
        params.append(ntype)

    if q:
        where.append("n.raw LIKE ?")
        params.append(f"%{q}%")

    if status == "ok":
        where.append("s.status = ?")
        params.append("ok")
    elif status == "fail":
        where.append("s.status = ?")
        params.append("fail")
    elif status == "unknown":
        where.append("(s.status IS NULL OR s.status = '')")

    where_sql = " AND ".join(where)

    conn = _db()
    try:
        count_sql = f"""
            SELECT COUNT(*) AS c
            FROM nodes n
            LEFT JOIN node_status s ON s.node_id = n.id
            WHERE {where_sql}
        """
        cur = conn.execute(count_sql, params)
        total = cur.fetchone()["c"]

        data_sql = f"""
            SELECT n.id, n.type, n.raw, n.created_at,
                   s.status, s.latency_ms, s.error, s.checked_at
            FROM nodes n
            LEFT JOIN node_status s ON s.node_id = n.id
            WHERE {where_sql}
            ORDER BY n.created_at DESC
        """
        if limit is not None:
            data_sql += " LIMIT ? OFFSET ?"
            cur = conn.execute(data_sql, params + [limit, offset])
        else:
            cur = conn.execute(data_sql, params)
        rows = cur.fetchall()
    finally:
        conn.close()

    return rows, total


def _list_nodes(owner: str, page: int = 1, page_size: int = 20, q: str = "", ntype: str = "all", status: str = "all"):
    page = max(1, int(page))
    page_size = int(page_size)
    offset = (page - 1) * page_size
    rows, total = _query_nodes(owner=owner, q=q, ntype=ntype, status=status, limit=page_size, offset=offset)

    nodes = []
    for row in rows:
        raw = row["raw"]
        try:
            node = parse_node(raw)
        except Exception as e:
            node = {"type": row["type"], "raw": raw, "parse_error": str(e)}
        node["id"] = row["id"]
        node["status"] = row["status"] or ""
        node["latency_ms"] = row["latency_ms"]
        node["error"] = row["error"] or ""
        node["checked_at"] = row["checked_at"] or ""
        nodes.append(node)

    return nodes, total


def _find_node_by_id(owner: str, node_id: str):
    conn = _db()
    try:
        cur = conn.execute("SELECT id, type, raw FROM nodes WHERE id=? AND owner=?", (node_id, owner))
        row = cur.fetchone()
        if not row:
            return None
        return {"id": row["id"], "type": row["type"], "raw": row["raw"]}
    finally:
        conn.close()


def _upsert_node(owner: str, node_type: str, raw: str):
    node_id = _node_id(raw)
    conn = _db()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO nodes (id, owner, type, raw, created_at) VALUES (?,?,?,?,?)",
            (node_id, owner, node_type, raw, _now_str())
        )
        conn.commit()
    finally:
        conn.close()
    return node_id


def _delete_node(owner: str, node_id: str):
    conn = _db()
    try:
        conn.execute("DELETE FROM nodes WHERE id=? AND owner=?", (node_id, owner))
        conn.execute("DELETE FROM node_status WHERE node_id=? AND owner=?", (node_id, owner))
        conn.commit()
    finally:
        conn.close()


def _update_status(owner: str, node_id: str, result: dict):
    conn = _db()
    try:
        success = bool(result.get("success"))
        # read current consecutive_fail
        row = conn.execute(
            "SELECT consecutive_fail FROM node_status WHERE owner=? AND node_id=?",
            (owner, node_id),
        ).fetchone()
        current_fail = row["consecutive_fail"] if row else 0
        if success:
            new_fail = 0
        else:
            new_fail = int(current_fail) + 1

        conn.execute(
            """
            INSERT INTO node_status (node_id, owner, status, latency_ms, error, checked_at, consecutive_fail)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(owner, node_id) DO UPDATE SET
                status=excluded.status,
                latency_ms=excluded.latency_ms,
                error=excluded.error,
                checked_at=excluded.checked_at,
                consecutive_fail=excluded.consecutive_fail
            """,
            (
                node_id,
                owner,
                "ok" if success else "fail",
                result.get("latency_ms"),
                result.get("error", ""),
                _now_str(),
                new_fail,
            )
        )

        if not success and new_fail >= 3:
            blacklist_until = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                """
                UPDATE nodes
                SET disabled=1, disabled_reason='blacklist', blacklist_until=?
                WHERE owner=? AND id=?
                """,
                (blacklist_until, owner, node_id),
            )

        conn.commit()
    finally:
        conn.close()


def _import_legacy_if_empty():
    conn = _db()
    try:
        cur = conn.execute("SELECT COUNT(*) AS c FROM nodes")
        count = cur.fetchone()["c"]
    finally:
        conn.close()

    if count > 0:
        return

    _ensure_legacy_files()
    for raw in _load_vmess_lines():
        _upsert_node(ADMIN_USER, "vmess", raw)
    for entry in _load_other_nodes():
        raw = entry.get("raw", "")
        ntype = entry.get("type", "")
        if raw and ntype:
            _upsert_node(ADMIN_USER, ntype, raw)


def _create_session(username: str):
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now() + timedelta(days=SESSION_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    conn = _db()
    try:
        conn.execute(
            "INSERT INTO sessions (token, username, expires_at) VALUES (?,?,?)",
            (token, username, expires_at)
        )
        conn.commit()
    finally:
        conn.close()
    return token, expires_at


def _get_user_from_session(token: str):
    if not token:
        return None
    conn = _db()
    try:
        cur = conn.execute("SELECT username, expires_at FROM sessions WHERE token=?", (token,))
        row = cur.fetchone()
        if not row:
            return None
        expires_at = datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S")
        if expires_at < datetime.now():
            conn.execute("DELETE FROM sessions WHERE token=?", (token,))
            conn.commit()
            return None
        return row["username"]
    finally:
        conn.close()


def _delete_session(token: str):
    conn = _db()
    try:
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))
        conn.commit()
    finally:
        conn.close()


def _get_client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _get_login_record(ip: str, username: str):
    conn = _db()
    try:
        cur = conn.execute(
            "SELECT ip, username, failed_count, first_failed_at, last_failed_at, locked_until FROM login_attempts WHERE ip=? AND username=?",
            (ip, username),
        )
        return cur.fetchone()
    finally:
        conn.close()


def _clear_login_record(ip: str, username: str):
    conn = _db()
    try:
        conn.execute("DELETE FROM login_attempts WHERE ip=? AND username=?", (ip, username))
        conn.commit()
    finally:
        conn.close()


def _record_login_failure(ip: str, username: str):
    now = datetime.now()
    record = _get_login_record(ip, username)
    window_start = now - timedelta(minutes=LOGIN_WINDOW_MIN)
    locked_until = None

    if record:
        first_failed_at = datetime.strptime(record["first_failed_at"], "%Y-%m-%d %H:%M:%S")
        failed_count = record["failed_count"]
        if first_failed_at < window_start:
            failed_count = 0
            first_failed_at = now
        failed_count += 1
    else:
        failed_count = 1
        first_failed_at = now

    if failed_count >= LOGIN_MAX_ATTEMPTS:
        locked_until = now + timedelta(minutes=LOGIN_LOCK_MIN)

    conn = _db()
    try:
        conn.execute(
            """
            INSERT INTO login_attempts (ip, username, failed_count, first_failed_at, last_failed_at, locked_until)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(ip, username) DO UPDATE SET
                failed_count=excluded.failed_count,
                first_failed_at=excluded.first_failed_at,
                last_failed_at=excluded.last_failed_at,
                locked_until=excluded.locked_until
            """,
            (
                ip,
                username,
                failed_count,
                first_failed_at.strftime("%Y-%m-%d %H:%M:%S"),
                now.strftime("%Y-%m-%d %H:%M:%S"),
                locked_until.strftime("%Y-%m-%d %H:%M:%S") if locked_until else None,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return locked_until


def _test_all_worker(owner: str, nodes, test_url: str):
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = {ex.submit(test_node_raw, n["raw"], test_url): n for n in nodes}
            for fut in concurrent.futures.as_completed(futures):
                if _get_status(owner).get("cancel"):
                    break
                node = futures[fut]
                try:
                    result = fut.result()
                except Exception as e:
                    result = {"success": False, "error": str(e)}
                _update_status(owner, node["id"], result)
                with run_lock:
                    _get_status(owner)["done"] += 1
    except Exception as e:
        with run_lock:
            _get_status(owner)["last_error"] = str(e)
    finally:
        with run_lock:
            st = _get_status(owner)
            st["running"] = False
            st["finished_at"] = _now_str()


def start_test_all(owner: str):
    with run_lock:
        st = _get_status(owner)
        if st["running"]:
            return False
        nodes, _ = _list_nodes(owner=owner, page=1, page_size=1000000)
        st["running"] = True
        st["total"] = len(nodes)
        st["done"] = 0
        st["started_at"] = _now_str()
        st["finished_at"] = None
        st["last_error"] = ""
        st["cancel"] = False

    test_url = _get_setting("test_url", TEST_URL_DEFAULT)
    t = threading.Thread(target=_test_all_worker, args=(owner, nodes, test_url), daemon=True)
    t.start()
    return True


def start_test_filtered(owner: str, q: str = "", ntype: str = "all", status: str = "all"):
    with run_lock:
        st = _get_status(owner)
        if st["running"]:
            return False
        rows, total = _query_nodes(owner=owner, q=q, ntype=ntype, status=status, limit=None, offset=0)
        nodes = [{"id": r["id"], "raw": r["raw"]} for r in rows]
        st["running"] = True
        st["total"] = total
        st["done"] = 0
        st["started_at"] = _now_str()
        st["finished_at"] = None
        st["last_error"] = ""
        st["cancel"] = False

    test_url = _get_setting("test_url", TEST_URL_DEFAULT)
    t = threading.Thread(target=_test_all_worker, args=(owner, nodes, test_url), daemon=True)
    t.start()
    return True


def _auto_check_loop():
    while True:
        time.sleep(30)
        conn = _db()
        try:
            cur = conn.execute(
                "SELECT username, auto_check_enabled, interval_min, next_run_at FROM user_settings WHERE auto_check_enabled=1"
            )
            rows = cur.fetchall()
        finally:
            conn.close()

        now = datetime.now()
        for r in rows:
            username = r["username"]
            interval_min = int(r["interval_min"])
            next_run_at = r["next_run_at"]

            if _get_status(username)["running"]:
                continue

            if next_run_at:
                try:
                    next_run = datetime.strptime(next_run_at, "%Y-%m-%d %H:%M:%S")
                    if now < next_run:
                        continue
                except Exception:
                    pass

            started = start_test_all(username)
            if started:
                new_next = (now + timedelta(minutes=interval_min)).strftime("%Y-%m-%d %H:%M:%S")
                _set_user_settings(username, 1, interval_min, new_next)


@app.on_event("startup")
def on_startup():
    _init_db()
    _ensure_admin()
    _import_legacy_if_empty()
    _get_setting("test_url", TEST_URL_DEFAULT)
    _ensure_user_settings(ADMIN_USER)
    t = threading.Thread(target=_auto_check_loop, daemon=True)
    t.start()
    s = threading.Thread(target=_subscription_fetch_loop, daemon=True)
    s.start()


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith("/static") or path.startswith("/sub/") or path in ("/login", "/api/login"):
        return await call_next(request)

    token = request.cookies.get("xray_session")
    user = _get_user_from_session(token)
    if not user:
        if path.startswith("/api"):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return RedirectResponse("/login")

    request.state.user = user
    request.state.role = _get_user_role(user)
    return await call_next(request)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": ""})


@app.get("/password", response_class=HTMLResponse)
def password_page(request: Request):
    return templates.TemplateResponse("password.html", {"request": request})


@app.post("/api/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    ip = _get_client_ip(request)
    record = _get_login_record(ip, username)
    if record and record["locked_until"]:
        locked_until = datetime.strptime(record["locked_until"], "%Y-%m-%d %H:%M:%S")
        if locked_until > datetime.now():
            minutes = max(1, int((locked_until - datetime.now()).total_seconds() // 60) + 1)
            return templates.TemplateResponse(
                "login.html",
                {"request": request, "error": f"登录过于频繁，请 {minutes} 分钟后再试"},
                status_code=429,
            )

    conn = _db()
    try:
        cur = conn.execute("SELECT password_hash FROM users WHERE username=?", (username,))
        row = cur.fetchone()
        if not row:
            _record_login_failure(ip, username)
            return templates.TemplateResponse(
                "login.html",
                {"request": request, "error": "用户名或密码错误"},
                status_code=401,
            )
        if _hash_password(password) != row["password_hash"]:
            _record_login_failure(ip, username)
            return templates.TemplateResponse(
                "login.html",
                {"request": request, "error": "用户名或密码错误"},
                status_code=401,
            )
    finally:
        conn.close()

    _ensure_user_settings(username)
    _clear_login_record(ip, username)
    token, _ = _create_session(username)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie("xray_session", token, httponly=True)
    return resp


@app.post("/api/logout")
def logout(request: Request):
    token = request.cookies.get("xray_session")
    if token:
        _delete_session(token)
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("xray_session")
    return resp


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    page: int = 1,
    page_size: int = 20,
    q: str = "",
    type: str = "all",
    status: str = "all",
):
    username = request.state.user
    if page_size not in (20, 50, 100):
        page_size = 20
    nodes, total = _list_nodes(owner=username, page=page, page_size=page_size, q=q, ntype=type, status=status)
    test_url = _get_setting("test_url", TEST_URL_DEFAULT)
    settings = _get_user_settings(username)
    st = _get_status(username)
    interval_min = int(settings["interval_min"])
    if interval_min % 10080 == 0:
        interval_unit = "week"
        interval_value = interval_min // 10080
    elif interval_min % 1440 == 0:
        interval_unit = "day"
        interval_value = interval_min // 1440
    elif interval_min % 60 == 0:
        interval_unit = "hour"
        interval_value = interval_min // 60
    else:
        interval_unit = "minute"
        interval_value = interval_min
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))

    qs_base = urllib.parse.urlencode(
        {
            "q": q or "",
            "type": type or "all",
            "status": status or "all",
            "page_size": page_size,
        }
    )
    if qs_base:
        qs_base = qs_base + "&"

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "nodes": nodes,
            "status": st,
            "test_url": test_url,
            "username": username,
            "role": request.state.role,
            "auto_check_enabled": settings["auto_check_enabled"],
            "auto_check_interval": settings["interval_min"],
            "auto_interval_value": interval_value,
            "auto_interval_unit": interval_unit,
            "next_run_at": settings["next_run_at"],
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
            "q": q,
            "type_filter": type,
            "status_filter": status,
            "qs_base": qs_base,
        }
    )


@app.post("/api/nodes")
def add_node(request: Request, node_type: str = Form(...), raw: str = Form(...)):
    raw = raw.strip()
    if not raw:
        raise HTTPException(status_code=400, detail="raw is empty")

    try:
        parsed = parse_node(raw)
        node_type = parsed.get("type", node_type)
    except Exception:
        pass

    if node_type == "vmess" and not raw.startswith("vmess://"):
        raise HTTPException(status_code=400, detail="invalid vmess link")

    _upsert_node(request.state.user, node_type, raw)
    return {"success": True}


@app.put("/api/nodes/{node_id}")
def update_node(request: Request, node_id: str, node_type: str = Form(...), raw: str = Form(...)):
    raw = raw.strip()
    if not raw:
        raise HTTPException(status_code=400, detail="raw is empty")

    if not _find_node_by_id(request.state.user, node_id):
        raise HTTPException(status_code=404, detail="node not found")

    _delete_node(request.state.user, node_id)

    try:
        parsed = parse_node(raw)
        node_type = parsed.get("type", node_type)
    except Exception:
        pass

    if node_type == "vmess" and not raw.startswith("vmess://"):
        raise HTTPException(status_code=400, detail="invalid vmess link")

    _upsert_node(request.state.user, node_type, raw)
    return {"success": True}


@app.delete("/api/nodes/{node_id}")
def delete_node(request: Request, node_id: str):
    if not _find_node_by_id(request.state.user, node_id):
        raise HTTPException(status_code=404, detail="node not found")
    _delete_node(request.state.user, node_id)
    return {"success": True}


@app.post("/api/nodes/{node_id}/test")
def test_node(request: Request, node_id: str):
    item = _find_node_by_id(request.state.user, node_id)
    if not item:
        raise HTTPException(status_code=404, detail="node not found")

    try:
        test_url = _get_setting("test_url", TEST_URL_DEFAULT)
        result = test_node_raw(item["raw"], test_url)
    except Exception as e:
        result = {"success": False, "error": str(e)}

    _update_status(request.state.user, node_id, result)
    return {"success": True, "result": result}


@app.post("/api/test-all")
def test_all(request: Request):
    started = start_test_all(request.state.user)
    return {"success": True, "started": started, "status": _get_status(request.state.user)}


@app.get("/api/status")
def status(request: Request):
    return JSONResponse(_get_status(request.state.user))


@app.get("/api/nodes/status")
def nodes_status(request: Request, ids: str | None = None):
    conn = _db()
    try:
        if ids:
            id_list = [i for i in ids.split(",") if i]
            if not id_list:
                return JSONResponse({})
            placeholders = ",".join(["?"] * len(id_list))
            cur = conn.execute(
                f"SELECT node_id, status, latency_ms, error, checked_at FROM node_status WHERE owner=? AND node_id IN ({placeholders})",
                [request.state.user] + id_list,
            )
        else:
            cur = conn.execute(
                "SELECT node_id, status, latency_ms, error, checked_at FROM node_status WHERE owner=?",
                (request.state.user,),
            )
        rows = cur.fetchall()
    finally:
        conn.close()

    data = {}
    for r in rows:
        data[r["node_id"]] = {
            "status": r["status"],
            "latency_ms": r["latency_ms"],
            "error": r["error"],
            "checked_at": r["checked_at"],
        }
    return JSONResponse(data)


@app.delete("/api/nodes")
def delete_all_nodes(request: Request):
    conn = _db()
    try:
        conn.execute("DELETE FROM nodes WHERE owner=?", (request.state.user,))
        conn.execute("DELETE FROM node_status WHERE owner=?", (request.state.user,))
        conn.commit()
    finally:
        conn.close()
    return {"success": True}


@app.post("/api/test-filtered")
def test_filtered(request: Request, q: str = Form(""), type: str = Form("all"), status: str = Form("all")):
    started = start_test_filtered(request.state.user, q=q, ntype=type, status=status)
    return {"success": True, "started": started, "status": _get_status(request.state.user)}


@app.post("/api/test-stop")
def test_stop(request: Request):
    st = _get_status(request.state.user)
    st["cancel"] = True
    st["running"] = False
    st["finished_at"] = _now_str()
    return {"success": True}


@app.delete("/api/nodes/filtered")
def delete_filtered_nodes(request: Request, q: str = "", type: str = "all", status: str = "all"):
    rows, _ = _query_nodes(owner=request.state.user, q=q, ntype=type, status=status, limit=None, offset=0)
    ids = [r["id"] for r in rows]
    if not ids:
        return {"success": True, "deleted": 0}
    conn = _db()
    try:
        placeholders = ",".join(["?"] * len(ids))
        conn.execute(
            f"DELETE FROM nodes WHERE owner=? AND id IN ({placeholders})",
            [request.state.user] + ids,
        )
        conn.execute(
            f"DELETE FROM node_status WHERE owner=? AND node_id IN ({placeholders})",
            [request.state.user] + ids,
        )
        conn.commit()
    finally:
        conn.close()
    return {"success": True, "deleted": len(ids)}


@app.post("/api/import")
def import_nodes(request: Request, raw: str = Form(...)):
    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    total = len(lines)
    added = 0
    skipped = 0
    errors = 0

    for line in lines:
        try:
            node = parse_node(line)
            node_id = _node_id(line)
            if _find_node_by_id(request.state.user, node_id):
                skipped += 1
                continue
            _upsert_node(request.state.user, node.get("type", "other"), line)
            added += 1
        except Exception:
            errors += 1

    return {"success": True, "total": total, "added": added, "skipped": skipped, "errors": errors}


@app.post("/api/import/subscription")
def import_subscription(request: Request, urls: str = Form(...)):
    url_list = [u.strip() for u in urls.splitlines() if u.strip()]
    if not url_list:
        raise HTTPException(status_code=400, detail="订阅地址为空")

    total_urls = len(url_list)
    total_links = 0
    added = 0
    skipped = 0
    errors = 0
    failed_urls = []

    for url in url_list:
        try:
            content = _fetch_subscription(url)
            links = _parse_subscription_content(content)
            total_links += len(links)
            for link in links:
                try:
                    node = parse_node(link)
                    node_id = _node_id(link)
                    if _find_node_by_id(request.state.user, node_id):
                        skipped += 1
                        continue
                    _upsert_node(request.state.user, node.get("type", "other"), link)
                    added += 1
                except Exception:
                    errors += 1
        except Exception:
            failed_urls.append(url)

    return {
        "success": True,
        "total_urls": total_urls,
        "total_links": total_links,
        "added": added,
        "skipped": skipped,
        "errors": errors,
        "failed_urls": failed_urls,
    }


@app.get("/api/export-rules")
def list_export_rules(request: Request):
    conn = _db()
    try:
        rows = conn.execute(
            """
            SELECT id, name, token, format, enabled, rules_json, created_at
            FROM export_rules
            WHERE owner=?
            ORDER BY created_at DESC
            """,
            (request.state.user,),
        ).fetchall()
    finally:
        conn.close()
    return {"success": True, "items": [dict(r) for r in rows]}


@app.post("/api/export-rules")
def create_export_rule(
    request: Request,
    name: str = Form(...),
    format: str = Form("clash"),
    enabled: int = Form(1),
    rules_json: str = Form("[]"),
):
    fmt = (format or "clash").strip().lower()
    if fmt not in ("clash", "raw", "base64", "singbox"):
        raise HTTPException(status_code=400, detail="invalid format")
    # Validate JSON shape (list of objects)
    rules = _parse_rules_json(rules_json)
    rid = uuid.uuid4().hex
    token = secrets.token_urlsafe(24)
    conn = _db()
    try:
        conn.execute(
            """
            INSERT INTO export_rules (id, owner, name, token, format, enabled, rules_json, created_at)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (rid, request.state.user, name.strip(), token, fmt, 1 if int(enabled) == 1 else 0, json.dumps(rules, ensure_ascii=False), _now_str()),
        )
        conn.commit()
    finally:
        conn.close()
    return {"success": True, "id": rid, "token": token}


@app.put("/api/export-rules/{rule_id}")
def update_export_rule(
    request: Request,
    rule_id: str,
    name: str = Form(""),
    format: str = Form(""),
    enabled: int | None = Form(None),
    rules_json: str | None = Form(None),
):
    updates = []
    params = []
    if name.strip():
        updates.append("name=?")
        params.append(name.strip())
    if format.strip():
        fmt = format.strip().lower()
        if fmt not in ("clash", "raw", "base64", "singbox"):
            raise HTTPException(status_code=400, detail="invalid format")
        updates.append("format=?")
        params.append(fmt)
    if enabled is not None:
        updates.append("enabled=?")
        params.append(1 if int(enabled) == 1 else 0)
    if rules_json is not None:
        rules = _parse_rules_json(rules_json)
        updates.append("rules_json=?")
        params.append(json.dumps(rules, ensure_ascii=False))

    if not updates:
        return {"success": True}

    params.extend([request.state.user, rule_id])
    conn = _db()
    try:
        conn.execute(
            f"UPDATE export_rules SET {', '.join(updates)} WHERE owner=? AND id=?",
            params,
        )
        conn.commit()
    finally:
        conn.close()
    return {"success": True}


@app.delete("/api/export-rules/{rule_id}")
def delete_export_rule(request: Request, rule_id: str):
    conn = _db()
    try:
        conn.execute(
            "DELETE FROM export_rules WHERE owner=? AND id=?",
            (request.state.user, rule_id),
        )
        conn.commit()
    finally:
        conn.close()
    return {"success": True}


@app.get("/sub/{token}")
def public_subscription(token: str, format: str | None = None):
    conn = _db()
    try:
        rule = conn.execute(
            """
            SELECT owner, format, enabled, rules_json
            FROM export_rules
            WHERE token=?
            LIMIT 1
            """,
            (token,),
        ).fetchone()
    finally:
        conn.close()

    if not rule or int(rule["enabled"] or 0) != 1:
        raise HTTPException(status_code=404, detail="subscription not found")

    fmt = (format or rule["format"] or "clash").strip().lower()
    if fmt not in ("clash", "raw", "base64", "singbox"):
        raise HTTPException(status_code=400, detail="invalid format")

    rules = _parse_rules_json(rule["rules_json"] or "[]")
    links = _collect_export_links(rule["owner"], rules)

    if fmt == "clash":
        return PlainTextResponse(_render_clash(links), media_type="text/yaml; charset=utf-8")
    if fmt == "raw":
        return PlainTextResponse(_render_raw(links), media_type="text/plain; charset=utf-8")
    if fmt == "base64":
        return PlainTextResponse(_render_base64(links), media_type="text/plain; charset=utf-8")
    return PlainTextResponse(_render_singbox(links), media_type="application/json; charset=utf-8")


def _parse_rules_json(raw: str) -> list[dict]:
    try:
        data = json.loads(raw or "[]")
    except Exception:
        return []
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    return []


def _to_float(val, default=0.0):
    try:
        return float(val)
    except Exception:
        return default


def _compare(left: float, op: str, right: float) -> bool:
    if op == ">":
        return left > right
    if op == ">=":
        return left >= right
    if op == "<":
        return left < right
    if op == "<=":
        return left <= right
    if op == "==":
        return left == right
    return False


def _minutes_from_now(ts: str) -> float | None:
    if not ts:
        return None
    try:
        dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None
    return (datetime.now() - dt).total_seconds() / 60.0


def _apply_export_rules(node: dict, rules: list[dict]) -> bool:
    # OR semantics: any rule passes means include.
    if not rules:
        return True
    for rule in rules:
        rtype = str(rule.get("type", "")).strip().lower()
        op = str(rule.get("op", ">=")).strip()
        value = _to_float(rule.get("value", 0), 0.0)

        if rtype in ("success_rate", "rate"):
            left = _to_float(node.get("success_rate", 0), 0.0)
            if _compare(left, op, value):
                return True
            continue

        if rtype in ("latency", "avg_latency"):
            left = _to_float(node.get("avg_latency", 999999), 999999)
            if _compare(left, op, value):
                return True
            continue

        if rtype in ("recent_successes", "recent_success"):
            left = _to_float(node.get("recent_successes", 0), 0.0)
            if _compare(left, op, value):
                return True
            continue

        if rtype in ("success_in_last_minutes", "recent_ok_minutes"):
            # Rule means: at least one success happened within N minutes.
            if node.get("status") == "ok":
                minutes = _minutes_from_now(node.get("checked_at", "")) or 999999
                if minutes <= value:
                    return True
            continue

    return False


def _collect_export_links(owner: str, rules: list[dict]) -> list[str]:
    conn = _db()
    try:
        rows = conn.execute(
            """
            SELECT n.raw, n.type, n.id, n.disabled, n.disabled_reason,
                   s.status, s.latency_ms, s.checked_at
            FROM nodes n
            LEFT JOIN node_status s ON s.owner=n.owner AND s.node_id=n.id
            WHERE n.owner=?
            ORDER BY n.created_at DESC
            """,
            (owner,),
        ).fetchall()
    finally:
        conn.close()

    links = []
    for r in rows:
        if int(r["disabled"] or 0) == 1:
            continue
        node = {
            "status": r["status"] or "",
            "checked_at": r["checked_at"] or "",
            "avg_latency": r["latency_ms"] if r["latency_ms"] is not None else 999999,
            "success_rate": 100 if (r["status"] == "ok") else 0,
            "recent_successes": 1 if (r["status"] == "ok") else 0,
        }
        if _apply_export_rules(node, rules):
            links.append(r["raw"])
    return links


def _render_raw(links: list[str]) -> str:
    return "\n".join(links) + ("\n" if links else "")


def _render_base64(links: list[str]) -> str:
    return _std_b64encode(_render_raw(links))


def _render_clash(links: list[str]) -> str:
    proxies = []
    idx = 0
    for raw in links:
        try:
            n = parse_node(raw)
        except Exception:
            continue
        idx += 1
        name = n.get("name") or f"node-{idx}"
        ptype = n.get("type")
        if ptype == "ss":
            proxies.append(
                {
                    "name": name,
                    "type": "ss",
                    "server": n.get("address"),
                    "port": int(n.get("port") or 0),
                    "cipher": n.get("method") or "aes-128-gcm",
                    "password": n.get("password") or "",
                }
            )
        elif ptype == "trojan":
            proxies.append(
                {
                    "name": name,
                    "type": "trojan",
                    "server": n.get("address"),
                    "port": int(n.get("port") or 0),
                    "password": n.get("password") or "",
                    "sni": n.get("sni") or "",
                }
            )
        elif ptype in ("vmess", "vless"):
            item = {
                "name": name,
                "type": ptype,
                "server": n.get("address"),
                "port": int(n.get("port") or 0),
                "uuid": n.get("uuid") or "",
                "network": n.get("net") or "tcp",
                "tls": True if (n.get("tls") == "tls" or n.get("security") == "tls") else False,
            }
            if ptype == "vmess":
                item["alterId"] = int(n.get("aid") or 0)
            proxies.append(item)

    try:
        import yaml  # type: ignore

        names = [p["name"] for p in proxies]
        data = {
            "proxies": proxies,
            "proxy-groups": [{"name": "AUTO", "type": "select", "proxies": names}],
            "rules": ["MATCH,AUTO"],
        }
        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    except Exception:
        # fallback raw format if YAML unavailable
        return _render_raw(links)


def _render_singbox(links: list[str]) -> str:
    # Minimal sing-box-compatible stub payload with raw links attached.
    data = {"version": 1, "links": links}
    return json.dumps(data, ensure_ascii=False)


@app.get("/api/export/txt")
def export_txt(request: Request):
    conn = _db()
    try:
        cur = conn.execute("SELECT raw FROM nodes WHERE owner=? ORDER BY created_at DESC", (request.state.user,))
        lines = [row["raw"] for row in cur.fetchall()]
    finally:
        conn.close()

    content = "\n".join(lines) + ("\n" if lines else "")
    headers = {"Content-Disposition": "attachment; filename=nodes.txt"}
    return PlainTextResponse(content, headers=headers)


@app.get("/api/export/json")
def export_json(request: Request):
    conn = _db()
    try:
        cur = conn.execute(
            "SELECT id, type, raw, created_at FROM nodes WHERE owner=? ORDER BY created_at DESC",
            (request.state.user,),
        )
        rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

    headers = {"Content-Disposition": "attachment; filename=nodes.json"}
    return JSONResponse(rows, headers=headers)


@app.get("/api/settings")
def get_settings():
    test_url = _get_setting("test_url", TEST_URL_DEFAULT)
    return {"test_url": test_url}


@app.post("/api/settings/test-url")
def set_test_url(test_url: str = Form(...)):
    test_url = test_url.strip()
    if not test_url.startswith("http://") and not test_url.startswith("https://"):
        raise HTTPException(status_code=400, detail="invalid url")
    _set_setting("test_url", test_url)
    return {"success": True, "test_url": test_url}


def _require_admin(request: Request):
    if request.state.role != "admin":
        raise HTTPException(status_code=403, detail="forbidden")


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    _require_admin(request)
    conn = _db()
    try:
        users = [dict(r) for r in conn.execute("SELECT username, role, created_at FROM users ORDER BY created_at DESC").fetchall()]
        counts = {r["owner"]: r["cnt"] for r in conn.execute("SELECT owner, COUNT(*) AS cnt FROM nodes GROUP BY owner").fetchall()}
        sessions = {r["username"]: r["cnt"] for r in conn.execute("SELECT username, COUNT(*) AS cnt FROM sessions GROUP BY username").fetchall()}
        total_nodes = conn.execute("SELECT COUNT(*) AS c FROM nodes").fetchone()["c"]
        total_users = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    finally:
        conn.close()

    for u in users:
        u["node_count"] = counts.get(u["username"], 0)
        u["session_count"] = sessions.get(u["username"], 0)

    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "users": users,
            "total_nodes": total_nodes,
            "total_users": total_users,
            "login_limit": LOGIN_MAX_ATTEMPTS,
            "login_window": LOGIN_WINDOW_MIN,
            "login_lock": LOGIN_LOCK_MIN,
            "auto_interval": AUTO_CHECK_DEFAULT_INTERVAL,
        },
    )


@app.post("/api/admin/users")
def admin_create_user(request: Request, username: str = Form(...), password: str = Form(...), role: str = Form("user")):
    _require_admin(request)
    username = username.strip()
    if not username or len(password) < 6:
        raise HTTPException(status_code=400, detail="用户名或密码不合法")
    role = "admin" if role == "admin" else "user"
    conn = _db()
    try:
        cur = conn.execute("SELECT username FROM users WHERE username=?", (username,))
        if cur.fetchone():
            raise HTTPException(status_code=400, detail="用户已存在")
        conn.execute(
            "INSERT INTO users (username, password_hash, role, created_at) VALUES (?,?,?,?)",
            (username, _hash_password(password), role, _now_str()),
        )
        conn.commit()
    finally:
        conn.close()

    _ensure_user_settings(username)
    return {"success": True}


@app.post("/api/admin/users/{username}/password")
def admin_reset_password(request: Request, username: str, new_password: str = Form(...)):
    _require_admin(request)
    if len(new_password) < 6:
        raise HTTPException(status_code=400, detail="密码长度至少 6 位")
    conn = _db()
    try:
        conn.execute("UPDATE users SET password_hash=? WHERE username=?", (_hash_password(new_password), username))
        conn.execute("DELETE FROM sessions WHERE username=?", (username,))
        conn.commit()
    finally:
        conn.close()
    return {"success": True}


@app.post("/api/admin/users/{username}/role")
def admin_update_role(request: Request, username: str, role: str = Form(...)):
    _require_admin(request)
    role = "admin" if role == "admin" else "user"
    conn = _db()
    try:
        conn.execute("UPDATE users SET role=? WHERE username=?", (role, username))
        conn.commit()
    finally:
        conn.close()
    return {"success": True}


@app.post("/api/admin/users/{username}/logout")
def admin_logout_user(request: Request, username: str):
    _require_admin(request)
    conn = _db()
    try:
        conn.execute("DELETE FROM sessions WHERE username=?", (username,))
        conn.commit()
    finally:
        conn.close()
    return {"success": True}


@app.post("/api/admin/users/{username}/delete")
def admin_delete_user(request: Request, username: str):
    _require_admin(request)
    if username == request.state.user:
        raise HTTPException(status_code=400, detail="不能删除当前登录用户")
    conn = _db()
    try:
        conn.execute("DELETE FROM users WHERE username=?", (username,))
        conn.execute("DELETE FROM sessions WHERE username=?", (username,))
        conn.execute("DELETE FROM nodes WHERE owner=?", (username,))
        conn.execute("DELETE FROM node_status WHERE owner=?", (username,))
        conn.execute("DELETE FROM user_settings WHERE username=?", (username,))
        conn.execute("DELETE FROM login_attempts WHERE username=?", (username,))
        conn.commit()
    finally:
        conn.close()
    return {"success": True}


@app.post("/api/settings/auto-check")
def set_auto_check(request: Request, enabled: int = Form(...), interval_min: int = Form(...)):
    enabled = 1 if int(enabled) == 1 else 0
    interval_min = int(interval_min)
    if interval_min < 1:
        interval_min = 1
    # allow larger than 7 days; cap at 365 days to avoid extreme values
    if interval_min > 525600:
        interval_min = 525600
    next_run = None
    if enabled:
        next_run = (datetime.now() + timedelta(minutes=interval_min)).strftime("%Y-%m-%d %H:%M:%S")
    _set_user_settings(request.state.user, enabled, interval_min, next_run)
    return {"success": True, "next_run_at": next_run}


@app.post("/api/user/password")
def change_password(
    request: Request,
    old_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    if new_password != confirm_password:
        raise HTTPException(status_code=400, detail="两次密码不一致")
    if len(new_password) < 6:
        raise HTTPException(status_code=400, detail="密码长度至少 6 位")

    username = request.state.user
    conn = _db()
    try:
        cur = conn.execute("SELECT password_hash FROM users WHERE username=?", (username,))
        row = cur.fetchone()
        if not row or _hash_password(old_password) != row["password_hash"]:
            raise HTTPException(status_code=401, detail="原密码错误")
        conn.execute(
            "UPDATE users SET password_hash=? WHERE username=?",
            (_hash_password(new_password), username),
        )
        conn.execute("DELETE FROM sessions WHERE username=?", (username,))
        conn.commit()
    finally:
        conn.close()

    return {"success": True, "relogin": True}
