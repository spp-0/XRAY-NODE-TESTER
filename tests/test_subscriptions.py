import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main


def test_init_db_creates_subscription_tables(tmp_path):
    main.DATA_DIR = str(tmp_path)
    main.DB_PATH = str(tmp_path / "data.db")
    main._init_db()
    conn = sqlite3.connect(tmp_path / "data.db")
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "subscriptions" in tables
    assert "export_rules" in tables
