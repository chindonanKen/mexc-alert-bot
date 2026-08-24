"""Remaining-cost average for OPEN positions.

remaining_avg = (bought USD − sold USD) / remaining qty
Sell above leftover avg → leftover avg goes down.
Sell below leftover avg → leftover avg goes up.
Closed / zero remaining qty must not divide by zero.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mexc_bot.learning.trades import (
    apply_open_remaining_cost_avg,
    remaining_cost_average,
    reconstruct_open_from_fills,
    segment_positions_from_fills,
)


def _fill(symbol, side, price, qty, ts, market="spot"):
    return {
        "symbol": symbol,
        "market": market,
        "side": side,
        "price": price,
        "qty": qty,
        "ts": ts,
    }


class TestRemainingCostAverageHelper(unittest.TestCase):
    def test_sell_above_drops_leftover_avg(self):
        # Buy 100 @ 2 → $200. Sell 50 @ 3 (above) → leftover (200-150)/50 = 1.0
        self.assertAlmostEqual(remaining_cost_average(200, 150, 50), 1.0, places=8)

    def test_sell_below_raises_leftover_avg(self):
        # Then sell 10 @ 0.50 (below 1.0) → (200-155)/40 = 1.125
        self.assertAlmostEqual(remaining_cost_average(200, 155, 40), 1.125, places=8)

    def test_closed_qty_is_none_not_crash(self):
        self.assertIsNone(remaining_cost_average(200, 220, 0))
        self.assertIsNone(remaining_cost_average(200, 220, 1e-15))
        self.assertIsNone(remaining_cost_average(200, 220, None))


class TestOpenSegmentRemainingCost(unittest.TestCase):
    def test_buy_sell_above_then_below_moves_avg(self):
        fills = [
            _fill("NESUSDT", "buy", 2.0, 100, 100),
            _fill("NESUSDT", "sell", 3.0, 50, 200),  # above 2 → leftover 1.0
            _fill("NESUSDT", "sell", 0.5, 10, 300),  # below 1 → leftover 1.125
        ]
        segs = segment_positions_from_fills(fills, symbol="NESUSDT", market="spot")
        self.assertEqual(len(segs), 1)
        open_ = segs[0]
        self.assertEqual(open_["status"], "open")
        self.assertGreater(open_["size_remaining"], 0)
        self.assertAlmostEqual(open_["size_remaining"], 40.0, places=8)
        self.assertAlmostEqual(open_["bought_usd"], 200.0, places=4)
        self.assertAlmostEqual(open_["sold_usd"], 155.0, places=4)
        self.assertAlmostEqual(open_["remaining_cost_usd"], 45.0, places=4)
        self.assertAlmostEqual(open_["entry_avg"], 1.125, places=8)
        self.assertAlmostEqual(open_["entry_display"], 1.125, places=8)
        # Frozen inventory avg would have stayed 2.0 — prove we did not freeze
        self.assertLess(open_["entry_avg"], 2.0)

    def test_interleaved_buys_and_sells_both_directions(self):
        fills = [
            _fill("FOOUSDT", "buy", 10.0, 10, 100),  # $100, qty 10, avg 10
            _fill("FOOUSDT", "sell", 14.0, 4, 200),  # sold $56, rem 6, avg 44/6
            _fill("FOOUSDT", "buy", 8.0, 6, 300),  # bought $148, rem 12, avg 92/12
            _fill("FOOUSDT", "sell", 6.0, 2, 400),  # sold $68, rem 10, avg 80/10=8
        ]
        segs = segment_positions_from_fills(fills, symbol="FOOUSDT", market="spot")
        open_ = next(s for s in segs if s["status"] == "open")
        self.assertAlmostEqual(open_["size_remaining"], 10.0, places=8)
        self.assertAlmostEqual(open_["bought_usd"], 148.0, places=4)
        self.assertAlmostEqual(open_["sold_usd"], 68.0, places=4)
        after_first_sell = (100.0 - 56.0) / 6.0  # 7.333… down from 10
        self.assertLess(after_first_sell, 10.0)
        after_second_buy = (148.0 - 56.0) / 12.0  # 7.666…
        after_sell_below = (148.0 - 68.0) / 10.0  # 8.0 up from 7.666
        self.assertGreater(after_sell_below, after_second_buy)
        self.assertAlmostEqual(open_["entry_avg"], 8.0, places=8)
        self.assertAlmostEqual(open_["remaining_cost_usd"], 80.0, places=4)

    def test_futures_open_same_formula(self):
        fills = [
            _fill("NES_USDT", "buy", 0.1407, 1000, 100, market="futures"),
            _fill("NES_USDT", "sell", 0.1500, 400, 200, market="futures"),
        ]
        segs = segment_positions_from_fills(
            fills, symbol="NES_USDT", market="futures"
        )
        open_ = next(s for s in segs if s["status"] == "open")
        bought = 0.1407 * 1000
        sold = 0.1500 * 400
        rem = 600.0
        expected = (bought - sold) / rem
        self.assertAlmostEqual(open_["size_remaining"], rem, places=8)
        self.assertAlmostEqual(open_["entry_avg"], expected, places=8)
        self.assertLess(open_["entry_avg"], 0.1407)  # sold above → leftover down

    def test_closed_full_exit_no_divide_by_zero(self):
        fills = [
            _fill("BARUSDT", "buy", 10.0, 10, 100),
            _fill("BARUSDT", "sell", 12.0, 10, 200),
        ]
        segs = segment_positions_from_fills(fills, symbol="BARUSDT", market="spot")
        self.assertEqual(len(segs), 1)
        closed = segs[0]
        self.assertEqual(closed["status"], "closed")
        self.assertAlmostEqual(closed["size_remaining"], 0.0, places=8)
        self.assertAlmostEqual(closed["entry_avg"], 10.0, places=8)  # all-buy VWAP
        self.assertAlmostEqual(closed["exit_avg"], 12.0, places=8)
        self.assertAlmostEqual(closed["remaining_cost_usd"], 0.0, places=8)
        # apply is a no-op on closed (must not crash / must not wipe entry)
        apply_open_remaining_cost_avg(closed)
        self.assertEqual(closed["status"], "closed")
        self.assertAlmostEqual(closed["entry_avg"], 10.0, places=8)

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


class TestApplyOpenRemainingCostOnEntity(unittest.TestCase):
    def test_futures_entity_layers_override_frozen_hold_avg(self):
        ent = {
            "symbol": "NES_USDT",
            "market": "futures",
            "status": "open",
            "is_open": True,
            "size_remaining": 600.0,
            "entry_avg": 0.1399,  # frozen MEXC hold avg
            "entry_display": 0.1399,
            "hold_avg": 0.1399,
            "buy_orders": [{"price": 0.1407, "qty": 1000, "ts": 1, "side": "buy"}],
            "sell_orders": [{"price": 0.1500, "qty": 400, "ts": 2, "side": "sell"}],
        }
        apply_open_remaining_cost_avg(ent)
        expected = (0.1407 * 1000 - 0.1500 * 400) / 600.0
        self.assertAlmostEqual(ent["entry_avg"], expected, places=8)
        self.assertAlmostEqual(ent["entry_display"], expected, places=8)
        self.assertAlmostEqual(ent["hold_avg"], 0.1399, places=8)  # untouched
        self.assertLess(ent["entry_avg"], 0.1399)
        self.assertAlmostEqual(ent["remaining_cost_usd"], 80.7, places=4)

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

    def test_zero_remaining_no_crash(self):
        ent = {
            "status": "open",
            "is_open": True,
            "size_remaining": 0.0,
            "entry_avg": 1.0,
            "bought_usd": 100.0,
            "sold_usd": 100.0,
        }
        apply_open_remaining_cost_avg(ent)
        self.assertAlmostEqual(ent["entry_avg"], 1.0, places=8)


class TestReconcileAppliesRemainingCost(unittest.TestCase):
    def test_futures_open_uses_fill_remaining_cost_not_hold_avg(self):
        from unittest.mock import patch

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
        self.assertAlmostEqual(fut["entry_display"], expected, places=8)
        self.assertLess(fut["entry_avg"], 0.1399)

    def test_spot_balance_size_recomputes_remaining_avg(self):
        from unittest.mock import patch

        from mexc_bot.webapi.positions_enrich import _reconcile_spot_with_balances

        entities = [
            {
                "symbol": "NESUSDT",
                "market": "spot",
                "status": "open",
                "is_open": True,
                "size_remaining": 40.0,
                "bought_usd": 200.0,
                "sold_usd": 155.0,
                "entry_avg": 1.125,
                "entry_display": 1.125,
                "buy_orders": [{"price": 2.0, "qty": 100}],
                "sell_orders": [{"price": 3.0, "qty": 50}, {"price": 0.5, "qty": 10}],
            }
        ]
        bals = [
            {
                "asset": "NES",
                "free": 40.0,
                "locked": 0.0,
                "total": 40.0,
                "symbol": "NESUSDT",
            }
        ]
        with patch(
            "mexc_bot.learning.fills.fetch_live_spot_balances", return_value=bals
        ), patch(
            "mexc_bot.webapi.positions_enrich._spot_symbol_tradeable",
            return_value=True,
        ):
            out = _reconcile_spot_with_balances(
                entities, store=None, user_id=1, fills_all=[]
            )
        open_ = next(e for e in out if e.get("status") == "open")
        self.assertAlmostEqual(open_["size_remaining"], 40.0, places=5)
        self.assertAlmostEqual(open_["entry_avg"], 1.125, places=8)


if __name__ == "__main__":
    unittest.main()
