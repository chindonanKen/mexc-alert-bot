"""P1: symbol normalize, incident anchors, four case buckets."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from mexc_bot.learning.chip_honesty import (
    OWNER_LESSON_CHIPS,
    sanitize_process_chips,
)
from mexc_bot.learning.cases import freeze_case
from mexc_bot.learning.incident import build_incident, enrich_lesson_row, incident_tags
from mexc_bot.learning.store import EventStore
from mexc_bot.learning.symbols import (
    learning_base,
    normalize_learning_symbol,
    rewrite_sym_tags,
)


class TestSymbols(unittest.TestCase):
    def test_same_coin_collapses(self):
        self.assertEqual(learning_base("HFTUSDT"), "HFT")
        self.assertEqual(learning_base("HFT_USDT"), "HFT")
        self.assertEqual(learning_base("HFT"), "HFT")
        self.assertEqual(learning_base("HFI"), "HFT")
        self.assertEqual(normalize_learning_symbol("HFT_USDT", "spot"), "HFTUSDT")
        self.assertEqual(normalize_learning_symbol("HFTUSDT", "futures"), "HFT_USDT")
        self.assertEqual(
            normalize_learning_symbol("AXTISTOCK_USDT", "futures"), "AXTISTOCK_USDT"
        )

    def test_rewrite_sym_tags(self):
        tags = rewrite_sym_tags(
            ["plan_ok", "sym:HFT_USDT", "mkt:spot", "ev:1"], "spot"
        )
        self.assertIn("sym:HFTUSDT", tags)
        self.assertIn("base:HFT", tags)
        self.assertNotIn("sym:HFT_USDT", tags)


class TestChipHonesty(unittest.TestCase):
    def test_no_dual_ad(self):
        chips = sanitize_process_chips(["plan_ok", "ad_met", "ad_missed"])
        self.assertIn("ad_missed", chips)
        self.assertNotIn("ad_met", chips)

    def test_owner_map_covers_first_lessons(self):
        self.assertIn(22, OWNER_LESSON_CHIPS)
        self.assertIn("ad_met", OWNER_LESSON_CHIPS[22])


class TestIncidentAndFreeze(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = EventStore(Path(self.tmp.name) / "c.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_incident_tags(self):
        inc = build_incident(
            incident_ts=1700000000.0, incident_price=0.023, event_id=9
        )
        tags = incident_tags(inc)
        self.assertTrue(any(t.startswith("ts:") for t in tags))
        self.assertTrue(any(t.startswith("px:") for t in tags))

    def test_freeze_normalizes_and_buckets(self):
        eid = self.store.log_event(
            1,
            "mover_peak",
            "HFTUSDT",
            "futures",
            ts=1700000100.0,
            price=0.02,
            ref_price=0.03,
            drop_pct=-33,
            velocity_band="PANIC",
        )
        fake = {
            "ok": True,
            "band": "PANIC",
            "dd_pct": 33,
            "ad_zone": "at_ad",
            "vol_flag": "surge",
            "ad_ready": True,
            "setup_prior": 0.8,
        }
        with patch(
            "mexc_bot.learning.cases.build_features_for_event", return_value=fake
        ):
            view = freeze_case(
                self.store,
                1,
                symbol="HFTUSDT",
                market="futures",
                event_id=eid,
                fire_ts=1700000100.0,
                fire_price=0.02,
                ref_price=0.03,
                chips=["plan_ok", "ad_met"],
                note="good AD",
                source="teach",
            )
        self.assertEqual(view.get("symbol"), "HFT_USDT")
        self.assertEqual(view.get("base"), "HFT")
        self.assertEqual(view.get("incident_ts"), 1700000100.0)
        self.assertEqual(view.get("incident_price"), 0.02)
        self.assertEqual(view.get("base"), "HFT")
        self.assertEqual(view.get("incident_ts"), 1700000100.0)

    def test_normalize_index(self):
        lid = self.store.teach_lesson(
            1,
            "test",
            tags=["sym:HFT_USDT", "mkt:spot", "plan_ok", "ad_met", "ad_missed"],
        )
        self.assertGreater(lid, 0)
        out = self.store.normalize_learning_index(1)
        self.assertGreaterEqual(out.get("lessons_rewritten", 0), 1)
        row = self.store.get_lesson(1, lid)
        en = enrich_lesson_row(row)
        self.assertEqual(en.get("symbol_norm"), "HFTUSDT")
        self.assertEqual(en.get("base"), "HFT")
        self.assertIsNotNone(en.get("incident_ts"))
        # honesty: not both ad chips
        chips = [t for t in (en.get("tags") or []) if ":" not in str(t)]
        self.assertFalse("ad_met" in chips and "ad_missed" in chips)


class TestLessonBucketEdit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = EventStore(Path(self.tmp.name) / "c.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_patch_behaviors_persists_bucket(self):
        from mexc_bot.webapi import learning_api as la

        lid = self.store.teach_lesson(
            1,
            "BTW late vol",
            tags=["sym:BTWUSDT", "mkt:spot", "plan_ok", "ad_met", "base:BTW"],
        )
        # Point event_store + uid at this temp DB
        la.event_store = lambda: self.store  # type: ignore
        la.uid_or_raise = lambda: 1  # type: ignore
        out = la.update_lesson(
            lid,
            text="BTW late vol — press size",
            behaviors=["plan_ok", "ad_met", "hesitant", "ad_press"],
        )
        self.assertTrue(out.get("ok"))
        row = self.store.get_lesson(1, lid)
        tags = json.loads(row["tags_json"] or "[]")
        self.assertIn("bucket:ad_press", tags)
        self.assertIn("plan_ok", tags)
        self.assertIn("ad_met", tags)
        # change bucket
        out2 = la.update_lesson(
            lid, behaviors=["plan_ok", "ad_met", "ad_wait"]
        )
        self.assertTrue(out2.get("ok"))
        tags2 = json.loads(self.store.get_lesson(1, lid)["tags_json"] or "[]")
        self.assertIn("bucket:ad_wait", tags2)
        self.assertNotIn("bucket:ad_press", tags2)


if __name__ == "__main__":
    unittest.main()
