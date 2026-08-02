#!/usr/bin/env python3
"""Trade dossiers, ticker profiles, rich pending, watch remove parity."""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mexc_bot.learning.store import EventStore
from mexc_bot.learning.trades import (
    build_trade_dossier,
    list_trade_dossiers,
    ticker_profile,
    enrich_pending_row,
)
from mexc_bot.learning.engagement import EngagementBridge


class TestTradeDossiers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "t.db"
        self.store = EventStore(self.db)
        self.uid = 8630949601
        os.environ["ALERTS_FILE"] = str(self.db)
        os.environ["DESK_USER_ID"] = str(self.uid)

    def tearDown(self):
        self.tmp.cleanup()

    def test_closed_trade_dossier_pnl_hold_layers(self):
        now = time.time()
        tid = self.store.journal_open(
            self.uid, "SIRENUSDT", "spot", entry_avg=0.10, notes="entry"
        )
        with self.store._lock:
            self.store._get_conn().execute(
                "UPDATE journal_trades SET opened_at = ? WHERE id = ?",
                (now - 7200, tid),
            )
        self.store.insert_fill(
            user_id=self.uid,
            exchange_trade_id="b1",
            symbol="SIRENUSDT",
            market="spot",
            side="buy",
            price=0.10,
            qty=1000,
            ts=now - 7100,
        )
        self.store.insert_fill(
            user_id=self.uid,
            exchange_trade_id="b2",
            symbol="SIRENUSDT",
            market="spot",
            side="buy",
            price=0.09,
            qty=2000,
            ts=now - 7000,
        )
        self.store.journal_close(self.uid, trade_id=tid, exit_avg=0.12, notes="tp")
        with self.store._lock:
            self.store._get_conn().execute(
                "UPDATE journal_trades SET closed_at = ? WHERE id = ?",
                (now - 100, tid),
            )
        self.store.insert_fill(
            user_id=self.uid,
            exchange_trade_id="s1",
            symbol="SIRENUSDT",
            market="spot",
            side="sell",
            price=0.12,
            qty=3000,
            ts=now - 90,
        )
        eid = self.store.log_event(
            self.uid,
            "mover_peak",
            "SIRENUSDT",
            "spot",
            ts=now - 7300,
            price=0.105,
            drop_pct=-8,
            velocity_band="PANIC",
            mode="peak",
        )
        dossiers = list_trade_dossiers(self.store, self.uid, closed_only=True)
        self.assertEqual(len(dossiers), 1)
        d = dossiers[0]
        self.assertEqual(d["id"], tid)
        self.assertIsNotNone(d["pnl_pct"])
        self.assertGreater(d["pnl_pct"], 15)  # 0.10 -> 0.12 = +20%
        self.assertIsNotNone(d["hold_hours"])
        self.assertGreaterEqual(d["n_buys"], 1)
        self.assertGreaterEqual(d["n_sells"], 1)
        self.assertTrue(d["linked_events"])
        self.assertEqual(d["primary_event_id"], eid)

    def test_ticker_profile(self):
        now = time.time()
        self.store.log_event(
            self.uid,
            "mover_peak",
            "BTC_USDT",
            "futures",
            ts=now - 100,
            price=1,
            velocity_band="PANIC",
        )
        tid = self.store.journal_open(self.uid, "BTC_USDT", "futures", entry_avg=100)
        self.store.journal_close(self.uid, trade_id=tid, exit_avg=105)
        prof = ticker_profile(self.store, self.uid, "BTC_USDT", "futures")
        self.assertGreaterEqual(prof["fires"], 1)
        self.assertGreaterEqual(prof["closed_trades"], 1)
        self.assertIsNotNone(prof["avg_pnl_pct"])

    def test_rich_pending_payload(self):
        now = time.time()
        eid = self.store.log_event(
            self.uid,
            "mover_peak",
            "ETH_USDT",
            "futures",
            ts=now - 5000,
            price=2000,
            drop_pct=-9,
            velocity_band="PANIC",
            mode="peak",
        )
        # late journal → pending with rich payload
        self.store.journal_open(self.uid, "ETH_USDT", "futures", entry_avg=2100)
        with self.store._lock:
            self.store._get_conn().execute(
                "UPDATE journal_trades SET opened_at = ? WHERE user_id = ?",
                (now - 1000, self.uid),
            )
        bridge = EngagementBridge(
            self.store, grace_seconds=3600, max_pending=2
        )
        bridge.run_once(now=now)
        pending = self.store.list_pending_questions(self.uid)
        self.assertTrue(pending)
        rich = enrich_pending_row(self.store, pending[0])
        self.assertIn("event", rich)
        self.assertEqual(rich.get("fire_price") or rich["event"].get("price"), 2000)
        self.assertIn("PANIC", (rich.get("velocity_band") or rich["event"].get("velocity_band") or ""))
        self.assertIn("inferred_action", rich)
        # question should mention symbol/price context from bridge
        self.assertIn("ETH", pending[0]["question"])


class TestWatchRemoveParity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "w.db"
        os.environ["ALERTS_FILE"] = str(self.db)
        os.environ["DESK_USER_ID"] = "8630949601"
        # ensure tables
        EventStore(self.db)
        from mexc_bot.webapi import db as desk_db

        conn = desk_db.connect()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mover_watchlist (
                user_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                market TEXT NOT NULL,
                PRIMARY KEY (user_id, symbol, market)
            )
            """
        )
        conn.execute(
            "INSERT INTO mover_watchlist VALUES (8630949601, 'BTC_USDT', 'futures')"
        )
        conn.execute(
            "INSERT INTO mover_watchlist VALUES (8630949601, 'TSLAUSDT', 'futures')"
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_remove_watch_norm_forms(self):
        from mexc_bot.webapi import actions

        r = actions.remove_watch("BTCUSDT", user_id=8630949601)
        self.assertTrue(r["ok"])
        self.assertTrue(r["removed"])
        # second form for TSLA compact
        r2 = actions.remove_watch("TSLA", market="futures", user_id=8630949601)
        self.assertTrue(r2["ok"])
        from mexc_bot.webapi import db as desk_db

        left = desk_db.fetch_all(
            "SELECT * FROM mover_watchlist WHERE user_id=8630949601"
        )
        self.assertEqual(left, [])

    def test_remove_missing_errors(self):
        from mexc_bot.webapi import actions

        with self.assertRaises(ValueError):
            actions.remove_watch("NOPE_COIN", user_id=8630949601)


class TestLearningApiBundle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "b.db"
        os.environ["ALERTS_FILE"] = str(self.db)
        os.environ["DESK_USER_ID"] = "8630949601"
        self.store = EventStore(self.db)
        self.uid = 8630949601

    def tearDown(self):
        self.tmp.cleanup()

    def test_bundle_has_trades_tickers_pending_fields(self):
        from mexc_bot.webapi import learning_api

        now = time.time()
        eid = self.store.log_event(
            self.uid,
            "mover_peak",
            "SOL_USDT",
            "futures",
            ts=now - 100,
            price=100,
            drop_pct=-6,
            velocity_band="FAST",
        )
        self.store.journal_open(self.uid, "SOL_USDT", "futures", entry_avg=99)
        self.store.enqueue_pending_question(
            self.uid,
            question="SOL_USDT [F] FAST drop=-6 @ 100 · test?",
            event_id=eid,
            symbol="SOL_USDT",
            payload={
                "inference": {"action": "skip", "confidence": 0.5, "reason": "test"},
                "event": {
                    "id": eid,
                    "symbol": "SOL_USDT",
                    "price": 100,
                    "drop_pct": -6,
                    "velocity_band": "FAST",
                    "ts": now - 100,
                },
            },
        )
        b = learning_api.learning_bundle(self.uid)
        self.assertIn("trades", b)
        self.assertIn("tickers", b)
        self.assertIn("closed_trades", b)
        self.assertTrue(b["pending_questions"])
        p0 = b["pending_questions"][0]
        self.assertTrue(p0.get("fire_price") == 100 or (p0.get("event") or {}).get("price") == 100)
        out = learning_api.coach_ask("SOL process?", user_id=self.uid)
        self.assertIn("reply", out)
        # should mention trade or ticker context eventually
        self.assertIn("SOL", out["reply"].upper())


if __name__ == "__main__":
    unittest.main()
