#!/usr/bin/env python3
"""Drop only machine_* SQLite tables. Never touch alerts, movers, journal, learning.

Usage:
  python3 scripts/drop_machine_tables.py --db data/alerts.db
  python3 scripts/drop_machine_tables.py --db data/alerts.db --dry-run
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

MACHINE_TABLES = (
    "machine_plans",
    "machine_orders",
    "machine_closes",
    "machine_kb",
    "machine_needs_you",
    "machine_log",
    "machine_process_pack",
)

CENSUS_TABLES = (
    "alerts",
    "mover_watchlist",
    "mover_settings",
    "journal_trades",
    "journal_fills",
    "learning_events",
    "learning_labels",
    "learning_outcomes",
    "learning_lessons",
    "learning_pending_questions",
    "position_flags",
)


def _tables(con: sqlite3.Connection) -> set[str]:
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    return {str(r[0]) for r in rows}


def _census(con: sqlite3.Connection) -> dict[str, int]:
    present = _tables(con)
    out: dict[str, int] = {}
    for name in CENSUS_TABLES:
        if name not in present:
            continue
        out[name] = int(con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
    return out


def drop_machine_tables(db_path: Path, *, dry_run: bool = False) -> dict:
    if not db_path.is_file():
        raise FileNotFoundError(f"db not found: {db_path}")
    con = sqlite3.connect(str(db_path))
    try:
        con.execute("PRAGMA foreign_keys=OFF")
        present = _tables(con)
        targets = [t for t in MACHINE_TABLES if t in present]
        extras = sorted(
            n
            for n in present
            if n.startswith("machine_") and n not in MACHINE_TABLES
        )
        for name in extras:
            if not name.startswith("machine_"):
                raise RuntimeError(f"refusing non-machine table {name}")
            targets.append(name)
        before = _census(con)
        dropped: list[str] = []
        if not dry_run:
            for name in targets:
                if not name.startswith("machine_"):
                    raise RuntimeError(f"refusing to drop {name}")
                con.execute(f'DROP TABLE IF EXISTS "{name}"')
                dropped.append(name)
            con.commit()
        after = _census(con)
        if after != before:
            raise RuntimeError(
                f"protected row counts changed: before={before} after={after}"
            )
        leftover = [n for n in _tables(con) if n.startswith("machine_")]
        if not dry_run and leftover:
            raise RuntimeError(f"machine tables still present: {leftover}")
        return {
            "db": str(db_path),
            "dry_run": dry_run,
            "would_drop" if dry_run else "dropped": targets,
            "census": after,
        }
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    try:
        result = drop_machine_tables(args.db, dry_run=args.dry_run)
    except Exception as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
