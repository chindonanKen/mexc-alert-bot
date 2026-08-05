#!/usr/bin/env python3
"""Backfill full delist ticker lists into news_events (run on host if Docker is 403'd by MEXC)."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# repo root
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mexc_bot.news.sources import _enrich_mexc_article, _session  # noqa: E402
from mexc_bot.news.store import NewsStore  # noqa: E402
from mexc_bot.news.tickers import extract_delist_bases  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--db",
        default=str(ROOT / "data" / "alerts.db"),
        help="Path to alerts.db",
    )
    args = ap.parse_args()
    db = Path(args.db)
    if not db.exists():
        print("missing db", db)
        return 1
    store = NewsStore(db)
    rows = store.recent(limit=80)
    sess = _session()
    fixed = 0
    for row in rows:
        title = row.get("title") or ""
        url = row.get("url") or ""
        symbol = row.get("symbol") or ""
        existing = []
        if symbol and "," in symbol:
            existing = [x.strip() for x in symbol.split(",") if x.strip()]
        raw = {}
        try:
            raw = json.loads(row.get("raw_json") or "{}")
        except Exception:
            raw = {}
        if raw.get("bases") and len(raw["bases"]) >= 3:
            continue
        if existing and len(existing) >= 3 and not re.search(
            r"and\s+\d+\s+other", title, re.I
        ):
            continue
        bases = list(existing)
        if "/announcements/article/" in url:
            enr = _enrich_mexc_article(sess, url, title, timeout=15.0)
            bases = enr.get("bases") or bases
            if enr.get("body"):
                raw["body"] = enr["body"]
        if not bases:
            bases = extract_delist_bases(title, raw.get("body") or "")
        if not bases:
            print("skip", row.get("id"), title[:60])
            continue
        raw["bases"] = bases
        list_title = title.split(" · full:")[0].strip()
        display = (
            f"{list_title} · full: {', '.join(bases)}"
            if re.search(r"and\s+\d+\s+other", list_title, re.I)
            else list_title
        )
        ok = store.update_news_symbols(
            int(row["id"]),
            symbol=",".join(bases),
            title=display,
            raw=raw,
        )
        print(
            "ok" if ok else "fail",
            row.get("id"),
            bases,
            display[:90],
        )
        if ok:
            fixed += 1
    print("fixed", fixed, "of", len(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
