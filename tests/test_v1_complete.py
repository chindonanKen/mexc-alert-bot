#!/usr/bin/env python3
"""V1 complete suite: news classify, fills, private map, integrity with target source."""

import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mexc_bot.news.classify import (
    classify_headline,
    extract_symbol_hints,
    should_push,
    TRUST_OFFICIAL,
    TRUST_REKT,
    TRUST_AGGREGATE,
)
from mexc_bot.exchange_private import trade_to_fill_row, normalize_spot_symbol_from_mexc
from mexc_bot.learning.store import EventStore
from mexc_bot.news.store import NewsStore
from mexc_bot.assistant.ux import parse_callback, fire_action_keyboard


class TestNewsClassify(unittest.TestCase):
    def test_delist_official_fatal(self):
        r = classify_headline(
            "MEXC Will Delist XYZUSDT Trading Pair",
            source_trust=TRUST_OFFICIAL,
        )
        self.assertIsNotNone(r)
        self.assertEqual(r[0], "DELIST")
        self.assertEqual(r[1], "fatal")
        self.assertTrue(should_push(r[1], TRUST_OFFICIAL))

    def test_hack_rekt(self):
        r = classify_headline(
            "Protocol X exploited — $10M drained",
            source_trust=TRUST_REKT,
        )
        self.assertEqual(r[0], "HACK")
        self.assertTrue(should_push(r[1], TRUST_REKT))

    def test_analyst_denied(self):
        r = classify_headline(
            "Analyst says price might crash next week",
            source_trust=TRUST_AGGREGATE,
        )
        self.assertIsNone(r)

    def test_symbol_hints(self):
        hints = extract_symbol_hints("SIREN delisting soon", {"SIREN", "BTC"})
        self.assertIn("SIREN", hints)


class TestFills(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "t.db"
        self.store = EventStore(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_trade_to_fill_and_insert(self):
        trade = {
            "id": "999",
            "symbol": "BTCUSDT",
            "price": "50000",
            "qty": "0.01",
            "quoteQty": "500",
            "isBuyer": True,
            "time": int(time.time() * 1000),
        }
        row = trade_to_fill_row(trade, 42)
        self.assertIsNotNone(row)
        self.assertEqual(row["side"], "buy")
        self.assertTrue(self.store.insert_fill(**{**row, "raw": trade}))
        self.assertFalse(self.store.insert_fill(**{**row, "raw": trade}))  # dedupe
        fills = self.store.recent_fills(42)
        self.assertEqual(len(fills), 1)
        self.store.upsert_journal_from_fill(row)
        opens = self.store.journal_list(42, open_only=True)
        self.assertEqual(len(opens), 1)

    def test_normalize(self):
        self.assertEqual(normalize_spot_symbol_from_mexc("btc_usdt"), "BTCUSDT")


class TestNewsStore(unittest.TestCase):
    def test_fingerprint_dedupe(self):
        tmp = tempfile.TemporaryDirectory()
        store = NewsStore(Path(tmp.name) / "n.db")
        n1 = store.insert(
            symbol="SIREN",
            class_="DELIST",
            severity="fatal",
            title="Delist SIREN",
            url="http://x",
            source="mexc",
            source_trust="official",
            fingerprint="abc123",
        )
        n2 = store.insert(
            symbol="SIREN",
            class_="DELIST",
            severity="fatal",
            title="Delist SIREN",
            url="http://x",
            source="mexc",
            source_trust="official",
            fingerprint="abc123",
        )
        self.assertGreater(n1, 0)
        self.assertEqual(n2, 0)
        self.assertTrue(store.has_fingerprint("abc123"))
        tmp.cleanup()


class TestFireKeyboard(unittest.TestCase):
    def test_keyboard_builds(self):
        kb = fire_action_keyboard(15)
        self.assertIsNotNone(kb)
        self.assertEqual(parse_callback("L:t:15"), ("took", 15))


if __name__ == "__main__":
    unittest.main()
