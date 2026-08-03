#!/usr/bin/env python3
"""AD Desk learning spine: engagement bridge, pending queue, lessons, coach.

Drives real EventStore / EngagementBridge / coach.engine — never alerts deletes.
"""

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
from mexc_bot.learning.engagement import (
    EngagementBridge,
    infer_engagement,
    DEFAULT_GRACE_SECONDS,
)
from mexc_bot.learning.integrity import assert_alerts_table_intact, ALLOWED_BEHAVIOR
from mexc_bot.coach.engine import (
    format_coach_reply,
    format_coach_pulse,
    format_brief,
)
from mexc_bot.storage import AlertStore


class TestEngagementInfer(unittest.TestCase):
    def test_took_from_journal_within_grace(self):
        now = 1_700_000_000.0
        event = {
            "id": 1,
            "symbol": "BTC_USDT",
            "market": "futures",
            "ts": now - 600,
            "price": 100.0,
        }
        journal = [
            {
                "symbol": "BTC_USDT",
                "market": "futures",
                "opened_at": now - 300,
                "entry_avg": 99.0,
            }
        ]
        inf = infer_engagement(
            event, journal_opens=journal, fills=[], now=now, grace_seconds=3600
        )
        self.assertEqual(inf["action"], "took")
        self.assertGreaterEqual(inf["confidence"], 0.75)
        self.assertFalse(inf["needs_question"])

    def test_skip_after_grace_flat(self):
        now = 1_700_000_000.0
        event = {
            "id": 2,
            "symbol": "ETH_USDT",
            "market": "futures",
            "ts": now - 4000,
            "price": 50.0,
        }
        inf = infer_engagement(
            event, journal_opens=[], fills=[], now=now, grace_seconds=3600
        )
        self.assertEqual(inf["action"], "skip")
        self.assertGreaterEqual(inf["confidence"], 0.85)
        self.assertEqual(inf["source"], "auto_skip")

    def test_still_in_grace_no_skip(self):
        now = 1_700_000_000.0
        event = {
            "id": 3,
            "symbol": "X",
            "market": "spot",
            "ts": now - 100,
            "price": 1.0,
        }
        inf = infer_engagement(
            event, journal_opens=[], fills=[], now=now, grace_seconds=3600
        )
        self.assertIsNone(inf["action"])
        self.assertEqual(inf["source"], "pending_grace")

    def test_late_after_grace(self):
        now = 1_700_000_000.0
        ets = now - 5000
        event = {
            "id": 4,
            "symbol": "SIRENUSDT",
            "market": "spot",
            "ts": ets,
            "price": 0.1,
        }
        journal = [
            {
                "symbol": "SIRENUSDT",
                "market": "spot",
                "opened_at": ets + 4000,
                "entry_avg": 0.12,
            }
        ]
        inf = infer_engagement(
            event, journal_opens=journal, fills=[], now=now, grace_seconds=3600
        )
        self.assertEqual(inf["action"], "late")
        self.assertTrue(inf["needs_question"])


class TestEngagementBridgeStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "alerts.db"
        self.store = EventStore(self.db)
        self.alerts = AlertStore(self.db)
        self.uid = 8630949601

    def tearDown(self):
        self.tmp.cleanup()

    def test_bridge_auto_skip_and_took(self):
        now = time.time()
        # Past grace, flat → skip
        eid_skip = self.store.log_event(
            self.uid,
            "mover_peak",
            "AAA_USDT",
            "futures",
            ts=now - 4000,
            price=10.0,
            drop_pct=-8,
            velocity_band="PANIC",
            mode="peak",
        )
        # Journal take within grace
        eid_took = self.store.log_event(
            self.uid,
            "mover_peak",
            "BBB_USDT",
            "futures",
            ts=now - 500,
            price=20.0,
            drop_pct=-6,
            velocity_band="FAST",
            mode="peak",
        )
        self.store.journal_open(
            self.uid, "BBB_USDT", "futures", entry_avg=19.5, notes="test"
        )
        # Patch journal opened_at into window (journal_open uses now)
        # Re-open with control: update via SQL
        conn = self.store._get_conn()
        conn.execute(
            "UPDATE journal_trades SET opened_at = ? WHERE user_id = ? AND symbol = ?",
            (now - 200, self.uid, "BBB_USDT"),
        )

        bridge = EngagementBridge(
            self.store, grace_seconds=3600, max_pending=2, poll_seconds=60
        )
        out = bridge.run_once(now=now)
        self.assertGreaterEqual(out["labeled"], 1)

        recent = self.store.recent_events(self.uid, limit=10)
        by_id = {r["id"]: r for r in recent}
        self.assertEqual(by_id[eid_skip]["last_action"], "skip")
        self.assertEqual(by_id[eid_took]["last_action"], "took")

    def test_pending_queue_cap_two(self):
        now = time.time()
        # Three late candidates that need questions
        for i, sym in enumerate(("C1_USDT", "C2_USDT", "C3_USDT")):
            ets = now - 5000
            self.store.log_event(
                self.uid,
                "mover_peak",
                sym,
                "futures",
                ts=ets,
                price=1.0 + i,
                mode="peak",
            )
            self.store.journal_open(self.uid, sym, "futures", entry_avg=1.1)
            conn = self.store._get_conn()
            conn.execute(
                "UPDATE journal_trades SET opened_at = ? WHERE symbol = ?",
                (ets + 4000, sym),
            )

        bridge = EngagementBridge(
            self.store, grace_seconds=3600, max_pending=2, poll_seconds=60
        )
        out = bridge.run_once(now=now)
        pending = self.store.list_pending_questions(self.uid)
        self.assertLessEqual(len(pending), 2)
        self.assertGreaterEqual(out["queued"] + out["skipped_queue"], 1)

        # Fourth enqueue refused
        q3 = self.store.enqueue_pending_question(
            self.uid, question="extra?", event_id=99999, max_open=2
        )
        self.assertIsNone(q3)

    def test_coalesce_same_event(self):
        eid = self.store.log_event(
            self.uid, "mover_peak", "Z_USDT", "futures", price=1.0
        )
        a = self.store.enqueue_pending_question(
            self.uid, question="q1", event_id=eid, max_open=2
        )
        b = self.store.enqueue_pending_question(
            self.uid, question="q1 again", event_id=eid, max_open=2
        )
        self.assertEqual(a, b)
        self.assertEqual(len(self.store.list_pending_questions(self.uid)), 1)

    def test_coalesce_same_symbol_different_events(self):
        e1 = self.store.log_event(
            self.uid, "mover_peak", "SAME_USDT", "futures", price=1.0
        )
        e2 = self.store.log_event(
            self.uid, "mover_step", "SAME_USDT", "futures", price=0.9
        )
        a = self.store.enqueue_pending_question(
            self.uid,
            question="late 1?",
            event_id=e1,
            symbol="SAME_USDT",
            max_open=2,
        )
        b = self.store.enqueue_pending_question(
            self.uid,
            question="late 2?",
            event_id=e2,
            symbol="SAME_USDT",
            max_open=2,
        )
        self.assertEqual(a, b)
        self.assertEqual(len(self.store.list_pending_questions(self.uid)), 1)
        # second different symbol still allowed under cap
        e3 = self.store.log_event(
            self.uid, "mover_peak", "OTHER_USDT", "futures", price=2.0
        )
        c = self.store.enqueue_pending_question(
            self.uid,
            question="other?",
            event_id=e3,
            symbol="OTHER_USDT",
            max_open=2,
        )
        self.assertIsNotNone(c)
        self.assertNotEqual(c, a)
        self.assertEqual(len(self.store.list_pending_questions(self.uid)), 2)

    def test_coach_ask_dedupes_drafts(self):
        import os
        from mexc_bot.webapi import learning_api

        os.environ["ALERTS_FILE"] = str(self.db)
        os.environ["DESK_USER_ID"] = str(self.uid)
        # late action → behavior draft
        eid = self.store.log_event(
            self.uid,
            "mover_peak",
            "FOMO_USDT",
            "futures",
            price=10.0,
            velocity_band="PANIC",
            drop_pct=-8,
        )
        self.store.label_event(eid, self.uid, action="late", source="auto")
        out1 = learning_api.coach_ask("fomo?", user_id=self.uid)
        out2 = learning_api.coach_ask("fomo again?", user_id=self.uid)
        out3 = learning_api.coach_ask("still fomo?", user_id=self.uid)
        drafts = self.store.list_lessons(self.uid, pending_only=True)
        self.assertLessEqual(len(drafts), 1)
        if out1.get("draft_id"):
            self.assertEqual(out1["draft_id"], out2.get("draft_id") or out1["draft_id"])
            self.assertEqual(out1["draft_id"], out3.get("draft_id") or out1["draft_id"])

    def test_teach_approve_lesson(self):
        lid = self.store.teach_lesson(
            self.uid,
            "No full size first layer on GRIND",
            tags=["grind", "size"],
            needs_approval=True,
            source="coach",
            kind="behavior_draft",
        )
        self.assertGreater(lid, 0)
        drafts = self.store.list_lessons(self.uid, pending_only=True)
        self.assertEqual(len(drafts), 1)
        self.assertTrue(self.store.approve_lesson(self.uid, lid))
        approved = self.store.list_lessons(self.uid, approved_only=True)
        self.assertTrue(any(x["id"] == lid for x in approved))
        self.assertEqual(len(self.store.list_lessons(self.uid, pending_only=True)), 0)

    def test_does_not_delete_alerts(self):
        sid = self.alerts.add_alert(self.uid, "BTCUSDT", 65000.0, market="spot")
        self.store.log_event(self.uid, "mover_peak", "BTCUSDT", "spot", price=1.0)
        bridge = EngagementBridge(self.store, grace_seconds=1, max_pending=2)
        bridge.run_once(now=time.time() + 10)
        self.store.teach_lesson(self.uid, "keep alerts", needs_approval=False)
        info = assert_alerts_table_intact(self.db, {sid})
        self.assertEqual(info["alert_count"], 1)

    def test_coach_cites_stats_not_invented_fills(self):
        eid = self.store.log_event(
            self.uid,
            "mover_peak",
            "BTC_USDT",
            "futures",
            price=100.0,
            drop_pct=-9,
            velocity_band="PANIC",
            mode="peak",
        )
        self.store.label_event(
            eid, self.uid, action="took", source="human", confidence=1.0
        )
        self.store.record_outcome(
            eid, 900, max_bounce_pct=4.2, max_dd_pct=-1.0, last_price=104.0
        )
        self.store.teach_lesson(
            self.uid, "Prefer PANIC over GRIND", needs_approval=False
        )
        stats = self.store.learning_stats(self.uid)
        self.assertEqual(stats["took"], 1)
        self.assertEqual(stats["events"], 1)
        recent = self.store.recent_events(self.uid)
        lessons = self.store.list_lessons(self.uid, approved_only=True)
        reply = format_coach_reply(
            "is this panic?",
            recent_events=recent,
            stats=stats,
            lessons=lessons,
        )
        self.assertIn("Memory (from your log): events=1", reply)
        self.assertIn("took=1", reply)
        self.assertIn("Prefer PANIC over GRIND", reply)
        self.assertNotIn("filled at", reply.lower())
        self.assertNotIn("your entry was", reply.lower())

        pulse = format_coach_pulse(stats=stats, lessons=lessons, pending_n=0, drafts_n=0)
        self.assertIn("fires", pulse.lower())
        brief = format_brief(
            recent_events=recent,
            open_trades=[],
            learning_on=True,
            stats=stats,
            lessons=lessons,
        )
        self.assertIn("SESSION BRIEF", brief)

    def test_behavior_codes_allowlist(self):
        for code in (
            "plan_ok",
            "pride",
            "greed",
            "hesitant",
            "fomo",
            "rule_break",
            "false_panic",
            "process_skip",
        ):
            self.assertIn(code, ALLOWED_BEHAVIOR)

    def test_grace_default_one_hour(self):
        self.assertEqual(DEFAULT_GRACE_SECONDS, 3600)


class TestDeskSurfacesStructural(unittest.TestCase):
    def test_html_has_needs_you_and_learning(self):
        html = (ROOT / "mexc_bot/webapi/static/index.html").read_text()
        self.assertIn("ovNeedsYou", html)
        self.assertIn("ovCoachPulse", html)
        self.assertIn("ovBookIntel", html)
        self.assertIn("teachForm", html)
        self.assertIn("coachForm", html)
        self.assertIn("learnPending", html)
        self.assertIn("learnDrafts", html)
        self.assertIn("navLearnBadge", html)
        self.assertIn("navLearning", html)

    def test_js_calls_learning_apis(self):
        js = (ROOT / "mexc_bot/webapi/static/assets/desk.js").read_text()
        self.assertIn("/api/learning", js)
        self.assertIn("/api/learning/teach", js)
        self.assertIn("/api/learning/approve", js)
        self.assertIn("/api/learning/answer", js)
        self.assertIn("Needs you", js)
        self.assertIn("updateLearningNavBadge", js)
        self.assertIn("book_intel", js)
        self.assertIn("ovBookIntel", js)

    def test_no_delete_alerts_in_learning_modules(self):
        for rel in (
            "mexc_bot/learning/store.py",
            "mexc_bot/learning/engagement.py",
            "mexc_bot/webapi/learning_api.py",
            "mexc_bot/coach/engine.py",
        ):
            text = (ROOT / rel).read_text()
            self.assertNotIn("DELETE FROM alerts", text)
            self.assertNotIn("delete from alerts", text.lower())


class TestLearningApiIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "desk.db"
        os.environ["ALERTS_FILE"] = str(self.db)
        os.environ["DESK_USER_ID"] = "8630949601"
        # Reset db module path cache by re-import path
        from mexc_bot.webapi import db as desk_db

        self.desk_db = desk_db
        self.store = EventStore(self.db)
        self.uid = 8630949601

    def tearDown(self):
        self.tmp.cleanup()

    def test_learning_bundle_and_coach_ask(self):
        from mexc_bot.webapi import learning_api

        eid = self.store.log_event(
            self.uid,
            "mover_peak",
            "ETH_USDT",
            "futures",
            price=2000.0,
            velocity_band="PANIC",
            drop_pct=-7,
        )
        self.store.label_event(eid, self.uid, action="took", source="auto_journal")
        self.store.enqueue_pending_question(
            self.uid, question="Was skip intentional?", event_id=None, max_open=2
        )
        bundle = learning_api.learning_bundle(self.uid)
        self.assertIn("needs_you", bundle)
        self.assertIn("coach_pulse", bundle)
        self.assertGreaterEqual(bundle["needs_you"]["count"], 1)
        self.assertEqual(bundle["stats"]["took"], 1)

        out = learning_api.coach_ask("panic setup?", user_id=self.uid)
        self.assertIn("reply", out)
        self.assertTrue(
            "SUPER-AGENT" in out["reply"] or "Judgment" in out["reply"] or "PANIC" in out["reply"]
        )
        teach = learning_api.teach("Test lesson from desk", user_id=self.uid)
        self.assertTrue(teach["ok"])

        # answer pending
        qs = self.store.list_pending_questions(self.uid)
        self.assertTrue(qs)
        ans = learning_api.answer_question(
            qs[0]["id"], answer_text="AFK", action="skip", user_id=self.uid
        )
        self.assertTrue(ans["ok"])


if __name__ == "__main__":
    unittest.main()
