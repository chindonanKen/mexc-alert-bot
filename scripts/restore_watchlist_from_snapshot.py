#!/usr/bin/env python3
"""Additive restore of mover_watchlist from data/.safety/watchlist_snapshot.json.

Usage:
  python3 scripts/restore_watchlist_from_snapshot.py
  python3 scripts/restore_watchlist_from_snapshot.py --db data/alerts.db
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mexc_bot.movers.storage import MoverStore  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="data/alerts.db")
    args = ap.parse_args()
    db = Path(args.db)
    if not db.is_file():
        print(f"FAIL: no database at {db}", file=sys.stderr)
        return 1
    result = MoverStore(db).restore_watchlist_from_snapshot()
    print(result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
