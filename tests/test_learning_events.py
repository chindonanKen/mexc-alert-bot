#!/usr/bin/env python3
"""Learning EventStore: log, label, outcomes, journal — never touches alerts."""

import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mexc_bot.learning.store import EventStore
from mexc_bot.learning.outcomes import OutcomePoller
from mexc_bot.learning.integrity import (
    coach_must_not_claim_unlogged,
    validate_event_row,
)
from mexc_bot.coach.engine import format_brief, format_coach_reply
from mexc_bot.storage import AlertStore


class TestLearningEvents(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "alerts.db"
        self.store = EventStore(self.db)
        self.alerts = AlertStore(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_log_and_label_latest(self):
        eid = self.store.log_event(
            42,
            "mover_peak",
            "BTC_USDT",
            "futures",
            price=100.0,
            ref_price=110.0,
            drop_pct=-9.1,
            velocity_band="PANIC",
            mode="peak",
        )
        self.assertGreater(eid, 0)
        labeled = self.store.label_latest(42, action="took")
        self.assertEqual(labeled, eid)
        rows = self.store.recent_events(42, limit=5)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["last_action"], "took")
        self.assertEqual(rows[0]["velocity_band"], "PANIC")

    def test_label_by_symbol_filter(self):
        self.store.log_event(1, "mover_peak", "ETH_USDT", "futures", price=1.0, drop_pct=-5)
        self.store.log_event(1, "mover_step", "BTC_USDT", "futures", price=2.0, drop_pct=-5)
        eid = self.store.label_latest(1, symbol="ETH", action="skip")
        rows = self.store.recent_events(1, limit=10)
        eth = [r for r in rows if "ETH" in r["symbol"]][0]
        btc = [r for r in rows if "BTC" in r["symbol"]][0]
        self.assertEqual(eth["last_action"], "skip")
        self.assertIsNone(btc["last_action"])
        self.assertEqual(eid, eth["id"])

    def test_does_not_delete_alerts(self):
        # Add a real target alert and ensure learning ops leave it alone
        sid = self.alerts.add_alert(7, "BTCUSDT", 65000.0, market="spot")
        self.assertIsNotNone(sid)
        self.store.log_event(7, "mover_peak", "BTCUSDT", "spot", price=1.0)
        self.store.label_latest(7, action="took")
        self.store.journal_open(7, "BTCUSDT", "spot", entry_avg=1.0)
        alerts = self.alerts.get_user_alerts(7)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["symbol"], "BTCUSDT")

    def test_outcomes_pending_and_record(self):
        now = time.time()
        eid = self.store.log_event(
            9,
            "mover_peak",
            "SIRENUSDT",
            "spot",
            ts=now - 1000,
            price=10.0,
            drop_pct=-6,
            mode="peak",
        )
        pending = self.store.pending_outcomes([900], now=now)
        self.assertTrue(any(p["event_id"] == eid for p in pending))
        self.store.record_outcome(
            eid, 900, max_bounce_pct=3.0, max_dd_pct=-1.0, last_price=10.2
        )
        pending2 = self.store.pending_outcomes([900], now=now)
        self.assertFalse(any(p["event_id"] == eid for p in pending2))

    def test_outcome_poller_writes(self):
        now = time.time()
        eid = self.store.log_event(
            3,
            "mover_peak",
            "AAA_USDT",
            "futures",
            ts=now - 1000,
            price=50.0,
            mode="peak",
        )

        def get_price(market, symbol):
            return 55.0  # +10% bounce

        poller = OutcomePoller(
            self.store, get_price=get_price, horizons_seconds=[900], poll_seconds=60
        )
        poller._check_once()
        # extremes + record
        health = poller.get_health()
        self.assertGreaterEqual(health["outcomes_written"], 1)
        pending = self.store.pending_outcomes([900], now=time.time())
        self.assertFalse(any(p["event_id"] == eid for p in pending))

    def test_journal_open_close(self):
        tid = self.store.journal_open(5, "TSLAUSDT", "futures", entry_avg=250.0)
        self.assertGreater(tid, 0)
        opens = self.store.journal_list(5, open_only=True)
        self.assertEqual(len(opens), 1)
        ok = self.store.journal_close(5, trade_id=tid, exit_avg=260.0, notes="bounce")
        self.assertTrue(ok)
        self.assertEqual(len(self.store.journal_list(5, open_only=True)), 0)

    def test_coach_brief_text(self):
        self.store.log_event(
            1, "mover_peak", "X", "futures", price=1, drop_pct=-7, velocity_band="PANIC", mode="peak"
        )
        recent = self.store.recent_events(1)
        text = format_brief(recent_events=recent, open_trades=[], learning_on=True)
        self.assertIn("SESSION BRIEF", text)
        self.assertIn("PANIC", text)
        coach = format_coach_reply("is this panic?", recent_events=recent)
        self.assertIn("COACH", coach)
        self.assertIn("PANIC", coach.upper())

    def test_coach_empty_memory_no_false_claims(self):
        coach = format_coach_reply("what should I do?", recent_events=[], stats=None)
        self.assertIn("none logged", coach.lower())
        problems = coach_must_not_claim_unlogged(
            coach, has_events=False, has_stats=False
        )
        self.assertEqual(problems, [])

    def test_validate_event_row(self):
        ok = {
            "source": "target",
            "symbol": "BTCUSDT",
            "market": "spot",
            "price": 1.0,
            "velocity_band": "PANIC",
        }
        self.assertEqual(validate_event_row(ok), [])
        bad = {
            "source": "made_up",
            "symbol": "",
            "market": "both",
            "price": -1,
        }
        probs = validate_event_row(bad)
        self.assertTrue(len(probs) >= 2)

    def test_target_source_allowed(self):
        eid = self.store.log_event(
            11,
            "target",
            "ETHUSDT",
            "spot",
            price=2000.0,
            ref_price=2000.0,
            mode="crossed",
            payload={"reason": "crossed"},
        )
        self.assertGreater(eid, 0)
        row = self.store.recent_events(11, limit=1)[0]
        self.assertEqual(row["source"], "target")
        self.assertEqual(validate_event_row(row), [])


if __name__ == "__main__":
    unittest.main()
