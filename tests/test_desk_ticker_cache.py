#!/usr/bin/env python3
"""In-process TTL ticker book — batch 24hr, no per-symbol first-paint loop."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mexc_bot.webapi import prices


def _row(sym: str, last: float, chg: float) -> dict:
    return {
        "symbol": sym,
        "lastPrice": str(last),
        "priceChangePercent": str(chg),
    }


class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


class TestDeskTickerCache(unittest.TestCase):
    def setUp(self):
        prices.reset_ticker_cache()

    def tearDown(self):
        prices.reset_ticker_cache()

    def test_normalize_compact_forms(self):
        self.assertEqual(prices.normalize_ticker_symbol("BTC_USDT"), "BTCUSDT")
        self.assertEqual(prices.normalize_ticker_symbol("btc-usdt"), "BTCUSDT")
        self.assertEqual(prices.normalize_ticker_symbol("ETH"), "ETHUSDT")
        self.assertEqual(prices.normalize_ticker_symbol(""), "")

    def test_watchlist_fetches_needed_once_then_ttl_cache(self):
        by = {
            "BTCUSDT": _row("BTCUSDT", 65000, 1.2),
            "ETHUSDT": _row("ETHUSDT", 3200, -0.4),
            "SOLUSDT": _row("SOLUSDT", 140, -2.5),
        }
        calls = []

        def fake_get(url, params=None, timeout=8):
            calls.append((url, params or {}))
            sym = (params or {}).get("symbol")
            if "mexc.com" in url and sym in by:
                return _FakeResp(by[sym])
            self.fail(f"unexpected GET {url} {params}")

        with patch.object(prices._session, "get", side_effect=fake_get):
            rows = prices.watchlist_tickers(
                ["BTC_USDT", "ETHUSDT", "SOL", "BTCUSDT"]
            )
            again = prices.watchlist_tickers(["ETHUSDT", "BTCUSDT"])

        # 3 unique names, not a 37-step sequential loop on every poll
        self.assertEqual(len(calls), 3)
        self.assertEqual([r["symbol"] for r in rows], ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
        self.assertEqual(rows[0]["price"], 65000.0)
        self.assertEqual(rows[0]["changePercent"], 1.2)
        self.assertEqual(rows[0]["source"], "mexc")
        self.assertEqual(len(again), 2)
        self.assertEqual(len(calls), 3, "TTL cache must skip a second network round")
        info = prices.ticker_cache_info()
        self.assertTrue(info["fresh"])
        self.assertGreaterEqual(info["size"], 3)
        self.assertEqual(info["last_source"], "needed_parallel")

    def test_ttl_reuse_then_refresh(self):
        n = {"i": 0}

        def fake_get(url, params=None, timeout=8):
            n["i"] += 1
            return _FakeResp(_row("BTCUSDT", 100 + n["i"], 0.1))

        with patch.object(prices._session, "get", side_effect=fake_get):
            a = prices.ticker_24h("BTCUSDT")
            b = prices.ticker_24h("BTCUSDT")
            self.assertEqual(a["price"], b["price"])
            self.assertEqual(n["i"], 1)

            with patch.object(prices, "TICKER_CACHE_TTL_S", 0):
                # Force expiry via timestamp, not the constant (already captured).
                prices._book_ts = 0.0
                c = prices.ticker_24h("BTCUSDT")
            self.assertEqual(n["i"], 2)
            self.assertEqual(c["price"], 102.0)

    def test_market_context_shares_book(self):
        by = {
            "BTCUSDT": _row("BTCUSDT", 60000, -3.5),
            "ETHUSDT": _row("ETHUSDT", 2000, -1.0),
            "SOLUSDT": _row("SOLUSDT", 100, 0.2),
        }
        calls = []

        def fake_get(url, params=None, timeout=8):
            calls.append((params or {}).get("symbol"))
            sym = (params or {}).get("symbol")
            return _FakeResp(by[sym])

        with patch.object(prices._session, "get", side_effect=fake_get):
            ctx = prices.market_context()
            ctx2 = prices.market_context()
            marks = prices.watchlist_tickers(["BTCUSDT", "ETHUSDT"])

        self.assertEqual(sorted(calls), ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
        self.assertEqual(ctx["regime"], "RISK_OFF")
        self.assertEqual(len(ctx["majors"]), 3)
        self.assertEqual(ctx2["regime"], "RISK_OFF")
        self.assertEqual(marks[0]["price"], 60000.0)

    def test_known_miss_is_not_refetched_within_ttl(self):
        calls = []

        def fake_get(url, params=None, timeout=8):
            calls.append((url, (params or {}).get("symbol")))
            return _FakeResp({}, status=400)

        with patch.object(prices._session, "get", side_effect=fake_get):
            a = prices.watchlist_tickers(["NOCOINUSDT"])
            b = prices.watchlist_tickers(["NOCOINUSDT"])
        self.assertEqual(a, [])
        self.assertEqual(b, [])
        mexc = [c for c in calls if "mexc.com" in c[0]]
        self.assertEqual(len(mexc), 1, "negative cache must skip a second MEXC round")

    def test_binance_fallback_on_mexc_miss(self):
        def fake_get(url, params=None, timeout=8):
            sym = (params or {}).get("symbol")
            if "mexc.com" in url:
                if sym == "BTCUSDT":
                    return _FakeResp(_row("BTCUSDT", 1, 0))
                return _FakeResp({}, status=400)
            if "binance.com" in url and sym == "PEPEUSDT":
                return _FakeResp(_row("PEPEUSDT", 0.001, -8.0))
            self.fail(f"{url} {params}")

        with patch.object(prices._session, "get", side_effect=fake_get):
            # Request path is MEXC-only; ticker_24h still has the Binance fallback.
            prices.reset_ticker_cache()
            btc = prices.ticker_24h("BTCUSDT")
            prices.reset_ticker_cache()
            pepe = prices.ticker_24h("PEPEUSDT")

        self.assertEqual(btc["source"], "mexc")
        self.assertEqual(pepe["source"], "binance")
        self.assertEqual(pepe["changePercent"], -8.0)

    def test_overview_strip_skips_full_learning_home(self):
        src = Path(ROOT / "mexc_bot/webapi/learning_v1.py").read_text(encoding="utf-8")
        # Strip body must not pull the 640KB home / money-review path.
        start = src.index("def overview_learning_strip")
        end = src.index("def learning_home_v1")
        body = src[start:end]
        # Ignore the docstring (it names the path we are avoiding).
        impl = body.split('"""', 2)[-1]
        self.assertNotIn("learning_home_v1(", impl)
        self.assertNotIn("list_money_reviews", impl)
        self.assertNotIn("what_have_you_learned", impl)
        self.assertIn("list_pending_questions", body)
        self.assertIn("list_lessons", body)

        # Shape the Overview UI already reads.
        self.assertIn("pending_questions", body)
        self.assertIn("has_lessons", body)
        self.assertIn("agent_summary", body)


class TestOverviewUsesSlimStrip(unittest.TestCase):
    def test_app_overview_calls_strip_not_home(self):
        text = (ROOT / "mexc_bot/webapi/app.py").read_text(encoding="utf-8")
        ov = text.split("def overview")[1].split("def get_alerts")[0]
        self.assertIn("overview_learning_strip", ov)
        self.assertNotIn("learning_home_v1", ov)
        self.assertIn("market_context", ov)
        self.assertIn("watchlist_tickers", text.split("def get_watch")[1].split("def desk_alarms")[0])
        self.assertIn("schedule_ticker_prewarm", text.split("def create_app")[1].split("def health")[0])


if __name__ == "__main__":
    unittest.main()
