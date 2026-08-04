"""P1 case factory — structured freeze + store."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from mexc_bot.learning.cases import case_public_view, freeze_case
from mexc_bot.learning.store import EventStore


class TestP1Cases(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = EventStore(Path(self.tmp.name) / "c.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_freeze_on_event_id(self):
        eid = self.store.log_event(
            1,
            "mover_peak",
            "FOO_USDT",
            "futures",
            ts=time.time(),
            price=1.0,
            ref_price=1.2,
            drop_pct=-16.6,
            velocity_band="PANIC",
        )
        self.assertGreater(eid, 0)
        fake_feats = {
            "ok": True,
            "band": "PANIC",
            "dd_pct": 16.6,
            "ad_zone": "at_ad",
            "ad_depth_ratio": 1.05,
            "vol_flag": "surge",
            "vol_ratio": 1.8,
            "setup_prior": 0.72,
            "vel_pct_min": 2.1,
        }
        with patch(
            "mexc_bot.learning.cases.build_features_for_event",
            return_value=fake_feats,
        ):
            view = freeze_case(
                self.store,
                1,
                symbol="FOO_USDT",
                market="futures",
                event_id=eid,
                fire_ts=time.time(),
                fire_price=1.0,
                ref_price=1.2,
                drop_pct=-16.6,
                velocity_band="PANIC",
                chips=["plan_ok", "ad_met"],
                note="test note",
                source="teach",
            )
        self.assertTrue(view.get("ok"))
        self.assertEqual(view.get("ad_zone"), "at_ad")
        self.assertEqual(view.get("freeze"), "ok")
        row = self.store.get_setup_case(1, event_id=eid)
        self.assertIsNotNone(row)
        pub = case_public_view(row)
        self.assertIn("plan_ok", pub.get("chips") or [])

    def test_upsert_same_event(self):
        eid = self.store.log_event(
            1, "mover_peak", "BAR_USDT", "futures", price=2.0, drop_pct=-10
        )
        with patch(
            "mexc_bot.learning.cases.build_features_for_event",
            return_value={"ok": False, "error": "no klines"},
        ):
            a = freeze_case(
                self.store,
                1,
                symbol="BAR_USDT",
                market="futures",
                event_id=eid,
                fire_price=2.0,
                drop_pct=-10,
                source="fire",
            )
            b = freeze_case(
                self.store,
                1,
                symbol="BAR_USDT",
                market="futures",
                event_id=eid,
                fire_price=2.0,
                drop_pct=-10,
                chips=["ad_missed"],
                note="never hit zone",
                lesson_id=9,
                source="teach",
            )
        self.assertEqual(a.get("id"), b.get("id"))
        row = self.store.get_setup_case(1, event_id=eid)
        self.assertEqual(row.get("lesson_id"), 9)


if __name__ == "__main__":
    unittest.main()
