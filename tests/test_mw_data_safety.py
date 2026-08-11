#!/usr/bin/env python3
"""Hard safety: mover watchlist must not wipe on bad /mw usage."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mexc_bot.bot import _parse_mw_token, _strip_mw_token
from mexc_bot.movers.storage import MoverStore


class TestMwTokenParse(unittest.TestCase):
    def test_strip_quotes(self) -> None:
        self.assertEqual(_strip_mw_token("'BLUAI'"), "BLUAI")
        self.assertEqual(_strip_mw_token('"BTC"'), "BTC")

    def test_parse_quoted(self) -> None:
        body, mkt = _parse_mw_token("'BLUAI'", default_market="futures")
        self.assertEqual(body, "BLUAI")
        self.assertEqual(mkt, "futures")


class TestWatchlistNoWipe(unittest.TestCase):
    def test_set_watchlist_refuses_empty_replace(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = MoverStore(Path(td) / "t.db")
            u = 1
            store.set_watchlist(
                u,
                [
                    {"symbol": "BTC_USDT", "market": "futures"},
                    {"symbol": "ETH_USDT", "market": "futures"},
                    {"symbol": "SOL_USDT", "market": "futures"},
                ],
            )
            assert len(store.get_watchlist(u)) == 3
            with self.assertRaises(ValueError) as ctx:
                store.set_watchlist(u, [])
            self.assertIn("refusing", str(ctx.exception).lower())
            self.assertEqual(len(store.get_watchlist(u)), 3)

    def test_set_watchlist_force_empty_for_clear(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = MoverStore(Path(td) / "t.db")
            u = 2
            store.set_watchlist(u, [{"symbol": "BTC_USDT", "market": "futures"}])
            n = store.set_watchlist(u, [], force_empty=True)
            self.assertEqual(n, 0)

    def test_add_preserves_existing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = MoverStore(Path(td) / "t.db")
            u = 3
            store.set_watchlist(
                u,
                [
                    {"symbol": "BTC_USDT", "market": "futures"},
                    {"symbol": "ETH_USDT", "market": "futures"},
                ],
            )
            store.add_watchlist(u, "SOL_USDT", "futures")
            syms = {r["symbol"] for r in store.get_watchlist(u)}
            self.assertEqual(syms, {"BTC_USDT", "ETH_USDT", "SOL_USDT"})

    def test_failed_resolve_pattern_would_not_empty_via_set(self) -> None:
        """Simulates bare /mw BADCOIN that resolved to zero items — must not wipe."""
        with tempfile.TemporaryDirectory() as td:
            store = MoverStore(Path(td) / "t.db")
            u = 4
            store.set_watchlist(
                u,
                [{"symbol": "BLUAI_USDT", "market": "futures"}] * 1
                + [{"symbol": f"COIN{i}_USDT", "market": "futures"} for i in range(5)],
            )
            before = len(store.get_watchlist(u))
            self.assertGreater(before, 1)
            # old bug: set_watchlist(u, []) after all resolve failed
            with self.assertRaises(ValueError):
                store.set_watchlist(u, [])
            self.assertEqual(len(store.get_watchlist(u)), before)


class TestBotMwHandlerSourceGuard(unittest.TestCase):
    """Static: bare symbols must not call set_watchlist; clear needs confirm."""

    def test_bot_mw_source_safety(self) -> None:
        text = Path("mexc_bot/bot.py").read_text(encoding="utf-8")
        # Extract cmd_mover_watch roughly
        start = text.find('commands=["mw"')
        self.assertGreater(start, 0)
        chunk = text[start : start + 12000]
        self.assertIn("clear confirm", chunk)
        self.assertIn("replace", chunk)
        self.assertIn("do_add", chunk)
        # Destructive set_watchlist only inside replace/set/only path with empty abort
        self.assertIn("Replace aborted", chunk)
        self.assertIn("List unchanged for failed names", chunk)


if __name__ == "__main__":
    unittest.main()
