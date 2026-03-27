import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main


def _setup_db(tmp_path):
    main.DATA_DIR = str(tmp_path)
    main.DB_PATH = str(tmp_path / "data.db")
    main._init_db()


def test_init_db_creates_ip_columns(tmp_path):
    _setup_db(tmp_path)
    conn = sqlite3.connect(main.DB_PATH)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(node_status)").fetchall()]
    conn.close()

    assert "exit_ip" in cols
    assert "ip_country" in cols
    assert "ip_org" in cols
    assert "ip_risk" in cols


def test_parse_ipcheck_payload_extracts_fields():
    payload = {
        "ip": "1.2.3.4",
        "city": "Hong Kong",
        "region": "Hong Kong",
        "country_name": "China",
        "country_code": "CN",
        "asn": "AS4134",
        "org": "CHINANET-BACKBONE",
        "proxyDetect": {
            "risk": 26,
            "proxy": False,
            "type": "normal",
        },
    }

    info = main._parse_ipcheck_payload(payload)
    assert info["exit_ip"] == "1.2.3.4"
    assert info["ip_country"] == "CN"
    assert info["ip_org"] == "CHINANET-BACKBONE"
    assert info["ip_risk"] == 26
    assert info["ip_type"] == "normal"


def test_update_status_persists_ip_info(tmp_path):
    _setup_db(tmp_path)
    owner = "u1"
    node_id = "n1"

    conn = sqlite3.connect(main.DB_PATH)
    conn.execute(
        "INSERT INTO nodes (id, owner, type, raw, created_at) VALUES (?,?,?,?,?)",
        (node_id, owner, "vmess", "vmess://x", "2026-03-24 00:00:00"),
    )
    conn.commit()
    conn.close()

    main._update_status(
        owner,
        node_id,
        {
            "success": True,
            "latency_ms": 123,
            "ip_info": {
                "exit_ip": "1.2.3.4",
                "ip_country": "CN",
                "ip_region": "Hong Kong",
                "ip_city": "Hong Kong",
                "ip_asn": "AS4134",
                "ip_org": "CHINANET-BACKBONE",
                "ip_risk": 26,
                "ip_type": "normal",
                "ip_proxy": "false",
            },
        },
    )

    conn = sqlite3.connect(main.DB_PATH)
    row = conn.execute(
        """
        SELECT exit_ip, ip_country, ip_org, ip_risk, ip_type, ip_proxy
        FROM node_status WHERE owner=? AND node_id=?
        """,
        (owner, node_id),
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == "1.2.3.4"
    assert row[1] == "CN"
    assert row[2] == "CHINANET-BACKBONE"
    assert row[3] == 26
    assert row[4] == "normal"
    assert row[5] == "false"

def test_parse_ipapi_payload_extracts_fields():
    payload = {
        "status": "success",
        "query": "38.179.68.27",
        "countryCode": "US",
        "regionName": "California",
        "city": "Los Angeles",
        "as": "AS152179 GLOBAL COMMUNICATION NETWORK LIMITED",
        "isp": "Global Communication Network Limited",
        "proxy": False,
        "hosting": False,
        "mobile": False,
    }

    info = main._parse_ipapi_payload(payload)
    assert info["exit_ip"] == "38.179.68.27"
    assert info["ip_country"] == "US"
    assert info["ip_region"] == "California"
    assert info["ip_city"] == "Los Angeles"
    assert info["ip_org"] == "Global Communication Network Limited"
    assert info["ip_asn"].startswith("AS152179")
    assert info["ip_proxy"] == "false"


def test_merge_ip_info_prefers_primary_and_fills_missing():
    primary = {"exit_ip": "1.1.1.1", "ip_country": "", "ip_org": "", "ip_risk": None}
    fallback = {"exit_ip": "1.1.1.1", "ip_country": "US", "ip_org": "A", "ip_risk": None}
    merged = main._merge_ip_info(primary, fallback)
    assert merged["exit_ip"] == "1.1.1.1"
    assert merged["ip_country"] == "US"
    assert merged["ip_org"] == "A"

def test_fetch_ipapi_info_tries_multiple_base_urls(monkeypatch):
    calls = []

    class DummyResp:
        def __init__(self, body: str):
            self._body = body.encode('utf-8')

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return self._body

    def fake_urlopen(req, timeout=0):
        url = req.full_url
        calls.append(url)
        if 'bad.example' in url:
            raise RuntimeError('bad upstream')
        return DummyResp('{"status":"success","query":"8.8.8.8","countryCode":"US","isp":"Google LLC"}')

    monkeypatch.setattr(main.urllib.request, 'urlopen', fake_urlopen)
    monkeypatch.setattr(main, 'IP_FALLBACK_APIS', ['http://bad.example/json', 'http://ok.example/json'])

    info = main._fetch_ipapi_info('8.8.8.8')
    assert info['exit_ip'] == '8.8.8.8'
    assert info['ip_country'] == 'US'
    assert info['ip_org'] == 'Google LLC'
    assert len(calls) == 2
