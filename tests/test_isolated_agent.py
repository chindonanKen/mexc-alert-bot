#!/usr/bin/env python3
"""Isolated dump agent: triggers, store, non-blocking queue."""

import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mexc_bot.investigators.triggers import (
    IsolatedDumpCriteria,
    should_investigate_isolated,
)
from mexc_bot.investigators.store import InvestigatorStore
from mexc_bot.investigators.queue import InvestigationJob, InvestigationQueue
from mexc_bot.investigators.agent import IsolatedDumpAgent, _base_from_symbol
from mexc_bot.investigators.radar import _extract_bases, _DELIST_RE


class TestTriggers(unittest.TestCase):
    def test_rejects_small_drop(self):
        self.assertFalse(
            should_investigate_isolated(
                drop_pct=-5,
                user_threshold_pct=5,
                velocity_band="PANIC",
                heat_dumping_count=1,
            )
        )

    def test_rejects_market_wide(self):
        self.assertFalse(
            should_investigate_isolated(
                drop_pct=-12,
                user_threshold_pct=5,
                velocity_band="PANIC",
                heat_dumping_count=8,
                watchlist_count=10,
            )
        )

    def test_rejects_grind(self):
        self.assertFalse(
            should_investigate_isolated(
                drop_pct=-12,
                user_threshold_pct=5,
                velocity_band="GRIND",
                heat_dumping_count=1,
            )
        )

    def test_accepts_extreme_isolated_panic(self):
        self.assertTrue(
            should_investigate_isolated(
                drop_pct=-12,
                user_threshold_pct=5,
                velocity_band="PANIC",
                heat_dumping_count=1,
                watchlist_count=20,
            )
        )

    def test_multiplier_floor(self):
        # 5% * 1.6 = 8 — need >= 8
        self.assertFalse(
            should_investigate_isolated(
                drop_pct=-7.5,
                user_threshold_pct=5,
                velocity_band="FAST",
                heat_dumping_count=0,
                criteria=IsolatedDumpCriteria(min_drop_pct=5, threshold_multiplier=1.6),
            )
        )
        self.assertTrue(
            should_investigate_isolated(
                drop_pct=-8.1,
                user_threshold_pct=5,
                velocity_band="FAST",
                heat_dumping_count=0,
                criteria=IsolatedDumpCriteria(min_drop_pct=5, threshold_multiplier=1.6),
            )
        )


class TestStoreAndQueue(unittest.TestCase):
    def test_queue_never_blocks(self):
        q = InvestigationQueue(maxsize=2)
        j = InvestigationJob(1, "X", "futures", -10.0)
        self.assertTrue(q.try_put(j))
        self.assertTrue(q.try_put(j))
        self.assertFalse(q.try_put(j))  # full → drop

    def test_investigation_and_expertise(self):
        tmp = tempfile.TemporaryDirectory()
        store = InvestigatorStore(Path(tmp.name) / "i.db")
        store.upsert_delist(
            exchange="binance",
            base="ABC",
            title="Binance Will Delist ABCUSDT",
            url="http://x",
            kind="delist",
            ts=time.time(),
            fingerprint="fp1",
        )
        hits = store.find_delists_for_base("ABC")
        self.assertGreaterEqual(len(hits), 1)
        iid = store.save_investigation(
            user_id=1,
            event_id=99,
            symbol="ABC_USDT",
            market="futures",
            drop_pct=-15,
            velocity_band="PANIC",
            heat_breadth=1,
            verdict="NEWS_RELATED",
            confidence=0.9,
            evidence=[{"source": "binance", "kind": "delist", "title": "x"}],
        )
        self.assertGreater(iid, 0)
        store.record_investigation_outcome(
            iid,
            event_id=99,
            horizon_seconds=3600,
            max_bounce_pct=0.5,
            max_dd_pct=-3.0,
            verdict="NEWS_RELATED",
            evidence=[{"source": "binance", "kind": "delist"}],
        )
        w = store.get_source_weight("binance", "delist")
        self.assertGreaterEqual(w, 0.5)
        tops = store.top_sources()
        self.assertTrue(any(t["source"] == "binance" for t in tops))
        tmp.cleanup()

    def test_base_extract(self):
        self.assertEqual(_base_from_symbol("BTC_USDT"), "BTC")
        self.assertEqual(_base_from_symbol("SIRENUSDT"), "SIREN")
        self.assertTrue(_DELIST_RE.search("Notice of Removal of Spot Trading Pairs"))
        bases = _extract_bases("Binance Will Delist ATA FARM MLN on 2026-05-27")
        self.assertTrue(any(b in bases for b in ("ATA", "FARM", "MLN")))


class TestAgentEnqueue(unittest.TestCase):
    def test_agent_filters_and_cools(self):
        tmp = tempfile.TemporaryDirectory()
        store = InvestigatorStore(Path(tmp.name) / "a.db")
        notes = []

        def notify(uid, text, parse_mode=None, reply_markup=None):
            notes.append(text)

        agent = IsolatedDumpAgent(
            store,
            notifier=notify,
            criteria=IsolatedDumpCriteria(min_drop_pct=8, max_heat_breadth=2),
            cooldown_seconds=60,
            notify_none=True,
        )
        # too small
        self.assertFalse(
            agent.maybe_enqueue(
                user_id=1,
                symbol="X_USDT",
                market="futures",
                drop_pct=-5,
                user_threshold_pct=5,
                velocity_band="PANIC",
                heat_breadth=1,
            )
        )
        # extreme isolated
        self.assertTrue(
            agent.maybe_enqueue(
                user_id=1,
                symbol="X_USDT",
                market="futures",
                drop_pct=-14,
                user_threshold_pct=5,
                velocity_band="PANIC",
                heat_breadth=1,
                event_id=1,
            )
        )
        # cooldown
        self.assertFalse(
            agent.maybe_enqueue(
                user_id=1,
                symbol="X_USDT",
                market="futures",
                drop_pct=-14,
                user_threshold_pct=5,
                velocity_band="PANIC",
                heat_breadth=1,
            )
        )
        tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
