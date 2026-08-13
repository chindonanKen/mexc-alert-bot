#!/usr/bin/env python3
"""Explicit one-shot mover_watchlist PK upgrade. Never run from bot/desk init.

Usage (repo root or droplet):
  python3 scripts/migrate_watchlist_schema.py
  python3 scripts/migrate_watchlist_schema.py --db data/alerts.db

Refuses to create an empty table. Holds the shared schema lock.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mexc_bot.db_safety import (  # noqa: E402
    SchemaSafetyError,
    row_count,
    watchlist_pk_columns,
    watchlist_schema_is_final,
)
from mexc_bot.movers.storage import MoverStore  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--db",
        default="data/alerts.db",
        help="SQLite path (default: data/alerts.db)",
    )
    args = ap.parse_args()
    db = Path(args.db)
    if not db.is_file():
        print(f"FAIL: no database at {db}", file=sys.stderr)
        return 1

    store = MoverStore(db)
    conn = store._get_conn()
    before = row_count(conn, "mover_watchlist")
    print(f"watchlist rows before: {before}")
    print(f"pk before: {watchlist_pk_columns(conn)}")
    if watchlist_schema_is_final(conn):
        print("already final PK (set_id, symbol, market) — nothing to do")
        return 0
    try:
        ran = store.upgrade_watchlist_pk(conn)
    except SchemaSafetyError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1
    after = row_count(conn, "mover_watchlist")
    print(f"rebuilt={ran} rows after={after} pk={watchlist_pk_columns(conn)}")
    if after < before:
        print("FAIL: row count shrank", file=sys.stderr)
        return 1
    if not watchlist_schema_is_final(conn):
        print("FAIL: PK still not final", file=sys.stderr)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
