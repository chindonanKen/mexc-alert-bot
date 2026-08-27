#!/usr/bin/env python3
"""A futures BUY fill is not an open. Open = exchange only."""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mexc_bot.learning.fills import (
    _write_futures_open_cache,
    _write_spot_balances_cache,
    count_exchange_open_positions,
)
from mexc_bot.learning.store import EventStore
from mexc_bot.webapi.positions_enrich import list_position_entities


class TestFillIsNotAPosition(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "t.db"
        self._old = os.environ.get("ALERTS_FILE")
        os.environ["ALERTS_FILE"] = str(self.db)
        self.store = EventStore(self.db)
        self.uid = 8630949601

    def tearDown(self):
        if self._old is None:
            os.environ.pop("ALERTS_FILE", None)
        else:
            os.environ["ALERTS_FILE"] = self._old
        self.tmp.cleanup()

    def test_futures_buy_fill_does_not_journal_open(self):
        row = {
            "user_id": self.uid,
            "exchange_trade_id": "gua-buy-1",
            "symbol": "GUA_USDT",
            "market": "futures",
            "side": "buy",
            "price": 0.01,
            "qty": 100,
            "quote_qty": 1.0,
            "ts": time.time(),
            "raw": {"id": "gua-buy-1"},
        }
        self.assertTrue(self.store.insert_fill(**row))
        self.store.upsert_journal_from_fill(row)
        self.assertEqual(self.store.journal_list(self.uid, open_only=True), [])

    def test_journal_auto_fill_gua_is_not_a_desk_open(self):
        self.store.journal_open(
            self.uid,
            "GUA_USDT",
            "futures",
            entry_avg=0.01,
            notes="auto from MEXC fill",
        )
        _write_futures_open_cache(self.store, self.uid, [])
        _write_spot_balances_cache(self.store, self.uid, [])
        with patch(
            "mexc_bot.webapi.positions_enrich.ticker_24h",
            return_value={"price": 0.01, "changePercent": 0, "source": "test"},
        ), patch(
            "mexc_bot.learning.fills.fetch_live_futures_opens",
            return_value=[],
        ):
            ents = list_position_entities(self.uid, include_closed=False)
        opens = [
            e
            for e in ents
            if e.get("status") == "open"
            and "GUA" in str(e.get("symbol") or "").upper()
        ]
        self.assertEqual(opens, [])

    def test_telegram_count_ignores_journal_uses_exchange(self):
        self.store.journal_open(
            self.uid,
            "GUA_USDT",
            "futures",
            entry_avg=0.01,
            notes="auto from MEXC fill",
        )
        self.assertEqual(count_exchange_open_positions(self.store, self.uid), 0)
        _write_futures_open_cache(
            self.store,
            self.uid,
            [{"symbol": "SYN_USDT", "hold_vol": 12.0}],
        )
        _write_spot_balances_cache(self.store, self.uid, [])
        self.assertEqual(count_exchange_open_positions(self.store, self.uid), 1)
        bot = (ROOT / "mexc_bot" / "bot.py").read_text(encoding="utf-8")
        self.assertIn("count_exchange_open_positions", bot)
        self.assertIn("_exchange_open_count", bot)

    def test_no_position_opened_ping_copy(self):
        src = (ROOT / "mexc_bot" / "learning" / "fills.py").read_text(encoding="utf-8")
        bot = (ROOT / "mexc_bot" / "bot.py").read_text(encoding="utf-8")
        main = (ROOT / "mexc_bot" / "main.py").read_text(encoding="utf-8")
        for blob in (src, bot):
            self.assertNotIn("started a position", blob.lower())
            self.assertNotIn("position opened:", blob.lower())
        self.assertIn("write_auto_journal=False", main)
        self.assertIn("MEXC fills synced", src)


class TestBuildIdentity(unittest.TestCase):
    def test_env_sha_and_tag(self):
        from mexc_bot.webapi.build_info import build_identity

        with patch.dict(
            "os.environ",
            {"GIT_SHA": "abc123def", "IMAGE_TAG": "mexc-ad-desk:pre-lab-s1"},
            clear=False,
        ):
            ident = build_identity()
        self.assertEqual(ident["git_sha"], "abc123def")
        self.assertEqual(ident["image_tag"], "mexc-ad-desk:pre-lab-s1")


if __name__ == "__main__":
    unittest.main()
