"""DB compatibility helpers.

This project historically used sqlite3 with '?' placeholders and some SQLite-specific
SQL (INSERT OR REPLACE, WAL pragmas). To support MySQL while keeping changes small,
we provide helpers that:

- convert '?' placeholders to '%s' for MySQL drivers
- rewrite a small set of SQLite-specific statements

This is intentionally minimal and pragmatic (not a full ORM migration).
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence


def is_mysql(conn: Any) -> bool:
    return conn.__class__.__module__.startswith("pymysql")


def _rewrite_sql_for_mysql(sql: str) -> str:
    s = sql
    # SQLite upsert shortcut
    if "INSERT OR REPLACE INTO settings" in s:
        # settings key/value in MySQL uses k/v
        s = (
            "INSERT INTO settings (k, v) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE v=VALUES(v)"
        )
    # generic placeholder conversion handled separately
    return s


def execute(conn: Any, sql: str, params: Sequence[Any] | None = None):
    params = params or ()
    if is_mysql(conn):
        sql2 = _rewrite_sql_for_mysql(sql)
        sql2 = sql2.replace("?", "%s")
        with conn.cursor() as cur:
            cur.execute(sql2, params)
        return None
    else:
        return conn.execute(sql, params)


def query_one(conn: Any, sql: str, params: Sequence[Any] | None = None):
    params = params or ()
    if is_mysql(conn):
        sql2 = _rewrite_sql_for_mysql(sql).replace("?", "%s")
        with conn.cursor() as cur:
            cur.execute(sql2, params)
            return cur.fetchone()
    else:
        cur = conn.execute(sql, params)
        return cur.fetchone()


def query_all(conn: Any, sql: str, params: Sequence[Any] | None = None):
    params = params or ()
    if is_mysql(conn):
        sql2 = _rewrite_sql_for_mysql(sql).replace("?", "%s")
        with conn.cursor() as cur:
            cur.execute(sql2, params)
            return cur.fetchall()
    else:
        cur = conn.execute(sql, params)
        return cur.fetchall()
