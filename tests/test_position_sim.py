#!/usr/bin/env python3
"""Golden tapes: leftover avg, mark $, closed PnL — spot and futures."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mexc_bot.learning.trades import remaining_cost_average, segment_positions_from_fills
from mexc_bot.webapi.position_math import (
    apply_open_mark_math,
    apply_open_remaining_cost_avg,
    remaining_cost_average as rca,
)


def _fill(sym, market, side, price, qty, ts, quote=None):
    qq = quote if quote is not None else price * qty
    return {
        "symbol": sym,
        "market": market,
        "side": side,
        "price": price,
        "qty": qty,
        "quote_qty": qq,
        "ts": ts,
        "user_id": 1,
        "exchange_trade_id": f"{sym}-{ts}-{side}",
    }


class TestPositionSim(unittest.TestCase):
    def test_spot_partial_leftover(self):
        # Buy 10 @ 2 ($20) + 10 @ 4 ($40) = $60 / 20. Sell 10 @ 5 ($50).
        # rem=10 leftover = (60-50)/10 = 1.0. Mark 3 → bag $30, uPnL (3-1)*10 = $20.
        fills = [
            _fill("AAAUSDT", "spot", "buy", 2.0, 10, 1.0),
            _fill("AAAUSDT", "spot", "buy", 4.0, 10, 2.0),
            _fill("AAAUSDT", "spot", "sell", 5.0, 10, 3.0),
        ]
        segs = segment_positions_from_fills(fills, symbol="AAAUSDT", market="spot")
        opens = [s for s in segs if s.get("status") == "open"]
        self.assertEqual(len(opens), 1)
        o = opens[0]
        apply_open_remaining_cost_avg(o)
        self.assertAlmostEqual(o["leftover_avg"], 1.0, places=6)
        o["mark_price"] = 3.0
        apply_open_mark_math(o)
        self.assertAlmostEqual(o["remaining_mark_usd"], 30.0, places=4)
        self.assertAlmostEqual(o["upnl_usd_est"], 20.0, places=4)

    def test_spot_full_flat_one_closed(self):
        fills = [
            _fill("BBBUSDT", "spot", "buy", 10.0, 2, 1.0),
            _fill("BBBUSDT", "spot", "sell", 12.0, 2, 2.0),
        ]
        segs = segment_positions_from_fills(fills, symbol="BBBUSDT", market="spot")
        closed = [s for s in segs if s.get("status") == "closed"]
        self.assertEqual(len(closed), 1)
        c = closed[0]
        self.assertAlmostEqual(c["bought_usd"], 20.0, places=4)
        self.assertAlmostEqual(c["sold_usd"], 24.0, places=4)
        self.assertAlmostEqual(c["realized_pnl_usd"], 4.0, places=4)

    def test_futures_leftover_and_upnl_known_cs(self):
        # vol 2 @ 10, vol 2 @ 14, sell vol 2 @ 16. rem=2.
        # notional in=20+28=48 out=32 leftover px=(48-32)/2=8.
        # cs=10 cash in=480 out=320 remaining_cost=160 leftover*rem*cs=160.
        # mark 9 → uPnL (9-8)*2*10 = 20. bag = 2*10*9 = 180.
        ent = {
            "symbol": "ONG_USDT",
            "market": "futures",
            "book": "futures",
            "status": "open",
            "is_open": True,
            "size_remaining": 2.0,
            "contract_size": 10.0,
            "buy_orders": [
                {"price": 10.0, "qty": 2.0, "side": "buy"},
                {"price": 14.0, "qty": 2.0, "side": "buy"},
            ],
            "sell_orders": [{"price": 16.0, "qty": 2.0, "side": "sell"}],
        }
        apply_open_remaining_cost_avg(ent)
        self.assertAlmostEqual(ent["leftover_avg"], 8.0, places=6)
        self.assertAlmostEqual(ent["bought_usd"], 480.0, places=4)
        self.assertAlmostEqual(ent["sold_usd"], 320.0, places=4)
        self.assertAlmostEqual(ent["remaining_cost_usd"], 160.0, places=4)
        ent["mark_price"] = 9.0
        apply_open_mark_math(ent)
        self.assertAlmostEqual(ent["remaining_mark_usd"], 180.0, places=4)
        self.assertAlmostEqual(ent["upnl_usd_est"], 20.0, places=4)

    def test_futures_unknown_cs_not_silent_flat(self):
        ent = {
            "symbol": "ZZZ_USDT",
            "market": "futures",
            "book": "futures",
            "status": "open",
            "is_open": True,
            "size_remaining": 5.0,
            "buy_orders": [{"price": 1.0, "qty": 5.0, "side": "buy"}],
            "sell_orders": [],
        }
        apply_open_remaining_cost_avg(ent)
        self.assertTrue(ent.get("contract_size_unknown"))
        self.assertAlmostEqual(float(ent.get("size_remaining") or 0), 5.0, places=6)
        # leftover PRICE still from notional / rem
        self.assertAlmostEqual(float(ent.get("leftover_avg") or 0), 1.0, places=6)

    def test_marks_only_reticks_existing_mark(self):
        from mexc_bot.webapi import positions_enrich as pe

        pe._open_book_cache["ts"] = __import__("time").time()
        pe._open_book_cache["entities"] = [
            {
                "symbol": "AAAUSDT",
                "market": "spot",
                "book": "spot",
                "status": "open",
                "is_open": True,
                "size_remaining": 10.0,
                "leftover_avg": 1.0,
                "entry_avg": 1.0,
                "mark_price": 1.0,
                "opened_at": 1.0,
            }
        ]

        def fake_ticker(sym):
            return {"symbol": "AAAUSDT", "price": 2.0, "changePercent": 1.0, "source": "test"}

        old = pe.ticker_24h
        pe.ticker_24h = fake_ticker
        try:
            out = pe.list_position_entities(1, include_closed=False, marks_only=True)
        finally:
            pe.ticker_24h = old
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(float(out[0]["mark_price"]), 2.0, places=6)
        self.assertAlmostEqual(float(out[0]["upnl_usd_est"]), 10.0, places=4)

    def test_remaining_cost_formula(self):
        self.assertAlmostEqual(rca(60, 50, 10), 1.0, places=9)
        self.assertIsNone(remaining_cost_average(10, 10, 0))


if __name__ == "__main__":
    unittest.main()
