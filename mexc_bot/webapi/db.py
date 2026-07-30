"""Read-mostly access to the shared SQLite alerts.db for the desk API."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional


def db_path() -> Path:
    raw = os.getenv("ALERTS_FILE", "data/alerts.json")
    p = Path(raw)
    if str(p).endswith(".json"):
        p = p.with_suffix(".db")
    return p


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def rows_to_dicts(rows) -> List[Dict[str, Any]]:
    return [dict(r) for r in rows]


def fetch_all(sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    conn = connect()
    try:
        if "FROM " in sql.upper():
            # crude table check for first table name — callers handle empty
            pass
        cur = conn.execute(sql, params)
        return rows_to_dicts(cur.fetchall())
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def fetch_one(sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
    rows = fetch_all(sql, params)
    return rows[0] if rows else None


def default_user_id() -> Optional[int]:
    env = os.getenv("DESK_USER_ID") or os.getenv("MEXC_PRIVATE_TELEGRAM_USER_ID")
    if env and str(env).strip().isdigit():
        return int(env)
    # first user with alerts or mover settings
    for sql in (
        "SELECT user_id FROM alerts ORDER BY id ASC LIMIT 1",
        "SELECT user_id FROM mover_settings ORDER BY user_id ASC LIMIT 1",
        "SELECT user_id FROM learning_events ORDER BY id DESC LIMIT 1",
    ):
        try:
            conn = connect()
            if not table_exists(conn, sql.split()[3] if False else "alerts"):
                pass
            row = conn.execute(sql).fetchone()
            conn.close()
            if row:
                return int(row[0] if not isinstance(row, sqlite3.Row) else row["user_id"])
        except Exception:
            continue
    # try mover_settings / learning separately
    for table, col in (
        ("alerts", "user_id"),
        ("mover_settings", "user_id"),
        ("learning_events", "user_id"),
    ):
        try:
            conn = connect()
            if not table_exists(conn, table):
                conn.close()
                continue
            row = conn.execute(
                f"SELECT {col} FROM {table} ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            conn.close()
            if row:
                return int(row[0])
        except Exception:
            continue
    return None
