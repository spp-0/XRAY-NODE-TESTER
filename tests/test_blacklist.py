import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main


def test_blacklist_after_three_fails(tmp_path):
    main.DATA_DIR = str(tmp_path)
    main.DB_PATH = str(tmp_path / "data.db")
    main._init_db()

    owner = "u1"
    node_id = "n1"
    # ensure node exists
    conn = sqlite3.connect(main.DB_PATH)
    conn.execute(
        "INSERT INTO nodes (id, owner, type, raw, created_at) VALUES (?,?,?,?,?)",
        (node_id, owner, "vmess", "vmess://x", "2026-03-24 00:00:00"),
    )
    conn.commit()
    conn.close()

    for _ in range(3):
        main._update_status(owner, node_id, {"success": False, "error": "x"})

    conn = sqlite3.connect(main.DB_PATH)
    row = conn.execute(
        "SELECT disabled, disabled_reason, blacklist_until FROM nodes WHERE owner=? AND id=?",
        (owner, node_id),
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == 1
    assert row[1] == "blacklist"
    assert row[2] is not None
