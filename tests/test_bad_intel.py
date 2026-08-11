#!/usr/bin/env python3
"""Book intel bad-news feed for Overview."""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from mexc_bot.webapi.bad_intel import load_bad_intel_feed


def test_always_top_n_no_horizon():
    now = time.time()
    old = now - 30 * 86400  # 30 days ago

    def fetch_all(sql, params=None):
        if "news_events" in sql:
            return [
                {
                    "id": 1,
                    "symbol": "HFT",
                    "class": "DELIST",
                    "severity": "fatal",
                    "title": "Binance will delist HFTUSDT on next week",
                    "url": "https://example.com/hft",
                    "source": "binance",
                    "ts": old,
                },
                {
                    "id": 2,
                    "symbol": "SCAMCOIN",
                    "class": "SCAM",
                    "severity": "fatal",
                    "title": "Confirmed scam rug pull on SCAMCOIN",
                    "url": "https://example.com/scam",
                    "source": "rekt",
                    "ts": now - 3600,
                },
            ]
        if "delist_cache" in sql:
            return [
                {
                    "id": 10,
                    "exchange": "mexc",
                    "base": "ARROW",
                    "title": "Delisting of ARROW USDT-M Perpetual",
                    "url": "https://mexc.com/a",
                    "kind": "delist",
                    "ts": now - 86400,
                    "fingerprint": "a",
                },
                {
                    "id": 11,
                    "exchange": "mexc",
                    "base": "LEVI",
                    "title": "Delisting of ARROW USDT-M Perpetual",
                    "url": "https://mexc.com/a",
                    "kind": "delist",
                    "ts": now - 86400,
                    "fingerprint": "b",
                },
                {
                    "id": 12,
                    "exchange": "okx",
                    "base": "LATEST",
                    "title": "Delistings Announcements | Latest Delisted Cryptos | Help Center",
                    "url": "https://okx.com/help",
                    "kind": "delist",
                    "ts": now,
                    "fingerprint": "junk",
                },
            ]
        return []

    rows = load_bad_intel_feed(
        fetch_all, limit=5, book_bases={"HFT", "BTW"}, now=now
    )
    assert len(rows) >= 2
    assert len(rows) <= 5
    # Junk hub title excluded
    assert not any("Latest Delisted Cryptos" in (r.get("title") or "") for r in rows)
    # Old delist still present
    assert any("HFT" in (r.get("title") or "") or r.get("symbol") == "HFT" for r in rows)
    # Book hit preferred first when present
    assert rows[0].get("book_hit") is True
    # ARROW+LEVI collapsed to one announcement
    arrow_titles = [r for r in rows if "ARROW" in (r.get("title") or "")]
    assert len(arrow_titles) == 1
    print("PASS: bad intel top-N, no horizon, book prefer, dedupe")


if __name__ == "__main__":
    test_always_top_n_no_horizon()
    print("ALL BAD INTEL TESTS PASSED")
