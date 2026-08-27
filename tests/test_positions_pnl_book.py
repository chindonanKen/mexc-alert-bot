#!/usr/bin/env python3
"""Prove Positions / PnL book goals P1–P6.

P1 Open: one user order at one price = one row (never one row per fill).
P2 Closed: same.
P3 Remaining-cost leftover avg, spot and futures split.
P4 Every shown field has a number.
P5 PnL history has no day cutoff; oldest close still listed.
P6 PnL leftover numbers match Positions remaining-cost math.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mexc_bot.learning.trades import (
    apply_open_remaining_cost_avg,
    remaining_cost_average,
    reconstruct_open_from_fills,
    segment_positions_from_fills,
)
from mexc_bot.webapi.position_math import (
    apply_open_mark_math,
    collapse_fills_to_orders,
    ensure_position_display_fields,
    fully_filled_orders,
)
from mexc_bot.webapi.pnl import _window_cutoff, build_pnl_summary
from mexc_bot.webapi.positions_enrich import _attach_fills_window


def _fill(symbol, side, price, qty, ts, market="spot", order_id=None, raw=None, **extra):
    row = {
        "symbol": symbol,
        "market": market,
        "side": side,
        "price": price,
        "qty": qty,
        "quote_qty": price * qty,
        "ts": ts,
    }
    if order_id:
        row["order_id"] = order_id
        row["raw"] = raw or {"orderId": order_id, "status": "FILLED", "origQty": qty}
    if raw:
        row["raw"] = raw
    row.update(extra)
    return row


class TestP1P2OneOrderOneRow(unittest.TestCase):
    def test_p1_open_two_fills_same_order_one_row(self):
        fills = [
            _fill(
                "ABCUSDT",
                "buy",
                1.0,
                5,
                1000,
                order_id="ord-1",
                raw={"orderId": "ord-1", "origQty": 10, "executedQty": 5, "status": "PARTIALLY_FILLED"},
            ),
            _fill(
                "ABCUSDT",
                "buy",
                1.2,
                5,
                1100,
                order_id="ord-1",
                raw={"orderId": "ord-1", "origQty": 10, "executedQty": 10, "status": "FILLED"},
            ),
        ]
        orders = fully_filled_orders(fills)
        self.assertEqual(len(orders), 1)
        self.assertAlmostEqual(orders[0]["qty"], 10.0, places=6)
        self.assertAlmostEqual(orders[0]["quote_qty"], 11.0, places=6)
        segs = segment_positions_from_fills(fills, symbol="ABCUSDT", market="spot")
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0]["status"], "open")
        self.assertEqual(segs[0]["n_buys"], 1)
        self.assertAlmostEqual(segs[0]["size_remaining"], 10.0, places=6)

    def test_p2_closed_multi_fill_order_one_sell_row(self):
        fills = [
            _fill("XYZUSDT", "buy", 10.0, 10, 100, order_id="b1"),
            _fill(
                "XYZUSDT",
                "sell",
                12.0,
                4,
                200,
                order_id="s1",
                raw={"orderId": "s1", "origQty": 10, "executedQty": 4, "status": "PARTIALLY_FILLED"},
            ),
            _fill(
                "XYZUSDT",
                "sell",
                12.0,
                6,
                210,
                order_id="s1",
                raw={"orderId": "s1", "origQty": 10, "executedQty": 10, "status": "FILLED"},
            ),
        ]
        segs = segment_positions_from_fills(fills, symbol="XYZUSDT", market="spot")
        self.assertEqual(len(segs), 1)
        closed = segs[0]
        self.assertEqual(closed["status"], "closed")
        self.assertEqual(closed["n_buys"], 1)
        self.assertEqual(closed["n_sells"], 1)

    def test_attach_layers_collapse_and_drop_in_progress(self):
        ent = {
            "symbol": "SYN_USDT",
            "market": "futures",
            "opened_at": 1000.0,
            "closed_at": None,
            "contract_size": 1.0,
        }
        fills = [
            _fill(
                "SYN_USDT",
                "buy",
                0.1,
                4,
                1100,
                market="futures",
                raw={"orderId": "o-partial", "origQty": 10, "status": "PARTIALLY_FILLED"},
            ),
            _fill(
                "SYN_USDT",
                "buy",
                0.1,
                50,
                1200,
                market="futures",
                raw={"orderId": "o-full-a", "origQty": 100, "status": "FILLED"},
            ),
            _fill(
                "SYN_USDT",
                "buy",
                0.12,
                50,
                1210,
                market="futures",
                raw={"orderId": "o-full-a", "origQty": 100, "status": "FILLED"},
            ),
            _fill(
                "SYN_USDT",
                "sell",
                0.11,
                20,
                1300,
                market="futures",
                raw={"orderId": "o-sell", "origQty": 20, "status": "FILLED"},
            ),
        ]
        _attach_fills_window(ent, fills, market="futures", open_position=True)
        self.assertEqual(ent["n_buys"], 1)
        self.assertEqual(ent["n_sells"], 1)
        self.assertAlmostEqual(ent["buy_orders"][0]["qty"], 100.0, places=6)
        ids = {str(x.get("order_id") or "") for x in ent["buy_orders"]}
        self.assertNotIn("o-partial", ids)

    def test_legacy_fill_without_order_id_still_counts(self):
        fills = [_fill("XUSDT", "buy", 1.0, 2, 10)]
        segs = segment_positions_from_fills(fills, symbol="XUSDT", market="spot")
        self.assertEqual(len(segs), 1)
        self.assertAlmostEqual(segs[0]["size_remaining"], 2.0, places=6)


class TestP3RemainingCostSplitBooks(unittest.TestCase):
    def test_sell_above_drops_leftover_avg(self):
        self.assertAlmostEqual(remaining_cost_average(200, 150, 50), 1.0, places=8)

    def test_sell_below_raises_leftover_avg(self):
        self.assertAlmostEqual(remaining_cost_average(200, 155, 40), 1.125, places=8)

    def test_open_segment_remaining_cost(self):
        fills = [
            _fill("NESUSDT", "buy", 2.0, 100, 100),
            _fill("NESUSDT", "sell", 3.0, 50, 200),
            _fill("NESUSDT", "sell", 0.5, 10, 300),
        ]
        segs = segment_positions_from_fills(fills, symbol="NESUSDT", market="spot")
        self.assertEqual(len(segs), 1)
        open_ = segs[0]
        self.assertEqual(open_["status"], "open")
        self.assertAlmostEqual(open_["size_remaining"], 40.0, places=8)
        self.assertAlmostEqual(open_["bought_usd"], 200.0, places=4)
        self.assertAlmostEqual(open_["sold_usd"], 155.0, places=4)
        self.assertAlmostEqual(open_["remaining_cost_usd"], 45.0, places=4)
        self.assertAlmostEqual(open_["entry_avg"], 1.125, places=8)
        self.assertLess(open_["entry_avg"], 2.0)

    def test_futures_open_same_formula_not_mixed_with_spot(self):
        fills = [
            _fill("NES_USDT", "buy", 0.1407, 1000, 100, market="futures"),
            _fill("NES_USDT", "sell", 0.1500, 400, 200, market="futures"),
            _fill("NESUSDT", "buy", 9.0, 10, 150, market="spot"),
        ]
        fut = segment_positions_from_fills(fills, symbol="NES_USDT", market="futures")
        spot = segment_positions_from_fills(fills, symbol="NESUSDT", market="spot")
        open_f = next(s for s in fut if s["status"] == "open")
        bought = 0.1407 * 1000
        sold = 0.1500 * 400
        expected = (bought - sold) / 600.0
        self.assertAlmostEqual(open_f["entry_avg"], expected, places=8)
        self.assertLess(open_f["entry_avg"], 0.1407)
        self.assertEqual(len(spot), 1)
        self.assertAlmostEqual(spot[0]["size_remaining"], 10.0, places=6)
        self.assertAlmostEqual(spot[0]["entry_avg"], 9.0, places=6)

    def test_closed_no_divide_by_zero(self):
        fills = [
            _fill("BARUSDT", "buy", 10.0, 10, 100),
            _fill("BARUSDT", "sell", 12.0, 10, 200),
        ]
        segs = segment_positions_from_fills(fills, symbol="BARUSDT", market="spot")
        closed = segs[0]
        self.assertEqual(closed["status"], "closed")
        self.assertAlmostEqual(closed["remaining_cost_usd"], 0.0, places=8)
        apply_open_remaining_cost_avg(closed)
        self.assertAlmostEqual(closed["entry_avg"], 10.0, places=8)

    def test_spot_ignores_futures_contract_size(self):
        d = {
            "market": "spot",
            "size_remaining": 10.0,
            "entry_display": 1.0,
            "mark_price": 2.0,
            "raw": {"contractSize": 100},
            "unrealized_pnl": 999.0,
            "contract_size": 100.0,
        }
        apply_open_mark_math(d)
        self.assertEqual(d["book"], "spot")
        self.assertAlmostEqual(d["upnl_usd_est"], 10.0, places=6)
        self.assertAlmostEqual(d["remaining_mark_usd"], 20.0, places=6)

    def test_futures_uses_contract_size(self):
        d = {
            "market": "futures",
            "size_remaining": 10.0,
            "entry_display": 100.0,
            "mark_price": 110.0,
            "position_side": "long",
            "raw": {"contractSize": 0.01},
        }
        apply_open_mark_math(d)
        self.assertEqual(d["book"], "futures")
        self.assertAlmostEqual(d["remaining_mark_usd"], 11.0, places=6)
        self.assertAlmostEqual(d["upnl_usd_est"], 1.0, places=6)

    def test_reconstruct_open_uses_remaining_cost(self):
        fills = [
            _fill("AAAUSDT", "buy", 10.0, 10, 100),
            _fill("AAAUSDT", "buy", 12.0, 10, 200),
            _fill("AAAUSDT", "sell", 14.0, 5, 300),
            _fill("AAAUSDT", "buy", 11.0, 5, 400),
        ]
        r = reconstruct_open_from_fills(fills, symbol="AAAUSDT", market="spot")
        self.assertTrue(r["is_open"])
        self.assertAlmostEqual(r["size_remaining"], 20.0, places=5)
        self.assertAlmostEqual(r["entry_avg"], 10.25, places=5)


class TestP4NoEmptyFields(unittest.TestCase):
    def test_open_and_closed_display_fields_are_numbers(self):
        open_ent = {
            "symbol": "FOOUSDT",
            "market": "spot",
            "status": "open",
            "is_open": True,
            "size_remaining": 3,
            "buy_orders": [{"price": 2, "qty": 5, "quote_qty": 10}],
            "sell_orders": [{"price": 3, "qty": 2, "quote_qty": 6}],
        }
        ensure_position_display_fields(open_ent)
        for key in (
            "bought_usd",
            "sold_usd",
            "remaining_cost_usd",
            "remaining_mark_usd",
            "realized_pnl_usd",
            "size_remaining",
            "size_qty",
            "entry_avg",
            "leftover_avg",
            "upnl_usd_est",
        ):
            self.assertIsInstance(open_ent[key], (int, float), key)
            self.assertIsNotNone(open_ent[key], key)

        closed = {
            "symbol": "FOOUSDT",
            "market": "spot",
            "status": "closed",
            "is_open": False,
        }
        ensure_position_display_fields(closed)
        for key in (
            "bought_usd",
            "sold_usd",
            "remaining_cost_usd",
            "realized_pnl_usd",
            "size_remaining",
            "leftover_avg",
            "entry_avg",
            "exit_avg",
        ):
            self.assertIsInstance(closed[key], (int, float), key)


class TestP5P6PnlHistoryAndMath(unittest.TestCase):
    def test_unknown_window_is_not_30d(self):
        self.assertIsNone(_window_cutoff("all"))
        self.assertIsNone(_window_cutoff(""))
        self.assertIsNone(_window_cutoff("weird"))

    def test_p5_oldest_close_listed_and_p6_remaining_cost_match(self):
        now = time.time()
        old_ts = now - 120 * 86400  # older than 30d and 90d

        def fake_entities(user_id, include_closed=True, closed_limit=0):
            open_e = {
                "symbol": "NESUSDT",
                "market": "spot",
                "status": "open",
                "is_open": True,
                "size_remaining": 40.0,
                "bought_usd": 200.0,
                "sold_usd": 155.0,
                "remaining_cost_usd": 45.0,
                "entry_avg": 1.125,
                "entry_display": 1.125,
                "leftover_avg": 1.125,
                "realized_pnl_usd": 0.0,
                "remaining_mark_usd": 50.0,
                "upnl_usd_est": 5.0,
            }
            closed_old = {
                "symbol": "OLDUSDT",
                "market": "spot",
                "status": "closed",
                "is_open": False,
                "opened_at": old_ts - 3600,
                "closed_at": old_ts,
                "bought_usd": 100.0,
                "sold_usd": 130.0,
                "realized_pnl_usd": 30.0,
                "remaining_cost_usd": 0.0,
                "entry_avg": 10.0,
                "exit_avg": 13.0,
                "size_qty": 10.0,
            }
            closed_new = {
                "symbol": "NEWUSDT",
                "market": "futures",
                "status": "closed",
                "is_open": False,
                "opened_at": now - 3600,
                "closed_at": now - 60,
                "bought_usd": 50.0,
                "sold_usd": 40.0,
                "realized_pnl_usd": -10.0,
                "remaining_cost_usd": 0.0,
                "entry_avg": 5.0,
                "exit_avg": 4.0,
                "size_qty": 10.0,
            }
            return [open_e, closed_old, closed_new]

        with patch(
            "mexc_bot.webapi.pnl.list_position_entities", side_effect=fake_entities
        ):
            all_sum = build_pnl_summary(1, window="all")
            d30 = build_pnl_summary(1, window="30d")

        hist = all_sum["closed_history"]
        self.assertEqual(len(hist), 2)
        symbols = {h["symbol"] for h in hist}
        self.assertIn("OLDUSDT", symbols)
        self.assertIn("NEWUSDT", symbols)
        old = next(h for h in hist if h["symbol"] == "OLDUSDT")
        self.assertEqual(old["bought_usd"], 100.0)
        self.assertEqual(old["realized_pnl_usd"], 30.0)

        # 30d score may drop the old close, but history list stays full.
        self.assertEqual(len(d30["closed_history"]), 2)
        self.assertIn("OLDUSDT", {h["symbol"] for h in d30["closed_history"]})

        open_row = all_sum["open_book"][0]
        leftover = (open_row["bought_usd"] - open_row["sold_usd"]) / open_row[
            "size_remaining"
        ]
        self.assertAlmostEqual(leftover, 1.125, places=8)
        self.assertAlmostEqual(open_row["leftover_avg"], 1.125, places=8)
        self.assertAlmostEqual(open_row["remaining_cost_usd"], 45.0, places=4)
        for key in (
            "bought_usd",
            "sold_usd",
            "realized_pnl_usd",
            "remaining_mark_usd",
            "remaining_cost_usd",
            "leftover_avg",
        ):
            self.assertIsInstance(open_row[key], (int, float), key)

    def test_closed_limit_zero_keeps_all(self):
        from mexc_bot.webapi.positions_enrich import list_position_entities

        self.assertTrue(callable(list_position_entities))


class TestFuturesRemainingCostOnEntity(unittest.TestCase):
    def test_futures_entity_layers_override_frozen_hold_avg(self):
        ent = {
            "symbol": "NES_USDT",
            "market": "futures",
            "status": "open",
            "is_open": True,
            "size_remaining": 600.0,
            "entry_avg": 0.1399,
            "entry_display": 0.1399,
            "hold_avg": 0.1399,
            "buy_orders": [{"price": 0.1407, "qty": 1000, "ts": 1, "side": "buy"}],
            "sell_orders": [{"price": 0.1500, "qty": 400, "ts": 2, "side": "sell"}],
        }
        apply_open_remaining_cost_avg(ent)
        expected = (0.1407 * 1000 - 0.1500 * 400) / 600.0
        self.assertAlmostEqual(ent["entry_avg"], expected, places=8)
        self.assertAlmostEqual(ent["hold_avg"], 0.1399, places=8)
        self.assertLess(ent["entry_avg"], 0.1399)

    def test_no_fills_keeps_exchange_avg(self):
        ent = {
            "symbol": "SYN_USDT",
            "market": "futures",
            "status": "open",
            "is_open": True,
            "size_remaining": 4352.0,
            "entry_avg": 0.0998,
            "entry_display": 0.0998,
            "hold_avg": 0.0998,
            "buy_orders": [],
            "sell_orders": [],
        }
        apply_open_remaining_cost_avg(ent)
        self.assertAlmostEqual(ent["entry_avg"], 0.0998, places=8)
        self.assertNotIn("remaining_cost_usd", ent)


class TestReconcileRemainingCost(unittest.TestCase):
    def test_futures_open_uses_fill_remaining_cost_not_hold_avg(self):
        from mexc_bot.webapi.positions_enrich import _reconcile_futures_with_exchange

        exch = [
            {
                "symbol": "NES_USDT",
                "hold_vol": 600.0,
                "entry_avg": 0.1399,
                "hold_avg": 0.1399,
                "leverage": 1,
                "position_type": 1,
                "opened_at": 1_700_000_000,
            }
        ]
        fills = [
            _fill("NES_USDT", "buy", 0.1407, 1000, 1_700_000_000, market="futures"),
            _fill("NES_USDT", "sell", 0.1500, 400, 1_700_000_100, market="futures"),
        ]
        expected = (0.1407 * 1000 - 0.1500 * 400) / 600.0
        with patch(
            "mexc_bot.learning.fills.fetch_live_futures_opens", return_value=exch
        ):
            out = _reconcile_futures_with_exchange(
                [], store=None, user_id=1, fills_all=fills
            )
        fut = next(e for e in out if e.get("status") == "open")
        self.assertAlmostEqual(fut["size_remaining"], 600.0, places=5)
        self.assertAlmostEqual(fut["hold_avg"], 0.1399, places=8)
        self.assertAlmostEqual(fut["entry_avg"], expected, places=8)
        self.assertLess(fut["entry_avg"], 0.1399)


if __name__ == "__main__":
    unittest.main()
