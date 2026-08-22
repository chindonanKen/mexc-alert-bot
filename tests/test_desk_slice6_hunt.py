#!/usr/bin/env python3
"""Slice 6: still-up / already-off hunt lists — names only, no AD."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mexc_bot.webapi.hunt import (  # noqa: E402
    HUNT_PUBLIC_KEYS,
    assemble_hunt_lists,
    classify_week_bars,
    hunt_lists,
    payload_has_price_or_ad,
    public_hunt_row,
    start_sort_key,
)

JS = (ROOT / "mexc_bot/webapi/static/assets/desk.js").read_text()
HTML = (ROOT / "mexc_bot/webapi/static/index.html").read_text()
APP = (ROOT / "mexc_bot/webapi/app.py").read_text()
HUNT = (ROOT / "mexc_bot/webapi/hunt.py").read_text()


def _bars(closes, *, high=None, low=None, vol=10.0):
    """Oldest→newest daily-like bars. highs/lows default around close."""
    out = []
    for i, c in enumerate(closes):
        h = high[i] if high is not None else c * 1.01
        lo = low[i] if low is not None else c * 0.99
        out.append({"h": h, "l": lo, "c": c, "v": vol})
    return out


class TestHuntClassification(unittest.TestCase):
    def test_still_up_near_week_high(self):
        # Surge 50% (10 → 15), last still 14.7 → ~2% off high.
        bars = _bars(
            [10, 12, 14, 15, 14.7],
            high=[10.2, 12.4, 14.5, 15.0, 14.8],
            low=[9.8, 11.5, 13.0, 14.2, 14.4],
        )
        self.assertEqual(classify_week_bars(bars), "still_up")

    def test_already_off_after_surge(self):
        # Same surge, last 12 → 20% off the 15 high.
        bars = _bars(
            [10, 12, 15, 13, 12],
            high=[10.2, 12.5, 15.0, 13.2, 12.3],
            low=[9.7, 11.6, 14.0, 12.4, 11.8],
        )
        self.assertEqual(classify_week_bars(bars), "already_off")

    def test_no_surge_is_not_a_hunt_name(self):
        bars = _bars(
            [10, 10.2, 10.1, 10.3],
            high=[10.2, 10.3, 10.25, 10.35],
            low=[9.9, 10.0, 10.0, 10.1],
        )
        self.assertIsNone(classify_week_bars(bars))


class TestHuntListsNamesOnly(unittest.TestCase):
    def test_new_name_is_unranked(self):
        scored = [
            {
                "symbol": "NEWCOIN_USDT",
                "market": "futures",
                "state": "already_off",
                "surge_pct": 40,
                "dump_pct": 18,
                "volume": 9e6,
                "last": 1.23,
                "price": 1.23,
                "buy": 0.9,
                "ad": 0.8,
            }
        ]
        out = assemble_hunt_lists(scored, marks={})
        self.assertEqual(len(out["already_off"]), 1)
        row = out["already_off"][0]
        self.assertEqual(row["symbol"], "NEWCOIN_USDT")
        self.assertIsNone(row["rank"])
        self.assertEqual(set(row.keys()), HUNT_PUBLIC_KEYS)
        self.assertFalse(payload_has_price_or_ad(out))

    def test_payload_has_no_ad_or_buy_or_last(self):
        still_bars = _bars(
            [8, 10, 12, 11.8],
            high=[8.2, 10.4, 12.0, 11.9],
            low=[7.8, 9.6, 11.2, 11.5],
        )
        off_bars = _bars(
            [8, 12, 11, 9],
            high=[8.3, 12.0, 11.2, 9.4],
            low=[7.7, 10.5, 10.4, 8.8],
        )
        payload = hunt_lists(
            user_id=9,
            candidates=[
                {"symbol": "WAIT_USDT", "market": "futures"},
                {"symbol": "LOOK_USDT", "market": "futures"},
            ],
            bars_by_key={
                ("WAIT_USDT", "futures"): still_bars,
                ("LOOK_USDT", "futures"): off_bars,
            },
            marks={},
        )
        still_syms = [r["symbol"] for r in payload["still_up"]]
        off_syms = [r["symbol"] for r in payload["already_off"]]
        self.assertIn("WAIT_USDT", still_syms)
        self.assertIn("LOOK_USDT", off_syms)
        self.assertFalse(payload_has_price_or_ad(payload))
        blob = str(payload).lower()
        for leak in ("last", "buy", "ad_line", "visual_ad", "price"):
            self.assertNotIn(f"'{leak}'", blob)
        for row in payload["still_up"] + payload["already_off"]:
            self.assertEqual(set(row.keys()), {"symbol", "market", "rank"})
            self.assertIsNone(row["rank"])

    def test_start_rule_does_not_invent_a_rank(self):
        key = start_sort_key(40, 18, 1e7)
        self.assertGreater(key, 0)
        row = public_hunt_row("FOO_USDT", "futures", None)
        self.assertIsNone(row["rank"])
        self.assertNotIn("start_key", row)

    def test_kenneth_mark_is_the_only_rank(self):
        scored = [
            {
                "symbol": "AAA_USDT",
                "market": "futures",
                "state": "still_up",
                "surge_pct": 25,
                "dump_pct": 2,
                "volume": 1,
            },
            {
                "symbol": "BBB_USDT",
                "market": "futures",
                "state": "still_up",
                "surge_pct": 80,
                "dump_pct": 3,
                "volume": 9e9,
            },
        ]
        out = assemble_hunt_lists(
            scored, marks={("AAA_USDT", "futures"): 2}
        )
        by = {r["symbol"]: r["rank"] for r in out["still_up"]}
        self.assertEqual(by["AAA_USDT"], 2)
        self.assertIsNone(by["BBB_USDT"])


class TestHuntMarkTableAdditive(unittest.TestCase):
    def test_mark_then_list_keeps_rank_on_temp_db(self):
        tmp = Path(tempfile.mkdtemp()) / "slice6_hunt.db"
        os.environ["ALERTS_FILE"] = str(tmp.with_suffix(".json"))
        from importlib import reload

        import mexc_bot.webapi.db as desk_db
        import mexc_bot.webapi.hunt as hunt_mod

        reload(desk_db)
        reload(hunt_mod)
        uid = 77
        hunt_mod.ensure_hunt_marks_table()
        hunt_mod.mark_hunt_rank(uid, "MARKED_USDT", "futures", 4)
        marks = hunt_mod.load_hunt_marks(uid)
        self.assertEqual(marks.get(("MARKED_USDT", "futures")), 4)
        payload = hunt_mod.assemble_hunt_lists(
            [
                {
                    "symbol": "MARKED_USDT",
                    "market": "futures",
                    "state": "already_off",
                    "surge_pct": 30,
                    "dump_pct": 15,
                    "volume": 2,
                },
                {
                    "symbol": "FRESH_USDT",
                    "market": "futures",
                    "state": "already_off",
                    "surge_pct": 30,
                    "dump_pct": 16,
                    "volume": 3,
                },
            ],
            marks=marks,
        )
        by = {r["symbol"]: r["rank"] for r in payload["already_off"]}
        self.assertEqual(by["MARKED_USDT"], 4)
        self.assertIsNone(by["FRESH_USDT"])


class TestHuntDeskAndRoutes(unittest.TestCase):
    def test_cache_bust_slicelab6(self):
        self.assertIn("desk.js?v=slicelab7", HTML)
        self.assertIn("desk.css?v=slicelab7", HTML)

    def test_two_lists_on_desk(self):
        self.assertIn("id=\"huntStillUp\"", HTML)
        self.assertIn("id=\"huntAlreadyOff\"", HTML)
        self.assertIn("function loadHunt", JS)
        self.assertIn("still-up", HTML)
        self.assertIn("already-off", HTML)
        self.assertIn("Not an AD", HTML)

    def test_routes_readonly_get_no_query_token(self):
        self.assertIn('@app.get("/api/hunt")', APP)
        self.assertIn('@app.post("/api/hunt/mark")', APP)
        chunk = APP[APP.find('@app.get("/api/hunt")') : APP.find('@app.get("/api/watchlist")')]
        self.assertNotIn("token: Optional[str] = Query", chunk)
        self.assertNotIn("?token=", chunk)
        self.assertIn("CREATE TABLE IF NOT EXISTS desk_hunt_marks", HUNT)
        self.assertNotIn("DROP TABLE", HUNT)
        self.assertNotIn("DELETE FROM", HUNT)

    def test_slices_1_to_5_still_present(self):
        self.assertIn("function applySelectedSymbol", JS)
        self.assertIn("function playAlarmSound", JS)
        self.assertIn("async function jumpToLesson", JS)
        self.assertIn("Teach-this-fire", JS)
        self.assertIn("function setSelectedSymbol", JS)


if __name__ == "__main__":
    unittest.main()
