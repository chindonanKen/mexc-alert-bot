#!/usr/bin/env python3
"""Prove Slate PnL page structure (G1–G6). Leftover remaining-cost math is untouched."""

from __future__ import annotations

import os
import sys
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mexc_bot.webapi.pnl import (
    MANILA,
    _window_cutoff,
    build_pnl_summary,
    chip_date_span,
    display_name,
    group_closed_rows,
    hold_days,
    in_closed_window,
    manila_day_end_ts,
    manila_day_start_ts,
    parse_manila_date,
    resolve_group_by,
)


def _closed(
    symbol,
    *,
    ts,
    bought=100.0,
    sold=110.0,
    real=10.0,
    market="spot",
    opened_at=None,
    **extra,
):
    row = {
        "symbol": symbol,
        "market": market,
        "status": "closed",
        "is_open": False,
        "opened_at": opened_at if opened_at is not None else ts - 2 * 86400,
        "closed_at": ts,
        "bought_usd": bought,
        "sold_usd": sold,
        "realized_pnl_usd": real,
        "remaining_cost_usd": 0.0,
        "buy_orders": extra.pop("buy_orders", []),
        "sell_orders": extra.pop("sell_orders", []),
    }
    row.update(extra)
    return row


def _open(symbol, *, leftover=100.0, rem_avg=0.2, qty=500.0, **extra):
    row = {
        "symbol": symbol,
        "market": extra.pop("market", "spot"),
        "status": "open",
        "is_open": True,
        "size_remaining": qty,
        "bought_usd": leftover + extra.get("sold_usd", 0.0),
        "sold_usd": extra.pop("sold_usd", 0.0),
        "remaining_cost_usd": leftover,
        "remaining_avg": rem_avg,
        "leftover_avg": rem_avg,
        "entry_avg": rem_avg,
        "entry_display": rem_avg,
        "realized_pnl_usd": 0.0,
        "remaining_mark_usd": leftover + 10,
        "upnl_usd_est": 10.0,
        "free_coins": extra.pop("free_coins", False),
        "is_hold": extra.pop("is_hold", False),
        "position_book": extra.pop("position_book", "ad"),
    }
    if row["is_hold"]:
        row["position_book"] = "hold"
    row.update(extra)
    return row


class _Book:
    def __init__(self, rows):
        self.rows = rows

    def __call__(self, user_id, include_closed=True, closed_limit=0):
        return list(self.rows)


class TestSlateWindowMath(unittest.TestCase):
    def test_g5_real_in_out_sum_visible_closed_only(self):
        now = time.time()
        old = now - 80 * 86400
        rows = [
            _open("PRLUSDT", leftover=2740.7733, rem_avg=0.203105, qty=13494.37),
            _closed("NEWUSDT", ts=now - 3600, bought=200, sold=260, real=60),
            _closed("OLDUSDT", ts=old, bought=50, sold=40, real=-10),
        ]
        with patch("mexc_bot.webapi.pnl.list_position_entities", side_effect=_Book(rows)):
            all_sum = build_pnl_summary(1, window="all")
            d30 = build_pnl_summary(1, window="30d")

        self.assertEqual(all_sum["realized"]["closed_n"], 2)
        self.assertEqual(all_sum["realized"]["closed_all_n"], 2)
        self.assertAlmostEqual(all_sum["realized"]["pnl_usd"], 50.0, places=2)
        self.assertAlmostEqual(all_sum["realized"]["in_usd"], 250.0, places=2)
        self.assertAlmostEqual(all_sum["realized"]["out_usd"], 300.0, places=2)
        self.assertAlmostEqual(
            all_sum["realized"]["in_usd"],
            sum(h["bought_usd"] for h in all_sum["closed_history"]),
            places=2,
        )
        self.assertAlmostEqual(
            all_sum["realized"]["pnl_usd"],
            sum(h["realized_pnl_usd"] for h in all_sum["closed_history"]),
            places=2,
        )
        # Open leftover $ must not enter Real
        self.assertNotAlmostEqual(all_sum["realized"]["pnl_usd"], 50.0 + 2740.7733, places=1)

        self.assertEqual(d30["realized"]["closed_n"], 1)
        self.assertEqual(len(d30["closed_history"]), 1)
        self.assertEqual(d30["closed_history"][0]["symbol"], "NEWUSDT")
        self.assertAlmostEqual(d30["realized"]["pnl_usd"], 60.0, places=2)
        self.assertAlmostEqual(d30["realized"]["in_usd"], 200.0, places=2)
        self.assertAlmostEqual(d30["realized"]["out_usd"], 260.0, places=2)
        self.assertEqual(d30["realized"]["closed_all_n"], 2)

    def test_g2_open_book_ignores_date_window(self):
        now = time.time()
        rows = [
            _open("PRLUSDT", leftover=2740.7733, rem_avg=0.203105, qty=13494.37),
            _closed("NEWUSDT", ts=now - 3600),
        ]
        with patch("mexc_bot.webapi.pnl.list_position_entities", side_effect=_Book(rows)):
            empty = build_pnl_summary(1, window="custom", from_date="2010-01-01", to_date="2010-01-02")
        self.assertEqual(empty["realized"]["closed_n"], 0)
        self.assertEqual(len(empty["closed_history"]), 0)
        self.assertEqual(len(empty["open_book"]), 1)
        prl = empty["open_book"][0]
        self.assertAlmostEqual(prl["remaining_avg"], 0.203105, places=6)
        self.assertAlmostEqual(prl["remaining_cost_usd"], 2740.7733, places=4)
        leftover = (prl["bought_usd"] - prl["sold_usd"]) / prl["size_remaining"]
        self.assertAlmostEqual(leftover, 0.203105, places=6)

    def test_g6_manila_from_to_recompute_closed(self):
        # 2026-08-10 12:00 Manila and 2026-08-25 12:00 Manila
        a = datetime(2026, 8, 10, 12, 0, tzinfo=MANILA).timestamp()
        b = datetime(2026, 8, 25, 12, 0, tzinfo=MANILA).timestamp()
        rows = [
            _closed("EARLYUSDT", ts=a, bought=10, sold=12, real=2),
            _closed("LATEUSDT", ts=b, bought=30, sold=40, real=10),
        ]
        with patch("mexc_bot.webapi.pnl.list_position_entities", side_effect=_Book(rows)):
            mid = build_pnl_summary(1, window="custom", from_date="2026-08-20", to_date="2026-08-27")
            early = build_pnl_summary(1, window="custom", from_date="2026-08-01", to_date="2026-08-15")
            all_sum = build_pnl_summary(1, window="all")
        self.assertEqual([r["symbol"] for r in mid["closed_history"]], ["LATEUSDT"])
        self.assertAlmostEqual(mid["realized"]["pnl_usd"], 10.0, places=2)
        self.assertEqual([r["symbol"] for r in early["closed_history"]], ["EARLYUSDT"])
        self.assertEqual(len(all_sum["closed_history"]), 2)
        self.assertEqual(all_sum["timezone"], "Asia/Manila")

    def test_g6_all_keeps_full_history_225(self):
        now = time.time()
        rows = [_closed(f"C{i}USDT", ts=now - i * 86400, real=1.0) for i in range(225)]
        with patch("mexc_bot.webapi.pnl.list_position_entities", side_effect=_Book(rows)):
            all_sum = build_pnl_summary(1, window="all")
            d7 = build_pnl_summary(1, window="7d")
        self.assertEqual(len(all_sum["closed_history"]), 225)
        self.assertEqual(all_sum["realized"]["closed_all_n"], 225)
        self.assertEqual(all_sum["realized"]["closed_n"], 225)
        self.assertLess(d7["realized"]["closed_n"], 225)
        self.assertEqual(d7["realized"]["closed_all_n"], 225)

    def test_empty_window_does_not_drop_all_n(self):
        ts = datetime(2026, 1, 1, 12, 0, tzinfo=MANILA).timestamp()
        rows = [_closed("OLDUSDT", ts=ts)]
        with patch("mexc_bot.webapi.pnl.list_position_entities", side_effect=_Book(rows)):
            d = build_pnl_summary(1, window="custom", from_date="2026-08-01", to_date="2026-08-07")
        self.assertEqual(d["closed_history"], [])
        self.assertEqual(d["realized"]["closed_n"], 0)
        self.assertEqual(d["realized"]["closed_all_n"], 1)


class TestSlateOpenBookSort(unittest.TestCase):
    def test_g2_sort_ad_then_free_then_hold(self):
        rows = [
            _open("HOLDUSDT", leftover=50, is_hold=True, position_book="hold"),
            _open("FREEUSDT", leftover=80, free_coins=True),
            _open("ADUSDT", leftover=20),
            _open("AD2USDT", leftover=90),
        ]
        with patch("mexc_bot.webapi.pnl.list_position_entities", side_effect=_Book(rows)):
            d = build_pnl_summary(1, window="all")
        names = [p["name"] for p in d["open_book"]]
        self.assertEqual(names, ["AD2", "AD", "FREE", "HOLD"])
        self.assertTrue(d["open_book"][2]["free_coins"])
        self.assertTrue(d["open_book"][3]["is_hold"])
        self.assertEqual(d["open_book"][0]["book_label"], "SPOT")

    def test_g2_one_leftover_price_one_row_passthrough(self):
        rows = [
            _open(
                "PRLUSDT",
                leftover=2740.7733,
                rem_avg=0.203105,
                qty=13494.37,
                sold_usd=0.0,
            )
        ]
        with patch("mexc_bot.webapi.pnl.list_position_entities", side_effect=_Book(rows)):
            d = build_pnl_summary(1, window="all")
        self.assertEqual(len(d["open_book"]), 1)
        p = d["open_book"][0]
        self.assertEqual(p["name"], "PRL")
        self.assertAlmostEqual(p["remaining_avg"], 0.203105, places=6)
        self.assertAlmostEqual(p["remaining_cost_usd"], 2740.7733, places=4)
        self.assertAlmostEqual(p["remaining_mark_usd"], 2750.7733, places=4)

    def test_g2_left_dollar_is_mark_avg_stays_leftover_cost(self):
        """LEFT $ = remaining_mark_usd. AVG = remaining_avg leftover-cost."""
        rows = [
            _open(
                "SMALLCOSTUSDT",
                leftover=10.0,
                rem_avg=0.10,
                qty=100.0,
            ),
            _open(
                "BIGCOSTUSDT",
                leftover=900.0,
                rem_avg=0.90,
                qty=1000.0,
            ),
        ]
        # Invert mark vs cost so leftover rank follows mark, not cost.
        rows[0]["remaining_mark_usd"] = 500.0
        rows[1]["remaining_mark_usd"] = 50.0
        with patch("mexc_bot.webapi.pnl.list_position_entities", side_effect=_Book(rows)):
            d = build_pnl_summary(1, window="all")
        names = [p["name"] for p in d["open_book"]]
        self.assertEqual(names, ["SMALLCOST", "BIGCOST"])
        small, big = d["open_book"]
        self.assertAlmostEqual(small["remaining_mark_usd"], 500.0, places=2)
        self.assertAlmostEqual(small["remaining_cost_usd"], 10.0, places=2)
        self.assertAlmostEqual(small["remaining_avg"], 0.10, places=6)
        self.assertAlmostEqual(big["remaining_mark_usd"], 50.0, places=2)
        self.assertAlmostEqual(big["remaining_cost_usd"], 900.0, places=2)
        self.assertAlmostEqual(big["remaining_avg"], 0.90, places=6)
        leftover = (small["bought_usd"] - small["sold_usd"]) / small["size_remaining"]
        self.assertAlmostEqual(leftover, small["remaining_avg"], places=6)


class TestSlateClosedGroups(unittest.TestCase):
    def test_g3_month_groups_when_all(self):
        jul = datetime(2026, 7, 20, 12, 0, tzinfo=MANILA).timestamp()
        aug = datetime(2026, 8, 10, 12, 0, tzinfo=MANILA).timestamp()
        rows = [
            _closed("AUG1USDT", ts=aug, bought=10, sold=15, real=5),
            _closed("AUG2USDT", ts=aug + 86400, bought=20, sold=30, real=10),
            _closed("JULUSDT", ts=jul, bought=8, sold=6, real=-2),
        ]
        with patch("mexc_bot.webapi.pnl.list_position_entities", side_effect=_Book(rows)):
            d = build_pnl_summary(1, window="all")
        self.assertEqual(d["group_by"], "month")
        labels = [g["label"] for g in d["closed_groups"]]
        self.assertEqual(labels[0], "AUG 2026")
        self.assertEqual(labels[1], "JUL 2026")
        aug_g = d["closed_groups"][0]
        self.assertEqual(aug_g["closed_n"], 2)
        self.assertAlmostEqual(aug_g["realized_usd"], 15.0, places=2)
        self.assertAlmostEqual(aug_g["in_usd"], 30.0, places=2)
        self.assertAlmostEqual(aug_g["out_usd"], 45.0, places=2)

    def test_g3_week_groups_when_7d_or_30d(self):
        self.assertEqual(resolve_group_by("7d", None, None), "week")
        self.assertEqual(resolve_group_by("30d", None, None), "week")
        self.assertEqual(resolve_group_by("all", None, None), "month")
        from datetime import date

        self.assertEqual(
            resolve_group_by("custom", date(2026, 7, 1), date(2026, 8, 20)),
            "month",
        )
        self.assertEqual(
            resolve_group_by("custom", date(2026, 8, 20), date(2026, 8, 27)),
            "week",
        )

        mon = datetime(2026, 8, 24, 12, 0, tzinfo=MANILA).timestamp()  # Mon
        sun = datetime(2026, 8, 16, 12, 0, tzinfo=MANILA).timestamp()  # prior week
        rows = [_closed("AUSDT", ts=mon), _closed("BUSDT", ts=sun)]
        groups = group_closed_rows(
            [
                {
                    "closed_at": mon,
                    "bought_usd": 1,
                    "sold_usd": 2,
                    "realized_pnl_usd": 1,
                },
                {
                    "closed_at": sun,
                    "bought_usd": 3,
                    "sold_usd": 3,
                    "realized_pnl_usd": 0,
                },
            ],
            "week",
        )
        self.assertEqual(len(groups), 2)
        self.assertGreater(groups[0]["key"], groups[1]["key"])
        self.assertIn("–", groups[0]["label"])

    def test_g3_closed_row_has_fills_and_days_no_need_for_notes(self):
        ts = datetime(2026, 8, 20, 12, 0, tzinfo=MANILA).timestamp()
        rows = [
            _closed(
                "FOOUSDT",
                ts=ts,
                opened_at=ts - 3 * 86400,
                buy_orders=[{"side": "buy", "price": 1.0, "qty": 2, "quote_qty": 2, "ts": ts}],
                sell_orders=[{"side": "sell", "price": 1.2, "qty": 2, "quote_qty": 2.4, "ts": ts}],
            )
        ]
        with patch("mexc_bot.webapi.pnl.list_position_entities", side_effect=_Book(rows)):
            d = build_pnl_summary(1, window="all")
        row = d["closed_history"][0]
        self.assertEqual(row["name"], "FOO")
        self.assertEqual(row["book_label"], "SPOT")
        self.assertAlmostEqual(row["hold_days"], 3.0, places=2)
        self.assertEqual(len(row["buy_orders"]), 1)
        self.assertEqual(len(row["sell_orders"]), 1)
        self.assertNotIn("notes", row)


class TestSlateApiAndDesk(unittest.TestCase):
    def test_api_from_to_and_default_all(self):
        os.environ.pop("DESK_API_TOKEN", None)
        os.environ.pop("WEB_UI_TOKEN", None)
        from fastapi.testclient import TestClient
        from mexc_bot.webapi.app import create_app

        a = datetime(2026, 8, 10, 12, 0, tzinfo=MANILA).timestamp()
        b = datetime(2026, 8, 25, 12, 0, tzinfo=MANILA).timestamp()
        rows = [
            _open("PRLUSDT", leftover=2740.7733, rem_avg=0.203105, qty=13494.37),
            _closed("EARLYUSDT", ts=a, bought=10, sold=12, real=2),
            _closed("LATEUSDT", ts=b, bought=30, sold=40, real=10),
        ]
        with patch(
            "mexc_bot.webapi.pnl.list_position_entities", side_effect=_Book(rows)
        ), patch("mexc_bot.webapi.app.db.default_user_id", return_value=1):
            client = TestClient(create_app())
            bare = client.get("/api/pnl")
            custom = client.get("/api/pnl?from=2026-08-20&to=2026-08-27")
        self.assertEqual(bare.status_code, 200)
        self.assertEqual(bare.json()["window"], "all")
        self.assertEqual(len(bare.json()["closed_history"]), 2)
        self.assertEqual(len(custom.json()["closed_history"]), 1)
        self.assertEqual(custom.json()["closed_history"][0]["symbol"], "LATEUSDT")
        self.assertAlmostEqual(custom.json()["realized"]["pnl_usd"], 10.0, places=2)
        self.assertAlmostEqual(custom.json()["realized"]["in_usd"], 30.0, places=2)
        self.assertEqual(len(custom.json()["open_book"]), 1)

    def test_g1_g4_desk_markup_is_slate(self):
        js = (ROOT / "mexc_bot/webapi/static/assets/desk.js").read_text()
        html = (ROOT / "mexc_bot/webapi/static/index.html").read_text()
        css = (ROOT / "mexc_bot/webapi/static/assets/desk.css").read_text()
        self.assertIn('id="pnlFrom"', html)
        self.assertIn('id="pnlTo"', html)
        self.assertIn('placeholder="YYYY-MM-DD"', html)
        self.assertIn("_pnlValidYmd", js)
        self.assertIn('id="pnlWindowSum"', html)
        self.assertIn('data-pnl-win="7d"', html)
        self.assertIn('data-pnl-win="30d"', html)
        self.assertIn('data-pnl-win="all"', html)
        self.assertIn("applyPnlChip", js)
        self.assertIn("applyPnlCustomDates", js)
        self.assertIn("Asia/Manila", js)
        self.assertIn("No closes in this window", js)
        self.assertIn("LEFT $", js)
        self.assertIn("_pnlUsd(p.remaining_mark_usd", js)
        open_row = js[js.index("function _pnlOpenRowHtml") : js.index("function _pnlGroupHead")]
        self.assertIn("remaining_mark_usd", open_row)
        self.assertNotIn("remaining_cost_usd", open_row)
        self.assertIn("remaining_avg", open_row)
        self.assertIn("Open Book", js)
        self.assertIn("pnl-close-cols", js)
        self.assertIn("CLOSED", js)
        self.assertNotIn("By book · extremes", js)
        # Positions page may still say Free bags; PnL renderer must not.
        slate = js[js.index("function _pnlFillsHtml") : js.index("async function loadPositions")]
        self.assertNotIn("Free bags", slate)
        self.assertNotIn("pnl-hero", slate)
        self.assertNotIn("By book", slate)
        self.assertNotIn("WIN", slate)
        self.assertNotIn("MISS", slate)
        self.assertNotIn("EXCH", slate)
        self.assertIn("pnl-fills", slate)
        self.assertIn("_pnlFillsHtml", js)
        self.assertIn("pnl-window-sum", css)
        self.assertIn("pnl-group-h", css)
        self.assertIn("position: sticky", css)

    def test_chip_dates_write_manila_span(self):
        today = datetime(2026, 8, 27, tzinfo=MANILA).date()
        f7, t7 = chip_date_span("7d", today)
        f30, t30 = chip_date_span("30d", today)
        fall, tall = chip_date_span("all", today)
        self.assertEqual(str(t7), "2026-08-27")
        self.assertEqual(str(f7), "2026-08-21")
        self.assertEqual(str(f30), "2026-07-29")
        self.assertIsNone(fall)
        self.assertIsNone(tall)

    def test_display_name_and_hold_days(self):
        self.assertEqual(display_name("PRLUSDT"), "PRL")
        self.assertEqual(display_name("ONG_USDT"), "ONG")
        self.assertEqual(display_name("TSLASTOCK_USDT"), "TSLA")
        self.assertAlmostEqual(hold_days(100.0, 100.0 + 2.5 * 86400), 2.5, places=2)

    def test_unknown_window_still_all(self):
        self.assertIsNone(_window_cutoff("all"))
        self.assertIsNone(_window_cutoff("weird"))
        self.assertIsNone(_window_cutoff("custom"))

    def test_in_window_inclusive_manila_days(self):
        ts = datetime(2026, 8, 27, 0, 30, tzinfo=MANILA).timestamp()
        e = {"closed_at": ts}
        self.assertTrue(
            in_closed_window(
                e,
                from_d=parse_manila_date("2026-08-27"),
                to_d=parse_manila_date("2026-08-27"),
                cutoff=None,
            )
        )
        self.assertFalse(
            in_closed_window(
                e,
                from_d=parse_manila_date("2026-08-28"),
                to_d=parse_manila_date("2026-08-28"),
                cutoff=None,
            )
        )
        start = manila_day_start_ts(parse_manila_date("2026-08-27"))
        end = manila_day_end_ts(parse_manila_date("2026-08-27"))
        self.assertLessEqual(start, ts)
        self.assertLess(ts, end)


if __name__ == "__main__":
    unittest.main()
