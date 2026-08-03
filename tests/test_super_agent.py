#!/usr/bin/env python3
"""AD Super-Agent: beliefs, judge_fire, outcome→edge, exec training."""

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

from mexc_bot.learning.store import EventStore
from mexc_bot.learning.beliefs import (
    BeliefEngine,
    outcome_label,
    edge_from_counts,
    heat_bin,
    drop_bin,
)
from mexc_bot.learning.chart_features import (
    rsi_wilder,
    setup_posterior,
    compute_fire_features,
)


class TestBeliefMath(unittest.TestCase):
    def test_outcome_label_and_edge(self):
        self.assertEqual(outcome_label(3.0, -1.0), "good")
        self.assertEqual(outcome_label(0.2, -5.0), "bad")
        e = edge_from_counts(8, 2, 10)
        self.assertGreater(e, 0)
        self.assertEqual(heat_bin(0), "isolated")
        self.assertEqual(heat_bin(6), "broad")
        self.assertEqual(drop_bin(-7), "std")

    def test_rsi_wilder_runs(self):
        closes = [float(100 + i % 5 - 2) for i in range(40)]
        r = rsi_wilder(closes, 14)
        self.assertIsNotNone(r[-1])
        self.assertTrue(0 <= r[-1] <= 100)

    def test_setup_posterior(self):
        p = setup_posterior(0.8, 4.0, -1.0, ad_median=5.0)
        self.assertGreater(p, 0.5)


class TestBeliefEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "b.db"
        self.store = EventStore(self.db)
        self.eng = BeliefEngine(self.store)
        self.uid = 8630949601
        os.environ["ALERTS_FILE"] = str(self.db)
        os.environ["DESK_USER_ID"] = str(self.uid)

    def tearDown(self):
        self.tmp.cleanup()

    def test_outcome_updates_setup_edge(self):
        eid = self.store.log_event(
            self.uid,
            "mover_peak",
            "AAA_USDT",
            "futures",
            price=100,
            drop_pct=-8,
            velocity_band="PANIC",
            heat_breadth=5,
        )
        lab = self.eng.update_from_outcome(
            self.uid, eid, max_bounce_pct=4.0, max_dd_pct=-1.0, horizon_seconds=900
        )
        self.assertEqual(lab, "good")
        # second same horizon ignored
        lab2 = self.eng.update_from_outcome(
            self.uid, eid, max_bounce_pct=4.0, max_dd_pct=-1.0, horizon_seconds=900
        )
        self.assertIsNone(lab2)
        b = self.eng.get_setup_belief(self.uid, "PANIC", "broad", "std")
        self.assertEqual(b["n"], 1)
        self.assertEqual(b["n_good"], 1)
        self.assertIsNotNone(b["edge"])
        t = self.eng.get_ticker_belief(self.uid, "AAA_USDT", "futures")
        self.assertEqual(t["n_fires"], 1)

    def test_judge_grind_no_trade(self):
        eid = self.store.log_event(
            self.uid,
            "mover_peak",
            "SLOW_USDT",
            "futures",
            price=50,
            drop_pct=-4,
            velocity_band="GRIND",
            heat_breadth=0,
        )
        ev = self.store.recent_events(self.uid, limit=1)[0]
        j = self.eng.judge_fire(self.uid, ev, chart_features={"ok": False})
        self.assertEqual(j["setup"]["verdict"], "no_trade")
        self.assertEqual(j["size_hint"], "none")
        self.assertTrue(j["cite"])
        self.assertTrue(j.get("self_critique"))

    def test_human_correction_changes_verdict(self):
        eid = self.store.log_event(
            self.uid,
            "mover_peak",
            "FIX_USDT",
            "futures",
            price=10,
            drop_pct=-8,
            velocity_band="PANIC",
            heat_breadth=1,
        )
        ev = self.store.recent_events(self.uid, limit=1)[0]
        j = self.eng.judge_fire(self.uid, ev)
        cid = self.eng.open_case(self.uid, ev, j)
        out = self.eng.apply_human_correction(
            self.uid,
            case_id=cid,
            correct_verdict="no_trade",
            reason="isolated dump not market-wide",
        )
        self.assertEqual(out["correct_verdict"], "no_trade")
        self.assertEqual(out["judgment"]["setup"]["verdict"], "no_trade")
        self.assertIsNotNone(out["judgment"].get("human_override"))

    def test_judge_panic_broad_with_edge(self):
        # train setup cell first
        for i in range(6):
            eid = self.store.log_event(
                self.uid,
                "mover_peak",
                f"P{i}_USDT",
                "futures",
                price=10,
                drop_pct=-7,
                velocity_band="PANIC",
                heat_breadth=5,
            )
            self.eng.update_from_outcome(
                self.uid,
                eid,
                max_bounce_pct=3.0,
                max_dd_pct=-0.5,
                horizon_seconds=900,
            )
        eid = self.store.log_event(
            self.uid,
            "mover_peak",
            "NOW_USDT",
            "futures",
            price=10,
            drop_pct=-7,
            velocity_band="PANIC",
            heat_breadth=5,
        )
        ev = dict(self.store.recent_events(self.uid, limit=1)[0])
        j = self.eng.judge_fire(self.uid, ev)
        self.assertIn(j["setup"]["verdict"], ("take_layers", "take_scout"))
        self.assertGreaterEqual(j["setup"]["n"], 5)

    def test_fomo_process_hurts_exec_edge(self):
        tid = self.store.journal_open(
            self.uid, "FOMO_USDT", "futures", entry_avg=100, notes="[fomo]"
        )
        self.store.journal_close(self.uid, trade_id=tid, exit_avg=105)
        from mexc_bot.learning.trades import get_trade_dossier

        d = get_trade_dossier(self.store, self.uid, tid)
        # force linked empty
        d["linked_events"] = []
        d["notes"] = "[fomo]"
        d["n_buys"] = 1
        d["entry_avg"] = 103
        d["primary_event_id"] = None
        out = self.eng.update_from_trade_close(
            self.uid, tid, dossier=d, process_tags=["fomo"]
        )
        self.assertTrue(out["updated"])
        self.assertLessEqual(out["exec_score"], 0)
        t = self.eng.get_ticker_belief(self.uid, "FOMO_USDT", "futures")
        self.assertLessEqual(float(t["exec_edge"]), 0)

    def test_chart_features_soft_no_network(self):
        with patch(
            "mexc_bot.learning.chart_features.fetch_bars", return_value=[]
        ):
            f = compute_fire_features(
                market="futures",
                symbol="X_USDT",
                fire_px=1.0,
                fire_ts=time.time(),
            )
        self.assertFalse(f.get("ok"))


class TestReconstructFills(unittest.TestCase):
    def test_avg_cost_after_partial_sells(self):
        from mexc_bot.learning.trades import reconstruct_open_from_fills

        fills = [
            {"symbol": "AAAUSDT", "side": "buy", "price": 10.0, "qty": 10, "ts": 100},
            {"symbol": "AAAUSDT", "side": "buy", "price": 12.0, "qty": 10, "ts": 200},
            {"symbol": "AAAUSDT", "side": "sell", "price": 14.0, "qty": 5, "ts": 300},
            {"symbol": "AAAUSDT", "side": "buy", "price": 11.0, "qty": 5, "ts": 400},
        ]
        # After: bought 10@10 + 10@12 = 20 @11 avg, sell 5 → 15 left @11, buy 5@11 → 20 @11
        r = reconstruct_open_from_fills(fills, symbol="AAAUSDT", market="spot")
        self.assertTrue(r["is_open"])
        self.assertAlmostEqual(r["size_remaining"], 20.0, places=5)
        self.assertAlmostEqual(r["entry_avg"], 11.0, places=5)
        self.assertEqual(r["n_buys"], 3)
        self.assertEqual(r["n_sells"], 1)


class TestSegmentPositionEntities(unittest.TestCase):
    """Fully closed cycles are discrete entities; next open is separate."""

    def test_two_closed_cycles_then_open(self):
        from mexc_bot.learning.trades import segment_positions_from_fills

        fills = [
            # Cycle 1 — success
            {"symbol": "SYNUSDT", "side": "buy", "price": 1.0, "qty": 100, "ts": 1000},
            {"symbol": "SYNUSDT", "side": "sell", "price": 1.2, "qty": 100, "ts": 2000},
            # Cycle 2 — miss
            {"symbol": "SYNUSDT", "side": "buy", "price": 1.1, "qty": 50, "ts": 3000},
            {"symbol": "SYNUSDT", "side": "sell", "price": 0.9, "qty": 50, "ts": 4000},
            # Cycle 3 — still open
            {"symbol": "SYNUSDT", "side": "buy", "price": 1.0, "qty": 20, "ts": 5000},
            {"symbol": "SYNUSDT", "side": "buy", "price": 0.95, "qty": 10, "ts": 5100},
        ]
        segs = segment_positions_from_fills(fills, symbol="SYNUSDT", market="spot")
        self.assertEqual(len(segs), 3)
        # newest first
        self.assertEqual(segs[0]["status"], "open")
        self.assertAlmostEqual(segs[0]["size_remaining"], 30.0, places=5)
        self.assertEqual(segs[0]["outcome"], "open")
        self.assertEqual(segs[0]["n_buys"], 2)
        self.assertEqual(segs[0]["n_sells"], 0)

        closed = [s for s in segs if s["status"] == "closed"]
        self.assertEqual(len(closed), 2)
        # newest closed first among closed? overall sort by closed_at/opened_at
        by_open = sorted(closed, key=lambda x: x["opened_at"])
        c1, c2 = by_open[0], by_open[1]
        self.assertEqual(c1["outcome"], "success")
        self.assertAlmostEqual(c1["realized_pnl_pct"], 20.0, places=2)
        self.assertEqual(c1["n_buys"], 1)
        self.assertEqual(c1["n_sells"], 1)
        self.assertEqual(c2["outcome"], "miss")
        self.assertAlmostEqual(c2["realized_pnl_pct"], -100.0 * (1.1 - 0.9) / 1.1, places=2)
        # closed cycle does not mix cycle-3 buys
        self.assertEqual(c2["n_buys"], 1)
        self.assertAlmostEqual(c2["size_qty"], 50.0, places=5)

    def test_partial_sells_one_entity_until_flat(self):
        from mexc_bot.learning.trades import segment_positions_from_fills

        fills = [
            {"symbol": "XUSDT", "side": "buy", "price": 10.0, "qty": 10, "ts": 100},
            {"symbol": "XUSDT", "side": "sell", "price": 11.0, "qty": 4, "ts": 200},
            {"symbol": "XUSDT", "side": "sell", "price": 12.0, "qty": 6, "ts": 300},
            # new cycle after flat
            {"symbol": "XUSDT", "side": "buy", "price": 9.0, "qty": 5, "ts": 400},
        ]
        segs = segment_positions_from_fills(fills, symbol="XUSDT", market="spot")
        self.assertEqual(len(segs), 2)
        closed = next(s for s in segs if s["status"] == "closed")
        open_ = next(s for s in segs if s["status"] == "open")
        self.assertEqual(closed["n_buys"], 1)
        self.assertEqual(closed["n_sells"], 2)
        self.assertEqual(closed["outcome"], "success")
        self.assertAlmostEqual(open_["size_remaining"], 5.0, places=5)
        self.assertEqual(open_["n_buys"], 1)
        self.assertEqual(open_["n_sells"], 0)

    def test_newest_first_ordering(self):
        from mexc_bot.learning.trades import segment_positions_from_fills

        fills = [
            {"symbol": "YUSDT", "side": "buy", "price": 1, "qty": 1, "ts": 100},
            {"symbol": "YUSDT", "side": "sell", "price": 2, "qty": 1, "ts": 200},
            {"symbol": "YUSDT", "side": "buy", "price": 1, "qty": 1, "ts": 300},
            {"symbol": "YUSDT", "side": "sell", "price": 0.5, "qty": 1, "ts": 400},
        ]
        segs = segment_positions_from_fills(fills, symbol="YUSDT", market="spot")
        self.assertEqual(len(segs), 2)
        self.assertGreaterEqual(
            float(segs[0].get("closed_at") or 0),
            float(segs[1].get("closed_at") or 0),
        )
        self.assertEqual(segs[0]["outcome"], "miss")
        self.assertEqual(segs[1]["outcome"], "success")

    def test_float_dust_still_closes_cycle(self):
        """Prod bug: residual ~1e-12 after full exit never closed SYN mega-cycle."""
        from mexc_bot.learning.trades import segment_positions_from_fills

        # Simulate avg-cost dust: buy 3 lots, sell almost all leaving 1e-11
        fills = [
            {"symbol": "SYNUSDT", "side": "buy", "price": 0.3, "qty": 10000, "ts": 1000},
            {"symbol": "SYNUSDT", "side": "buy", "price": 0.35, "qty": 5000, "ts": 1100},
            {"symbol": "SYNUSDT", "side": "sell", "price": 0.38, "qty": 14999.99999999, "ts": 2000},
            # new real cycle after dust close
            {"symbol": "SYNUSDT", "side": "buy", "price": 0.09, "qty": 40000, "ts": 3000},
        ]
        segs = segment_positions_from_fills(fills, symbol="SYNUSDT", market="spot")
        self.assertEqual(len(segs), 2)
        closed = next(s for s in segs if s["status"] == "closed")
        open_ = next(s for s in segs if s["status"] == "open")
        self.assertEqual(closed["outcome"], "success")
        self.assertAlmostEqual(open_["size_remaining"], 40000.0, places=3)
        self.assertEqual(open_["n_buys"], 1)

    def test_spot_and_futures_books_isolated(self):
        from mexc_bot.learning.trades import segment_positions_from_fills

        fills = [
            {
                "symbol": "SYNUSDT",
                "market": "spot",
                "side": "buy",
                "price": 0.1,
                "qty": 100,
                "ts": 100,
            },
            {
                "symbol": "SYN_USDT",
                "market": "futures",
                "side": "buy",
                "price": 0.1,
                "qty": 50,
                "ts": 200,
            },
            {
                "symbol": "SYN_USDT",
                "market": "futures",
                "side": "sell",
                "price": 0.12,
                "qty": 50,
                "ts": 300,
            },
        ]
        fut = segment_positions_from_fills(fills, symbol="SYN_USDT", market="futures")
        spot = segment_positions_from_fills(fills, symbol="SYNUSDT", market="spot")
        self.assertEqual(len(fut), 1)
        self.assertEqual(fut[0]["status"], "closed")
        self.assertEqual(fut[0]["outcome"], "success")
        self.assertEqual(len(spot), 1)
        self.assertEqual(spot[0]["status"], "open")
        self.assertAlmostEqual(spot[0]["size_remaining"], 100.0, places=5)


class TestFuturesDealMap(unittest.TestCase):
    def test_side_codes_to_buy_sell(self):
        from mexc_bot.exchange_private import futures_deal_to_fill_row

        buy = futures_deal_to_fill_row(
            {
                "id": "1",
                "symbol": "SYN_USDT",
                "side": 1,
                "vol": 10,
                "price": 0.1,
                "timestamp": 1_700_000_000_000,
            },
            1,
        )
        sell = futures_deal_to_fill_row(
            {
                "id": "2",
                "symbol": "SYN_USDT",
                "side": 3,
                "vol": 10,
                "price": 0.12,
                "timestamp": 1_700_000_100_000,
                "reduceOnly": True,
            },
            1,
        )
        self.assertEqual(buy["side"], "buy")
        self.assertEqual(buy["market"], "futures")
        self.assertEqual(buy["exchange_trade_id"], "f:1")
        self.assertEqual(sell["side"], "sell")
        self.assertEqual(sell["symbol"], "SYN_USDT")


class TestFuturesOpenAuthority(unittest.TestCase):
    def test_ghost_opens_dropped_when_exchange_known(self):
        from unittest.mock import patch

        from mexc_bot.webapi.positions_enrich import _reconcile_futures_with_exchange

        entities = [
            {
                "symbol": "SYN_USDT",
                "market": "futures",
                "status": "open",
                "is_open": True,
                "size_remaining": 9999,
                "entry_avg": 0.1,
            },
            {
                "symbol": "LAB_USDT",
                "market": "futures",
                "status": "open",
                "is_open": True,
                "size_remaining": 222,
            },
            {
                "symbol": "LAB_USDT",
                "market": "futures",
                "status": "closed",
                "outcome": "success",
                "realized_pnl_pct": 1.0,
            },
            {
                "symbol": "SYNUSDT",
                "market": "spot",
                "status": "open",
                "is_open": True,
                "size_remaining": 100,
            },
        ]
        exch = [
            {
                "symbol": "SYN_USDT",
                "hold_vol": 4352.0,
                "entry_avg": 0.0998,
                "leverage": 1,
                "realized": 28.0,
            }
        ]
        with patch(
            "mexc_bot.learning.fills.fetch_live_futures_opens", return_value=exch
        ):
            out = _reconcile_futures_with_exchange(entities, store=None, user_id=1)
        opens = [e for e in out if e.get("status") == "open"]
        fut_opens = [e for e in opens if e.get("market") == "futures"]
        self.assertEqual(len(fut_opens), 1)
        self.assertEqual(fut_opens[0]["symbol"], "SYN_USDT")
        self.assertAlmostEqual(fut_opens[0]["size_remaining"], 4352.0)
        self.assertTrue(fut_opens[0].get("exchange_hold"))
        # closed futures + spot open kept
        self.assertTrue(any(e.get("status") == "closed" for e in out))
        self.assertTrue(
            any(e.get("market") == "spot" and e.get("status") == "open" for e in out)
        )


class TestFatalNewsJudge(unittest.TestCase):
    def test_hard_fatal_forces_no_trade(self):
        from mexc_bot.learning.fatal_news import apply_fatal_to_verdict

        hard = {
            "fatal": True,
            "hard_fatal": True,
            "primary": {"class": "HACK", "severity": "fatal", "title": "Protocol exploited"},
        }
        out = apply_fatal_to_verdict("take_layers", "full_layers", hard)
        self.assertEqual(out["verdict"], "no_trade")
        self.assertTrue(out["overridden"])

    def test_symbol_match_exact(self):
        from mexc_bot.learning.fatal_news import _symbol_matches_news

        self.assertTrue(_symbol_matches_news("BTC_USDT", "BTC", "BTC delisting"))
        self.assertFalse(_symbol_matches_news("ETH", "ETHFI", "ETHFI hack"))
        self.assertFalse(_symbol_matches_news("ETH_USDT", "ETHFI_USDT", "ETHFI delist"))


class TestChartReader(unittest.TestCase):
    def test_read_chart_with_synthetic_bars(self):
        from mexc_bot.learning.chart_reader import read_chart

        bars = []
        px = 100.0
        for i in range(80):
            # slow grind down then dump
            if i < 50:
                px = 100 - i * 0.1
            else:
                px = px * 0.985
            bars.append(
                {
                    "ts": 1_700_000_000 + i * 300,
                    "o": px * 1.001,
                    "h": px * 1.005,
                    "l": px * 0.99,
                    "c": px,
                    "v": 1000 + i * 10,
                }
            )
        with patch(
            "mexc_bot.learning.chart_reader.fetch_bars",
            side_effect=lambda m, s, tf, limit=96: bars,
        ):
            r = read_chart(
                "futures",
                "TEST_USDT",
                fire_price=bars[-1]["c"],
                peak_price=100.0,
                velocity_band="PANIC",
                heat_breadth=4,
            )
        self.assertTrue(r.get("ok"))
        self.assertIn("thesis", r)
        self.assertIn("regime", r)
        self.assertIn("ad_zone", r)
        self.assertIn("invalidation", r)
        self.assertIn("levels", r)
        self.assertTrue(len(r["thesis"]) > 80)


class TestAgentApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "a.db"
        os.environ["ALERTS_FILE"] = str(self.db)
        os.environ["DESK_USER_ID"] = "8630949601"
        self.store = EventStore(self.db)
        self.uid = 8630949601

    def tearDown(self):
        self.tmp.cleanup()

    def test_judge_and_bundle_and_coach(self):
        from mexc_bot.webapi import learning_api

        eid = self.store.log_event(
            self.uid,
            "mover_peak",
            "SOL_USDT",
            "futures",
            price=100,
            drop_pct=-9,
            velocity_band="PANIC",
            heat_breadth=4,
        )
        with patch(
            "mexc_bot.webapi.learning_api.compute_fire_features",
            return_value={
                "ok": True,
                "setup_prior": 0.7,
                "ad_zone": "at_ad",
                "vol_flag": "expand",
                "rsi_now_5m": 28,
                "div_bull": True,
                "sharp_score": 0.8,
            },
        ):
            out = learning_api.judge_fire(event_id=eid, user_id=self.uid)
        self.assertIn("judgment", out)
        self.assertEqual(out["judgment"]["agent"], "AD-SuperAgent-v1")
        self.assertTrue(out["judgment"]["cite"])
        bundle = learning_api.agent_bundle(self.uid)
        self.assertEqual(bundle["agent"], "AD-SuperAgent-v1")
        self.assertIn("beliefs", bundle)
        self.assertIn("active_case", bundle)
        coach = learning_api.coach_ask("judge latest panic", user_id=self.uid)
        self.assertIn("SUPER-AGENT", coach["reply"])
        self.assertNotIn("filled at", coach["reply"].lower())


if __name__ == "__main__":
    unittest.main()
