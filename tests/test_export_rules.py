import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main


def test_or_rules_pass_any():
    node = {"success_rate": 90, "avg_latency": 900, "recent_successes": 1}
    rules = [
        {"type": "success_rate", "op": ">=", "value": 80},
        {"type": "latency", "op": "<=", "value": 800},
    ]
    assert main._apply_export_rules(node, rules) is True


def test_or_rules_false_when_none_match():
    node = {"success_rate": 50, "avg_latency": 900, "recent_successes": 0}
    rules = [
        {"type": "success_rate", "op": ">=", "value": 80},
        {"type": "latency", "op": "<=", "value": 800},
    ]
    assert main._apply_export_rules(node, rules) is False


def test_collect_export_links_only_enabled_nodes(tmp_path):
    main.DATA_DIR = str(tmp_path)
    main.DB_PATH = str(tmp_path / "data.db")
    main._init_db()

    conn = sqlite3.connect(main.DB_PATH)
    conn.execute(
        "INSERT INTO nodes (id, owner, type, raw, created_at, disabled, disabled_reason, blacklist_until) VALUES (?,?,?,?,?,?,?,?)",
        ("n1", "u1", "vmess", "vmess://a", "2026-03-24 00:00:00", 0, None, None),
    )
    conn.execute(
        "INSERT INTO nodes (id, owner, type, raw, created_at, disabled, disabled_reason, blacklist_until) VALUES (?,?,?,?,?,?,?,?)",
        ("n2", "u1", "vmess", "vmess://b", "2026-03-24 00:00:00", 1, "blacklist", "2026-03-30 00:00:00"),
    )
    conn.execute(
        "INSERT INTO node_status (node_id, owner, status, latency_ms, error, checked_at, consecutive_fail) VALUES (?,?,?,?,?,?,?)",
        ("n1", "u1", "ok", 123, "", "2026-03-24 00:00:00", 0),
    )
    conn.execute(
        "INSERT INTO node_status (node_id, owner, status, latency_ms, error, checked_at, consecutive_fail) VALUES (?,?,?,?,?,?,?)",
        ("n2", "u1", "ok", 100, "", "2026-03-24 00:00:00", 0),
    )
    conn.commit()
    conn.close()

    links = main._collect_export_links("u1", [])
    assert links == ["vmess://a"]
