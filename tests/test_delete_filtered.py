import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main
from fastapi.testclient import TestClient


def test_delete_filtered_unknown_nodes_route(tmp_path):
    main.DATA_DIR = str(tmp_path)
    main.DB_PATH = str(tmp_path / "data.db")
    main._init_db()
    main._ensure_admin()

    conn = sqlite3.connect(main.DB_PATH)
    conn.execute(
        "INSERT INTO nodes (id, owner, type, raw, created_at) VALUES (?,?,?,?,?)",
        ("n1", main.ADMIN_USER, "vmess", "vmess://x", "2026-03-24 00:00:00"),
    )
    conn.commit()
    conn.close()

    token, _ = main._create_session(main.ADMIN_USER)
    client = TestClient(main.app)
    client.cookies.set("xray_session", token)

    res = client.delete("/api/nodes/filtered", params={"status": "unknown", "type": "all", "q": ""})
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["deleted"] == 1

    conn = sqlite3.connect(main.DB_PATH)
    c = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    conn.close()
    assert c == 0
