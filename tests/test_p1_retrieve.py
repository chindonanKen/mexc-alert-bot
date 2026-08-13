#!/usr/bin/env python3
"""P1: teach tags + nearest-case scoring."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mexc_bot.learning.chart_features import (
    apply_teach_feature_tags,
    _factor_alignment,
)
from mexc_bot.learning.retrieve import score_case, similar_cases


class TestTeachTags(unittest.TestCase):
    def test_parse_note_and_chips(self) -> None:
        feats = apply_teach_feature_tags(
            {},
            chips=["tf:4h", "plan_ok"],
            note="regime:new_low reds:4 vol:climax skip first dump",
        )
        self.assertEqual(feats["tf_taught"], "4h")
        self.assertEqual(feats["regime_taught"], "new_low")
        self.assertEqual(feats["reds_taught"], 4)
        self.assertEqual(feats["vol_taught"], "climax")


class TestFactorAlignment(unittest.TestCase):
    def test_size_scales_with_stack(self) -> None:
        fat = _factor_alignment(
            timing={
                "ad_met": True,
                "vol_panic_on_that_bar": True,
                "red_streak": 4,
            },
            band="PANIC",
            regime="familiar",
            vol_flag="expand",
            heat_breadth=5,
        )
        thin = _factor_alignment(
            timing={"ad_met": False, "vol_panic_on_that_bar": False, "red_streak": 1},
            band="GRIND",
            regime="unknown",
            vol_flag="dry",
            heat_breadth=0,
        )
        self.assertIn(fat["size_hint"], ("standard", "press"))
        self.assertIn(thin["size_hint"], ("lean", "pass"))
        self.assertGreater(fat["yes_count"], thin["yes_count"])


class TestRetrieve(unittest.TestCase):
    def test_same_base_ranks_higher(self) -> None:
        q = {
            "symbol": "BLUAI_USDT",
            "market": "futures",
            "bucket": "ad_take",
            "band": "PANIC",
            "dd_pct": 9.0,
            "regime_guess": "familiar",
            "features": {"ad_by_tf": [{"tf": "15m", "ad_ready": True}]},
        }
        same = {
            "id": 2,
            "symbol": "BLUAI_USDT",
            "market": "futures",
            "bucket": "ad_take",
            "band": "PANIC",
            "dd_pct": 8.5,
            "regime_guess": "familiar",
            "features": {"ad_by_tf": [{"tf": "15m", "ad_ready": True}]},
        }
        other = {
            "id": 3,
            "symbol": "DODO_USDT",
            "market": "futures",
            "bucket": "ad_skip",
            "band": "GRIND",
            "dd_pct": 3.0,
            "regime_guess": "new_low",
            "features": {},
        }
        self.assertGreater(score_case(q, same), score_case(q, other))
        neigh = similar_cases([same, other], {**q, "id": 1}, k=2)
        self.assertEqual(neigh[0]["id"], 2)


if __name__ == "__main__":
    unittest.main()
