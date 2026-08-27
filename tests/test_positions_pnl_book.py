#!/usr/bin/env python3
"""Prove Positions / PnL book goals P1–P6 on live-shaped data.

Pike LIVE before-build (do not overlay live):
  P1 PRL: 50 fill rows / 16 unique prices / extra 34. One price = one row.
  P2 closed: 1290 fills / 458 unique prices / extra 832. Default ?closed=true.
  P3 leftover: remaining_avg = (bought − sold) / rem. PRL
      (2740.7733 − 0) / 13494.37 = 0.203105. Field name remaining_avg.
  P4 SYN + closed futures empties must be numbers. Closed mark + uPnL numbers.
  P5 GET /api/pnl and ?range=all must be full book (not 30d). Default All.
  P6 leftover avg on PnL; spot/futures split; closed-cycle list.
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
    collapse_entity_layers,
    collapse_fills_to_orders,
    ensure_position_display_fields,
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


def _live_fills(symbol, n_fills, prices, *, market="spot", side="buy", t0=1_000):
    """Deal-keyed fills: unique fill id per row, no shared orderId (live shape)."""
    out = []
    for i in range(n_fills):
        px = prices[i % len(prices)]
        out.append(
            _fill(
                symbol,
                side,
                px,
                1.0,
                t0 + i,
                market=market,
                order_id=f"deal-{i}",
                raw={"id": f"deal-{i}", "orderId": f"deal-{i}", "status": "FILLED"},
            )
        )
    return out


class TestP1OpenOnePriceOneRow(unittest.TestCase):
    def test_p1_prl_50_fills_16_prices_16_rows(self):
        prices = [0.200 + i * 0.001 for i in range(16)]
        fills = _live_fills("PRLUSDT", 50, prices)
        self.assertEqual(len(fills), 50)
        self.assertEqual(len({round(f["price"], 10) for f in fills}), 16)
        rows = collapse_fills_to_orders(fills)
        self.assertEqual(len(rows), 16)
        extra = 50 - 16
        self.assertEqual(extra, 34)

    def test_p1_same_order_two_prices_two_rows(self):
        fills = [
            _fill("ABCUSDT", "buy", 1.0, 5, 1000, order_id="ord-1"),
            _fill("ABCUSDT", "buy", 1.2, 5, 1100, order_id="ord-1"),
        ]
        rows = collapse_fills_to_orders(fills)
        self.assertEqual(len(rows), 2)

    def test_p1_attach_collapses_deal_keyed_fills(self):
        prices = [0.10, 0.11, 0.12]
        fills = _live_fills("PRL_USDT", 12, prices, market="futures")
        ent = {
            "symbol": "PRL_USDT",
            "market": "futures",
            "opened_at": 900.0,
            "closed_at": None,
            "contract_size": 1.0,
        }
        _attach_fills_window(ent, fills, market="futures", open_position=True)
        self.assertEqual(ent["n_buys"], 3)
        self.assertEqual(len(ent["buy_orders"]), 3)

    def test_legacy_fill_without_order_id_still_counts(self):
        fills = [_fill("XUSDT", "buy", 1.0, 2, 10)]
        segs = segment_positions_from_fills(fills, symbol="XUSDT", market="spot")
        self.assertEqual(len(segs), 1)
        self.assertAlmostEqual(segs[0]["size_remaining"], 2.0, places=6)
        collapse_entity_layers(segs[0])
        self.assertEqual(segs[0]["n_buys"], 1)


class TestP2ClosedOnePriceOneRow(unittest.TestCase):
    def test_p2_closed_1290_style_unique_prices(self):
        prices = [1.0 + i * 0.01 for i in range(458)]
        buys = _live_fills("FOOUSDT", 800, prices[:300], side="buy", t0=100)
        sells = _live_fills(
            "FOOUSDT", 490, prices[300:], side="sell", t0=10_000
        )
        self.assertEqual(len(buys) + len(sells), 1290)
        unique = {round(f["price"], 10) for f in buys + sells}
        self.assertEqual(len(unique), 458)
        rows = collapse_fills_to_orders(buys + sells)
        self.assertEqual(len(rows), 458)
        extra = 1290 - 458
        self.assertEqual(extra, 832)

    def test_p2_closed_entity_layers_collapse(self):
        fills = [
            _fill("XYZUSDT", "buy", 10.0, 4, 100, order_id="b-deal-1"),
            _fill("XYZUSDT", "buy", 10.0, 6, 110, order_id="b-deal-2"),
            _fill("XYZUSDT", "sell", 12.0, 4, 200, order_id="s-deal-1"),
            _fill("XYZUSDT", "sell", 12.0, 6, 210, order_id="s-deal-2"),
        ]
        segs = segment_positions_from_fills(fills, symbol="XYZUSDT", market="spot")
        self.assertEqual(len(segs), 1)
        closed = segs[0]
        self.assertEqual(closed["status"], "closed")
        collapse_entity_layers(closed)
        self.assertEqual(closed["n_buys"], 1)
        self.assertEqual(closed["n_sells"], 1)


class TestP3RemainingCostSplitBooks(unittest.TestCase):
    def test_p3_prl_leftover_formula_remaining_avg(self):
        bought, sold, rem = 2740.7733, 0.0, 13494.37
        leftover = remaining_cost_average(bought, sold, rem)
        self.assertAlmostEqual(leftover, 0.203105, places=6)
        ent = {
            "symbol": "PRLUSDT",
            "market": "spot",
            "status": "open",
            "is_open": True,
            "size_remaining": rem,
            "bought_usd": bought,
            "sold_usd": sold,
        }
        apply_open_remaining_cost_avg(ent)
        self.assertAlmostEqual(ent["remaining_avg"], 0.203105, places=6)
        self.assertAlmostEqual(ent["entry_display"], 0.203105, places=6)
        self.assertAlmostEqual(ent["remaining_cost_usd"], 2740.7733, places=4)

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
        apply_open_remaining_cost_avg(open_)
        self.assertAlmostEqual(open_["remaining_avg"], 1.125, places=8)

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

    def test_closed_remaining_avg_zero_no_divide(self):
        fills = [
            _fill("BARUSDT", "buy", 10.0, 10, 100),
            _fill("BARUSDT", "sell", 12.0, 10, 200),
        ]
        segs = segment_positions_from_fills(fills, symbol="BARUSDT", market="spot")
        closed = segs[0]
        self.assertEqual(closed["status"], "closed")
        ensure_position_display_fields(closed)
        self.assertAlmostEqual(closed["remaining_cost_usd"], 0.0, places=8)
        self.assertAlmostEqual(closed["remaining_avg"], 0.0, places=8)
        self.assertAlmostEqual(closed["size_remaining"], 0.0, places=8)

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
            "buy_orders": [{"price": 2, "qty": 5, "quote_qty": 10, "side": "buy"}],
            "sell_orders": [{"price": 3, "qty": 2, "quote_qty": 6, "side": "sell"}],
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
            "remaining_avg",
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
            "remaining_avg",
            "entry_avg",
            "exit_avg",
            "mark_price",
            "upnl_usd_est",
        ):
            self.assertIsInstance(closed[key], (int, float), key)

    def test_p4_syn_no_fills_in_out_avg_cost_upnl_are_numbers(self):
        syn = {
            "symbol": "SYN_USDT",
            "market": "futures",
            "status": "open",
            "is_open": True,
            "free_coins": True,
            "size_remaining": 4352.0,
            "entry_avg": 0.0998,
            "entry_display": 0.0998,
            "hold_avg": 0.0998,
            "buy_orders": [],
            "sell_orders": [],
        }
        ensure_position_display_fields(syn)
        for key in (
            "bought_usd",
            "sold_usd",
            "remaining_cost_usd",
            "remaining_avg",
            "entry_avg",
            "upnl_usd_est",
        ):
            self.assertIsInstance(syn[key], (int, float), key)
        self.assertAlmostEqual(syn["bought_usd"], 0.0, places=4)
        self.assertAlmostEqual(syn["sold_usd"], 0.0, places=4)
        self.assertAlmostEqual(syn["remaining_cost_usd"], 0.0, places=4)

    def test_p4_closed_futures_unknown_size_stays_zero(self):
        ent = {
            "symbol": "NOSUCHFUT_USDT",
            "market": "futures",
            "status": "closed",
            "is_open": False,
            "entry_avg": 1.0,
            "exit_avg": 1.1,
            "size_qty": 10.0,
            "size_sold": 10.0,
            "size_remaining": 0.0,
            "bought_usd": None,
            "sold_usd": None,
        }
        from mexc_bot.webapi.contract_size import resolve_futures_contract_size

        with patch(
            "mexc_bot.webapi.contract_size.refresh_contract_size_catalog",
            return_value=False,
        ):
            self.assertIsNone(resolve_futures_contract_size("NOSUCHFUT_USDT", fetch=True))
            ensure_position_display_fields(ent)
        self.assertEqual(ent["bought_usd"], 0.0)
        self.assertEqual(ent["sold_usd"], 0.0)

    def test_p4_all_40_closed_have_mark_and_upnl(self):
        for i in range(40):
            ent = {
                "symbol": f"C{i}USDT",
                "market": "spot" if i < 38 else "futures",
                "status": "closed",
                "is_open": False,
                "entry_avg": 1.0 + i,
                "exit_avg": 1.1 + i,
                "size_qty": 10.0,
                "size_remaining": 0.0,
            }
            ensure_position_display_fields(ent)
            self.assertIsInstance(ent["mark_price"], (int, float), i)
            self.assertIsInstance(ent["upnl_usd_est"], (int, float), i)
            self.assertIsNotNone(ent["mark_price"], i)
            self.assertIsNotNone(ent["upnl_usd_est"], i)


class TestP5P6PnlHistoryAndMath(unittest.TestCase):
    def test_unknown_window_is_not_30d(self):
        self.assertIsNone(_window_cutoff("all"))
        self.assertIsNone(_window_cutoff(""))
        self.assertIsNone(_window_cutoff("weird"))

    def _fake_book(self):
        now = time.time()
        old_ts = now - 120 * 86400

        def fake_entities(user_id, include_closed=True, closed_limit=0):
            open_e = {
                "symbol": "PRLUSDT",
                "market": "spot",
                "status": "open",
                "is_open": True,
                "size_remaining": 13494.37,
                "bought_usd": 2740.7733,
                "sold_usd": 0.0,
                "remaining_cost_usd": 2740.7733,
                "entry_avg": 0.203105,
                "entry_display": 0.203105,
                "leftover_avg": 0.203105,
                "remaining_avg": 0.203105,
                "realized_pnl_usd": 0.0,
                "remaining_mark_usd": 3000.0,
                "upnl_usd_est": 259.23,
            }
            syn = {
                "symbol": "SYN_USDT",
                "market": "futures",
                "status": "open",
                "is_open": True,
                "free_coins": True,
                "size_remaining": 4352.0,
                "entry_avg": 0.0998,
                "entry_display": 0.0998,
                "buy_orders": [],
                "sell_orders": [],
            }
            closed = []
            for i in range(80):
                ts = old_ts if i < 28 else now - 3600
                closed.append(
                    {
                        "symbol": f"OLD{i}USDT" if i < 28 else f"NEW{i}USDT",
                        "market": "spot" if i < 78 else "futures",
                        "status": "closed",
                        "is_open": False,
                        "opened_at": ts - 3600,
                        "closed_at": ts,
                        "bought_usd": 100.0,
                        "sold_usd": 110.0,
                        "realized_pnl_usd": 10.0,
                        "remaining_cost_usd": 0.0,
                        "entry_avg": 10.0,
                        "exit_avg": 11.0,
                        "size_qty": 10.0,
                    }
                )
            return [open_e, syn] + closed

        return fake_entities

    def test_p5_oldest_close_listed_and_p6_remaining_cost_match(self):
        with patch(
            "mexc_bot.webapi.pnl.list_position_entities",
            side_effect=self._fake_book(),
        ):
            all_sum = build_pnl_summary(1, window="all")
            d30 = build_pnl_summary(1, window="30d")

        hist = all_sum["closed_history"]
        self.assertEqual(len(hist), 80)
        self.assertEqual(all_sum["realized"]["closed_n"], 80)
        self.assertEqual(all_sum["realized"]["closed_all_n"], 80)
        self.assertEqual(all_sum["window"], "all")
        self.assertTrue(any(h["symbol"].startswith("OLD") for h in hist))

        self.assertEqual(len(d30["closed_history"]), 80)
        self.assertEqual(d30["realized"]["closed_n"], 52)
        self.assertEqual(d30["realized"]["closed_all_n"], 80)

        prl = next(p for p in all_sum["open_book"] if p["symbol"] == "PRLUSDT")
        leftover = (prl["bought_usd"] - prl["sold_usd"]) / prl["size_remaining"]
        self.assertAlmostEqual(leftover, 0.203105, places=6)
        self.assertAlmostEqual(prl["remaining_avg"], 0.203105, places=6)
        self.assertAlmostEqual(prl["remaining_cost_usd"], 2740.7733, places=4)
        syn = next(p for p in all_sum["open_book"] if p["symbol"] == "SYN_USDT")
        for key in (
            "bought_usd",
            "sold_usd",
            "realized_pnl_usd",
            "remaining_cost_usd",
            "remaining_avg",
        ):
            self.assertIsInstance(syn[key], (int, float), key)

    def test_p5_api_default_and_range_all_are_full_book(self):
        os.environ.pop("DESK_API_TOKEN", None)
        os.environ.pop("WEB_UI_TOKEN", None)
        from fastapi.testclient import TestClient
        from mexc_bot.webapi.app import create_app

        with patch(
            "mexc_bot.webapi.pnl.list_position_entities",
            side_effect=self._fake_book(),
        ), patch("mexc_bot.webapi.app.db.default_user_id", return_value=1):
            client = TestClient(create_app())
            bare = client.get("/api/pnl")
            ranged = client.get("/api/pnl?range=all")
            win_all = client.get("/api/pnl?window=all")
            win30 = client.get("/api/pnl?window=30d")

        self.assertEqual(bare.status_code, 200)
        self.assertEqual(bare.json()["window"], "all")
        self.assertEqual(bare.json()["realized"]["closed_n"], 80)
        self.assertEqual(len(bare.json()["closed_history"]), 80)
        self.assertEqual(ranged.json()["window"], "all")
        self.assertEqual(ranged.json()["realized"]["closed_n"], 80)
        self.assertEqual(win_all.json()["realized"]["closed_n"], 80)
        self.assertEqual(win30.json()["window"], "30d")
        self.assertEqual(win30.json()["realized"]["closed_n"], 52)
        self.assertEqual(len(win30.json()["closed_history"]), 80)

    def test_p5_range_all_overrides_window_30d(self):
        os.environ.pop("DESK_API_TOKEN", None)
        os.environ.pop("WEB_UI_TOKEN", None)
        from fastapi.testclient import TestClient
        from mexc_bot.webapi.app import create_app

        with patch(
            "mexc_bot.webapi.pnl.list_position_entities",
            side_effect=self._fake_book(),
        ), patch("mexc_bot.webapi.app.db.default_user_id", return_value=1):
            client = TestClient(create_app())
            r = client.get("/api/pnl?window=30d&range=all")
        self.assertEqual(r.json()["window"], "all")
        self.assertEqual(r.json()["realized"]["closed_n"], 80)

    def test_p6_desk_js_has_leftover_remaining_avg_and_closed_list(self):
        js = (ROOT / "mexc_bot/webapi/static/assets/desk.js").read_text()
        html = (ROOT / "mexc_bot/webapi/static/index.html").read_text()
        self.assertIn("remaining_avg", js)
        self.assertIn("collapseLayersByPrice", js)
        self.assertIn('state.pnlWindow = "all"', js)
        self.assertIn("range=", js)
        self.assertIn("Leftover", js)
        self.assertIn("closed_history", js)
        self.assertIn('data-pnl-win="all"', html)
        self.assertIn('data-pnl-win="all">All', html.replace("\n", ""))


class TestPikeClosedFuturesCashNotNotional(unittest.TestCase):
    """Live-shaped ONG / MRNASTOCK: In/Out are leftover-cost cash, not price×vol."""

    def test_ong_entry_times_qty_is_notional_not_cash(self):
        # Pike: In/Out 77.74/81.62 from entry×qty (0 fills) vs exchange Real 38.14.
        notional_in, notional_out = 77.74, 81.62
        qty = 1000.0
        entry = notional_in / qty
        exit_ = notional_out / qty
        cs = 10.0  # public /contract/detail ONG_USDT
        exch_real = 38.14
        notional_pnl = notional_out - notional_in
        self.assertAlmostEqual(notional_pnl, 3.88, places=2)
        self.assertLess(notional_pnl * 2, exch_real)  # 3.88 is not the cash PnL
        cash_in = notional_in * cs
        cash_out = notional_out * cs
        cash_real = cash_out - cash_in
        self.assertAlmostEqual(cash_in, 777.4, places=2)
        self.assertAlmostEqual(cash_out, 816.2, places=2)
        self.assertAlmostEqual(cash_real, 38.8, places=2)
        self.assertLess(abs(cash_real - exch_real) / exch_real, 0.05)

        ent = {
            "symbol": "ONG_USDT",
            "market": "futures",
            "status": "closed",
            "is_open": False,
            "entry_avg": entry,
            "entry_display": entry,
            "exit_avg": exit_,
            "size_qty": qty,
            "size_sold": qty,
            "size_remaining": 0.0,
            "bought_usd": None,
            "sold_usd": None,
            "realized_pnl_usd": exch_real,
            "buy_orders": [],
            "sell_orders": [],
        }
        ensure_position_display_fields(ent)
        self.assertAlmostEqual(ent["contract_size"], 10.0, places=6)
        self.assertAlmostEqual(ent["bought_usd"], cash_in, places=2)
        self.assertAlmostEqual(ent["sold_usd"], cash_out, places=2)
        self.assertAlmostEqual(ent["realized_pnl_usd"], exch_real, places=2)
        self.assertAlmostEqual(ent["remaining_avg"], 0.0, places=8)
        self.assertNotAlmostEqual(ent["bought_usd"], notional_in, places=1)

    def test_mrnastock_fill_notionals_are_1000x_cash(self):
        # Pike: 9 buy / 29 sell layers, In 10,941,817 / Out 11,446,697 vs ~$505 Real.
        cs = 0.001  # public spec: 1 Cont = 0.001 MRNA
        target_in = 10_941_817.0
        target_out = 11_446_697.0
        buy_px = [150.0 + i for i in range(9)]
        sell_px = [160.0 + i for i in range(29)]
        buys = []
        for i, px in enumerate(buy_px):
            notional = target_in / 9.0
            qty = notional / px
            buys.append(
                _fill(
                    "MRNASTOCK_USDT",
                    "buy",
                    px,
                    qty,
                    1000 + i,
                    market="futures",
                    order_id=f"deal-b-{i}",
                    raw={"id": f"deal-b-{i}", "orderId": f"deal-b-{i}"},
                )
            )
        sells = []
        for i, px in enumerate(sell_px):
            notional = target_out / 29.0
            qty = notional / px
            sells.append(
                _fill(
                    "MRNASTOCK_USDT",
                    "sell",
                    px,
                    qty,
                    2000 + i,
                    market="futures",
                    order_id=f"deal-s-{i}",
                    raw={"id": f"deal-s-{i}", "orderId": f"deal-s-{i}"},
                )
            )
        self.assertAlmostEqual(sum(f["price"] * f["qty"] for f in buys), target_in, places=0)
        self.assertAlmostEqual(sum(f["price"] * f["qty"] for f in sells), target_out, places=0)
        rows = collapse_fills_to_orders(buys + sells)
        self.assertEqual(len([r for r in rows if r["side"] == "buy"]), 9)
        self.assertEqual(len([r for r in rows if r["side"] == "sell"]), 29)

        ent = {
            "symbol": "MRNASTOCK_USDT",
            "market": "futures",
            "status": "closed",
            "is_open": False,
            "entry_avg": 155.0,
            "exit_avg": 170.0,
            "size_qty": 1.0,
            "size_sold": 1.0,
            "size_remaining": 0.0,
            "realized_pnl_usd": 505.0,
            "buy_orders": buys,
            "sell_orders": sells,
        }
        ensure_position_display_fields(ent)
        self.assertEqual(ent["n_buys"], 9)
        self.assertEqual(ent["n_sells"], 29)
        self.assertAlmostEqual(ent["contract_size"], cs, places=6)
        self.assertAlmostEqual(ent["bought_usd"], target_in * cs, places=1)
        self.assertAlmostEqual(ent["sold_usd"], target_out * cs, places=1)
        cash_real = ent["sold_usd"] - ent["bought_usd"]
        self.assertAlmostEqual(cash_real, 504.88, places=1)
        self.assertLess(abs(cash_real - 505.0) / 505.0, 0.02)
        self.assertLess(ent["bought_usd"], 20_000)
        self.assertGreater(ent["bought_usd"], 1_000)
        self.assertAlmostEqual(ent["realized_pnl_usd"], 505.0, places=2)
        self.assertAlmostEqual(ent["remaining_avg"], 0.0, places=8)

    def test_prl_spot_leftover_does_not_regress(self):
        ent = {
            "symbol": "PRLUSDT",
            "market": "spot",
            "status": "open",
            "is_open": True,
            "size_remaining": 13494.37,
            "bought_usd": 2740.7733,
            "sold_usd": 0.0,
        }
        apply_open_remaining_cost_avg(ent)
        ensure_position_display_fields(ent)
        self.assertAlmostEqual(ent["remaining_cost_usd"], 2740.7733, places=4)
        self.assertAlmostEqual(ent["remaining_avg"], 0.203105, places=6)
        self.assertAlmostEqual(ent["bought_usd"], 2740.7733, places=4)


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
        self.assertAlmostEqual(ent["remaining_avg"], expected, places=8)
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
        ensure_position_display_fields(ent)
        self.assertAlmostEqual(ent["bought_usd"], 0.0, places=4)
        self.assertAlmostEqual(ent["remaining_cost_usd"], 0.0, places=4)
        self.assertIsInstance(ent["remaining_avg"], (int, float))


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


class TestDeskPathIntegration(unittest.TestCase):
    """journal_fills → entities → PnL. Does not touch learning_* rows."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "alerts.db"
        self._old = os.environ.get("ALERTS_FILE")
        os.environ["ALERTS_FILE"] = str(self.db)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("ALERTS_FILE", None)
        else:
            os.environ["ALERTS_FILE"] = self._old
        self.tmp.cleanup()

    def test_grouped_fills_one_open_row_and_old_close_on_pnl(self):
        from mexc_bot.learning.store import EventStore
        from mexc_bot.webapi.positions_enrich import list_position_entities

        store = EventStore(self.db)
        uid = 8630949601
        now = time.time()
        old = now - 80 * 86400
        for i, tid in enumerate(("f1", "f2")):
            store.insert_fill(
                user_id=uid,
                exchange_trade_id=tid,
                symbol="NESUSDT",
                market="spot",
                side="buy",
                price=2.0,
                qty=50,
                quote_qty=100.0,
                ts=now - 100 + i,
                raw={"id": tid, "orderId": tid, "origQty": 50, "status": "FILLED"},
            )
        store.insert_fill(
            user_id=uid,
            exchange_trade_id="f3",
            symbol="NESUSDT",
            market="spot",
            side="sell",
            price=3.0,
            qty=50,
            quote_qty=150.0,
            ts=now - 80,
            raw={"id": "f3", "orderId": "f3", "origQty": 50, "status": "FILLED"},
        )
        store.insert_fill(
            user_id=uid,
            exchange_trade_id="oldb",
            symbol="OLDUSDT",
            market="spot",
            side="buy",
            price=1.0,
            qty=10,
            quote_qty=10.0,
            ts=old - 10,
            raw={"orderId": "old-b", "status": "FILLED"},
        )
        store.insert_fill(
            user_id=uid,
            exchange_trade_id="olds",
            symbol="OLDUSDT",
            market="spot",
            side="sell",
            price=2.0,
            qty=10,
            quote_qty=20.0,
            ts=old,
            raw={"orderId": "old-s", "status": "FILLED"},
        )

        with patch(
            "mexc_bot.webapi.positions_enrich._reconcile_spot_with_balances",
            side_effect=lambda entities, store, user_id, fills_all=None: entities,
        ), patch(
            "mexc_bot.webapi.positions_enrich._reconcile_futures_with_exchange",
            side_effect=lambda entities, store, user_id, fills_all=None: [
                e
                for e in entities
                if not (
                    (e.get("market") or "").lower() == "futures"
                    and (e.get("status") == "open" or e.get("is_open"))
                )
            ],
        ), patch(
            "mexc_bot.webapi.positions_enrich._merge_futures_closed_history",
            side_effect=lambda entities, store, user_id, fills_all, closed_limit=0: [
                e
                for e in entities
                if not (
                    (e.get("market") or "").lower() == "futures"
                    and e.get("status") == "closed"
                )
            ]
            + [],
        ), patch(
            "mexc_bot.webapi.positions_enrich.ticker_24h",
            return_value={"price": 2.5, "changePercent": 0, "source": "test"},
        ):
            ents = list_position_entities(uid, include_closed=True, closed_limit=0)
            summary = build_pnl_summary(uid, window="all")

        opens = [e for e in ents if e.get("status") == "open"]
        nes = next(e for e in opens if e.get("symbol") == "NESUSDT")
        self.assertEqual(nes["n_buys"], 1)
        leftover = (nes["bought_usd"] - nes["sold_usd"]) / nes["size_remaining"]
        self.assertAlmostEqual(nes["entry_avg"], leftover, places=8)
        self.assertAlmostEqual(nes["remaining_avg"], leftover, places=8)
        self.assertAlmostEqual(leftover, 1.0, places=8)
        for key in ("bought_usd", "sold_usd", "remaining_cost_usd", "size_remaining"):
            self.assertIsNotNone(nes[key])

        hist_syms = {h["symbol"] for h in summary["closed_history"]}
        self.assertIn("OLDUSDT", hist_syms)
        old_row = next(h for h in summary["closed_history"] if h["symbol"] == "OLDUSDT")
        self.assertGreater(now - float(old_row["closed_at"]), 30 * 86400)


if __name__ == "__main__":
    unittest.main()
