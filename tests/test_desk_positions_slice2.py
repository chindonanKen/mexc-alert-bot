#!/usr/bin/env python3
"""Positions tab: futures vs spot math, and fully filled orders only."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mexc_bot.learning.trades import segment_positions_from_fills
from mexc_bot.webapi.position_math import (
    apply_open_mark_math,
    fully_filled_orders,
    is_order_fully_filled,
)
from mexc_bot.webapi.positions_enrich import _attach_fills_window


def _spot_open(**extra):
    d = {
        "market": "spot",
        "size_remaining": 10.0,
        "entry_display": 1.0,
        "mark_price": 2.0,
        "raw": {"contractSize": 100, "unRealizedPnl": 999},
        "unrealized_pnl": 999.0,
        "contract_size": 100.0,
    }
    d.update(extra)
    return d


def _fut_open(**extra):
    d = {
        "market": "futures",
        "size_remaining": 10.0,
        "entry_display": 100.0,
        "mark_price": 110.0,
        "position_side": "long",
        "raw": {"contractSize": 0.01},
    }
    d.update(extra)
    return d


class TestFuturesVsSpotMath(unittest.TestCase):
    def test_spot_ignores_futures_contract_size_and_upnl(self):
        d = _spot_open()
        apply_open_mark_math(d)
        self.assertEqual(d["book"], "spot")
        self.assertEqual(d["math"], "spot")
        self.assertEqual(d["contract_size"], 1.0)
        # qty × (mark − entry) — not × contractSize, not exchange uPnL
        self.assertAlmostEqual(d["upnl_usd_est"], 10.0, places=6)
        self.assertAlmostEqual(d["remaining_mark_usd"], 20.0, places=6)
        # Fail if futures scale leaked onto spot
        self.assertNotAlmostEqual(d["upnl_usd_est"], 999.0, places=2)
        self.assertNotAlmostEqual(d["remaining_mark_usd"], 10.0 * 100.0 * 2.0, places=2)

    def test_futures_uses_contract_size_not_spot_qty_times_price(self):
        d = _fut_open()
        apply_open_mark_math(d)
        self.assertEqual(d["book"], "futures")
        self.assertEqual(d["math"], "futures")
        self.assertAlmostEqual(d["contract_size"], 0.01, places=8)
        # notional = 10 × 0.01 × 110 = 11; uPnL = (110−100)×10×0.01 = 1
        self.assertAlmostEqual(d["remaining_mark_usd"], 11.0, places=6)
        self.assertAlmostEqual(d["upnl_usd_est"], 1.0, places=6)
        # Fail if spot rem×mark / rem×(mark−entry) was applied
        self.assertNotAlmostEqual(d["remaining_mark_usd"], 1100.0, places=2)
        self.assertNotAlmostEqual(d["upnl_usd_est"], 100.0, places=2)

    def test_futures_short_flips_mark_pnl_sign(self):
        d = _fut_open(position_side="short", position_type=2)
        apply_open_mark_math(d)
        self.assertAlmostEqual(d["upnl_usd_est"], -1.0, places=6)
        self.assertAlmostEqual(d["remaining_mark_usd"], 11.0, places=6)

    def test_futures_exchange_upnl_is_usdt_and_notional_uses_cs(self):
        d = _fut_open(unrealized_pnl=1.5, mark_price=110.0)
        apply_open_mark_math(d)
        self.assertAlmostEqual(d["upnl_usd_est"], 1.5, places=6)
        self.assertAlmostEqual(d["remaining_mark_usd"], 11.0, places=6)

    def test_snapshot_exposes_contract_size(self):
        from mexc_bot.exchange_private import futures_position_snapshot

        snap = futures_position_snapshot(
            {
                "symbol": "TSLA_USDT",
                "positionType": 1,
                "holdVol": 2,
                "holdAvgPrice": 100,
                "contractSize": 0.01,
                "unRealizedPnl": 0.4,
            }
        )
        self.assertIsNotNone(snap)
        self.assertAlmostEqual(snap["contract_size"], 0.01, places=8)
        self.assertEqual(snap["raw"].get("contractSize"), 0.01)


class TestFullyFilledOrdersOnly(unittest.TestCase):
    def test_partial_fill_is_not_an_order_row(self):
        fills = [
            {
                "symbol": "ABCUSDT",
                "market": "spot",
                "side": "buy",
                "price": 1.0,
                "qty": 4,
                "ts": 1000,
                "raw": {
                    "orderId": "ord-1",
                    "origQty": 10,
                    "executedQty": 4,
                    "status": "PARTIALLY_FILLED",
                },
            }
        ]
        self.assertEqual(fully_filled_orders(fills), [])
        self.assertFalse(is_order_fully_filled(fills[0]))
        segs = segment_positions_from_fills(fills, symbol="ABCUSDT", market="spot")
        self.assertEqual(segs, [])

    def test_two_fills_same_order_become_one_row(self):
        fills = [
            {
                "symbol": "ABCUSDT",
                "market": "spot",
                "side": "buy",
                "price": 1.0,
                "qty": 5,
                "quote_qty": 5.0,
                "ts": 1000,
                "raw": {
                    "orderId": "ord-1",
                    "origQty": 10,
                    "executedQty": 5,
                    "status": "PARTIALLY_FILLED",
                },
            },
            {
                "symbol": "ABCUSDT",
                "market": "spot",
                "side": "buy",
                "price": 1.2,
                "qty": 5,
                "quote_qty": 6.0,
                "ts": 1100,
                "raw": {
                    "orderId": "ord-1",
                    "origQty": 10,
                    "executedQty": 10,
                    "status": "FILLED",
                },
            },
        ]
        orders = fully_filled_orders(fills)
        self.assertEqual(len(orders), 1)
        self.assertAlmostEqual(orders[0]["qty"], 10.0, places=6)
        segs = segment_positions_from_fills(fills, symbol="ABCUSDT", market="spot")
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0]["status"], "open")
        self.assertEqual(segs[0]["n_buys"], 1)
        self.assertAlmostEqual(segs[0]["size_remaining"], 10.0, places=6)
        self.assertAlmostEqual(segs[0]["entry_avg"], 1.1, places=6)

    def test_attach_layers_collapse_and_drop_partial(self):
        ent = {
            "symbol": "SYN_USDT",
            "market": "futures",
            "opened_at": 1000.0,
            "closed_at": None,
            "contract_size": 1.0,
        }
        fills = [
            {
                "symbol": "SYN_USDT",
                "market": "futures",
                "side": "buy",
                "price": 0.1,
                "qty": 4,
                "ts": 1100,
                "raw": {
                    "orderId": "o-partial",
                    "origQty": 10,
                    "status": "PARTIALLY_FILLED",
                },
            },
            {
                "symbol": "SYN_USDT",
                "market": "futures",
                "side": "buy",
                "price": 0.1,
                "qty": 50,
                "ts": 1200,
                "raw": {"orderId": "o-full-a", "origQty": 100, "status": "FILLED"},
            },
            {
                "symbol": "SYN_USDT",
                "market": "futures",
                "side": "buy",
                "price": 0.12,
                "qty": 50,
                "ts": 1210,
                "raw": {"orderId": "o-full-a", "origQty": 100, "status": "FILLED"},
            },
            {
                "symbol": "SYN_USDT",
                "market": "futures",
                "side": "sell",
                "price": 0.11,
                "qty": 20,
                "ts": 1300,
                "raw": {"orderId": "o-sell", "origQty": 20, "status": "FILLED"},
            },
        ]
        _attach_fills_window(ent, fills, market="futures", open_position=True)
        self.assertEqual(ent["n_buys"], 1)
        self.assertEqual(ent["n_sells"], 1)
        self.assertAlmostEqual(ent["buy_orders"][0]["qty"], 100.0, places=6)
        ids = {str(x.get("order_id") or "") for x in ent["buy_orders"]}
        self.assertNotIn("o-partial", ids)

    def test_legacy_fill_without_order_id_still_counts(self):
        fills = [
            {"symbol": "XUSDT", "side": "buy", "price": 1.0, "qty": 2, "ts": 10},
        ]
        segs = segment_positions_from_fills(fills, symbol="XUSDT", market="spot")
        self.assertEqual(len(segs), 1)
        self.assertAlmostEqual(segs[0]["size_remaining"], 2.0, places=6)


class TestPositionsFeedEntities(unittest.TestCase):
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

    def _insert(self, **kw):
        from mexc_bot.learning.store import EventStore

        store = EventStore(self.db)
        store.insert_fill(**kw)
        return store

    @patch("mexc_bot.learning.fills.fetch_live_futures_opens", return_value=[])
    @patch("mexc_bot.learning.fills.fetch_live_futures_closed", return_value=[])
    @patch("mexc_bot.learning.fills.fetch_live_spot_balances", return_value=None)
    @patch(
        "mexc_bot.webapi.positions_enrich.ticker_24h",
        return_value={"price": 1.5, "changePercent": 0, "source": "test"},
    )
    def test_partial_order_absent_from_positions_feed(self, *_mocks):
        self._insert(
            user_id=99,
            exchange_trade_id="t-partial",
            symbol="ABCUSDT",
            market="spot",
            side="buy",
            price=1.0,
            qty=4.0,
            quote_qty=4.0,
            ts=1_700_000_000.0,
            raw={
                "orderId": "ord-partial",
                "origQty": 10,
                "executedQty": 4,
                "status": "PARTIALLY_FILLED",
            },
        )
        from mexc_bot.webapi.positions_enrich import list_position_entities

        ents = list_position_entities(99, include_closed=True)
        self.assertEqual(
            [e for e in ents if str(e.get("symbol") or "").upper() == "ABCUSDT"],
            [],
        )

    @patch("mexc_bot.learning.fills.fetch_live_futures_opens", return_value=[])
    @patch("mexc_bot.learning.fills.fetch_live_futures_closed", return_value=[])
    @patch("mexc_bot.learning.fills.fetch_live_spot_balances", return_value=None)
    @patch(
        "mexc_bot.webapi.positions_enrich.ticker_24h",
        return_value={"price": 1.5, "changePercent": 0, "source": "test"},
    )
    def test_full_order_is_one_spot_row_with_spot_math(self, *_mocks):
        raw = {
            "orderId": "ord-full",
            "origQty": 10,
            "executedQty": 10,
            "status": "FILLED",
        }
        self._insert(
            user_id=99,
            exchange_trade_id="t-a",
            symbol="ABCUSDT",
            market="spot",
            side="buy",
            price=1.0,
            qty=5.0,
            quote_qty=5.0,
            ts=1_700_000_000.0,
            raw=dict(raw, executedQty=5, status="PARTIALLY_FILLED"),
        )
        self._insert(
            user_id=99,
            exchange_trade_id="t-b",
            symbol="ABCUSDT",
            market="spot",
            side="buy",
            price=1.0,
            qty=5.0,
            quote_qty=5.0,
            ts=1_700_000_010.0,
            raw=raw,
        )
        from mexc_bot.webapi.positions_enrich import list_position_entities

        ents = list_position_entities(99, include_closed=True)
        rows = [e for e in ents if str(e.get("symbol") or "").upper() == "ABCUSDT"]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.get("book") or row.get("market"), "spot")
        self.assertEqual(row.get("math"), "spot")
        self.assertEqual(row.get("n_buys"), 1)
        self.assertAlmostEqual(float(row["size_remaining"]), 10.0, places=6)
        self.assertAlmostEqual(float(row["upnl_usd_est"]), 5.0, places=6)
        self.assertAlmostEqual(float(row["remaining_mark_usd"]), 15.0, places=6)
        self.assertEqual(row.get("contract_size"), 1.0)


class TestPositionsTabSplit(unittest.TestCase):
    def test_desk_js_splits_futures_and_spot_books(self):
        js = (ROOT / "mexc_bot/webapi/static/assets/desk.js").read_text()
        self.assertIn("pos-book-fut", js)
        self.assertIn("pos-book-spot", js)
        self.assertIn("function posBookOf", js)
        html = (ROOT / "mexc_bot/webapi/static/index.html").read_text()
        self.assertIn("desk.js?v=slicelab4", html)
        self.assertIn("desk.css?v=slicelab4", html)


if __name__ == "__main__":
    unittest.main()
