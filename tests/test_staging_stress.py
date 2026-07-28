#!/usr/bin/env python3
"""
Staging stress / limit tests — no Telegram user session required.

Pushes learning store, history, news classify, fills, keyboards, and
optional bot→chat notify flood (needs env; does NOT call getUpdates).

Run:
  python3 tests/test_staging_stress.py
  STRESS_EVENTS=2000 python3 tests/test_staging_stress.py

Optional live notify (bot sends TO you; won't steal polling):
  STAGING_BOT_TOKEN=... STAGING_CHAT_ID=... python3 tests/test_staging_stress.py --notify
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mexc_bot.assistant.ux import fire_action_keyboard, parse_callback, parse_plain_intent
from mexc_bot.exchange_private import trade_to_fill_row
from mexc_bot.learning.store import EventStore
from mexc_bot.movers.history import PriceHistory
from mexc_bot.news.classify import classify_headline, extract_symbol_hints, TRUST_OFFICIAL, TRUST_REKT
from mexc_bot.news.store import NewsStore


N_EVENTS = int(os.getenv("STRESS_EVENTS", "500"))
N_THREADS = int(os.getenv("STRESS_THREADS", "8"))
USER = 900001


class TestStagingStress(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "stress.db"
        self.store = EventStore(self.db)
        self.news = NewsStore(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_bulk_event_log_and_label(self):
        t0 = time.perf_counter()
        ids = []
        for i in range(N_EVENTS):
            eid = self.store.log_event(
                USER,
                "mover_peak" if i % 2 == 0 else "mover_step",
                f"COIN{i % 50}_USDT",
                "futures",
                price=1.0 + i * 0.001,
                ref_price=1.1,
                drop_pct=-5.0 - (i % 10),
                velocity_band=["PANIC", "FAST", "GRIND"][i % 3],
                mode="peak" if i % 2 == 0 else "step",
            )
            self.assertGreater(eid, 0)
            ids.append(eid)
        elapsed = time.perf_counter() - t0
        self.assertEqual(len(ids), N_EVENTS)
        # label every 3rd
        for eid in ids[::3]:
            self.assertTrue(
                self.store.label_event(eid, USER, action="took" if eid % 2 else "skip")
            )
        recent = self.store.recent_events(USER, limit=50)
        self.assertEqual(len(recent), 50)
        print(f"  bulk {N_EVENTS} events in {elapsed:.2f}s ({N_EVENTS/elapsed:.0f}/s)")

    def test_concurrent_event_writes(self):
        def worker(n: int):
            local_ids = []
            for i in range(n):
                eid = self.store.log_event(
                    USER + (n % 3),
                    "target",
                    f"T{i}USDT",
                    "spot",
                    price=100.0,
                    ref_price=100.0,
                    mode="crossed",
                )
                local_ids.append(eid)
            return local_ids

        per = max(20, N_EVENTS // N_THREADS)
        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=N_THREADS) as ex:
            futs = [ex.submit(worker, per) for _ in range(N_THREADS)]
            all_ids = []
            for f in as_completed(futs):
                all_ids.extend(f.result())
        elapsed = time.perf_counter() - t0
        self.assertTrue(all(i > 0 for i in all_ids))
        self.assertEqual(len(all_ids), per * N_THREADS)
        print(
            f"  concurrent {len(all_ids)} writes "
            f"({N_THREADS} threads) in {elapsed:.2f}s"
        )

    def test_many_outcomes_pending(self):
        now = time.time()
        for i in range(100):
            self.store.log_event(
                USER,
                "mover_peak",
                f"O{i}_USDT",
                "futures",
                ts=now - 2000,
                price=10.0,
                mode="peak",
            )
        pending = self.store.pending_outcomes([900, 3600], now=now, limit=500)
        self.assertGreater(len(pending), 50)
        for p in pending[:50]:
            self.store.record_outcome(
                p["event_id"],
                p["horizon_seconds"],
                max_bounce_pct=2.0,
                max_dd_pct=-1.0,
                last_price=10.2,
            )
        pending2 = self.store.pending_outcomes([900], now=now, limit=500)
        # fewer pending for 900 after recording
        self.assertLess(len([x for x in pending2 if x["horizon_seconds"] == 900]), len(pending))

    def test_price_history_many_series(self):
        h = PriceHistory(max_age_seconds=900)
        t0 = time.time()
        for s in range(200):
            sym = f"S{s}_USDT"
            for j in range(50):
                h.record("futures", sym, 100.0 - j * 0.05, ts=t0 - 800 + j * 10)
        for s in range(200):
            dd = h.peak_drawdown("futures", f"S{s}_USDT", 900, now=t0)
            self.assertIsNotNone(dd)
        print("  200 series × 50 samples peak_drawdown OK")

    def test_news_classify_volume_and_dedupe(self):
        titles_ok = [
            "MEXC Will Delist ABCUSDT",
            "Protocol XYZ exploited and drained",
            "Project announces wind-down and cease operations",
            "Confirmed rug pull on TOKEN",
        ]
        titles_noise = [
            "Analyst says price might crash",
            "Could go bullish next week",
            "Community FUD spreads rumor",
        ]
        for t in titles_ok:
            self.assertIsNotNone(
                classify_headline(t, source_trust=TRUST_OFFICIAL if "Delist" in t else TRUST_REKT)
            )
        for t in titles_noise:
            self.assertIsNone(classify_headline(t, source_trust="aggregate"))

        for i in range(200):
            fp = f"fp-{i % 20}"  # force collisions
            self.news.insert(
                symbol="ABC",
                class_="DELIST",
                severity="fatal",
                title=f"Delist {i}",
                url="http://x",
                source="test",
                source_trust="official",
                fingerprint=fp if i >= 20 else f"unique-{i}",
            )
        # unique fingerprints 0-19 + first insert per fp-0..19 after
        recent = self.news.recent(limit=100)
        self.assertLessEqual(len(recent), 40)

    def test_fill_dedupe_storm(self):
        for i in range(300):
            trade = {
                "id": str(i % 50),  # heavy dupes
                "symbol": "ETHUSDT",
                "price": "2000",
                "qty": "0.1",
                "isBuyer": i % 2 == 0,
                "time": int(time.time() * 1000),
            }
            row = trade_to_fill_row(trade, USER)
            self.store.insert_fill(**{**row, "raw": trade})
        fills = self.store.recent_fills(USER, limit=100)
        self.assertEqual(len(fills), 50)  # unique trade ids

    def test_keyboard_and_callback_volume(self):
        for eid in range(1, 201):
            kb = fire_action_keyboard(eid)
            self.assertIsNotNone(kb)
            self.assertEqual(parse_callback(f"L:t:{eid}")[0], "took")
            self.assertEqual(parse_callback(f"L:s:{eid}")[0], "skip")
        for phrase in ("took", "skip", "later", "brief", "coach panic", "desk", "pride"):
            self.assertIsNotNone(parse_plain_intent(phrase))
        bases = {f"C{i}" for i in range(100)}
        hints = extract_symbol_hints("Delist C42 and C7 today", bases)
        self.assertIn("C42", hints)

    def test_label_latest_under_load(self):
        for i in range(100):
            self.store.log_event(
                USER, "mover_peak", "HOT_USDT", "futures", price=1.0, drop_pct=-8
            )
        # rapid labels on latest
        for _ in range(20):
            eid = self.store.label_latest(USER, action="took")
            self.assertIsNotNone(eid)


def optional_notify_flood():
    """Bot API sendMessage flood — does not use getUpdates (safe alongside staging)."""
    token = os.getenv("STAGING_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("STAGING_CHAT_ID")
    if not token or not chat:
        print("SKIP notify flood (set STAGING_BOT_TOKEN + STAGING_CHAT_ID)")
        return
    import requests

    n = int(os.getenv("STRESS_NOTIFY_N", "5"))
    ok = 0
    for i in range(n):
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": int(chat),
                "text": f"STRESS TEST {i+1}/{n} — ignore (staging limit test)",
            },
            timeout=30,
        )
        if r.status_code == 200 and r.json().get("ok"):
            ok += 1
        time.sleep(0.35)  # soft rate limit
    print(f"Notify flood: {ok}/{n} delivered to chat {chat}")
    if ok < n:
        raise SystemExit(f"notify flood incomplete {ok}/{n}")


if __name__ == "__main__":
    print(f"STRESS_EVENTS={N_EVENTS} STRESS_THREADS={N_THREADS}")
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if os.environ.get("STRESS_NOTIFY") or "--notify" in sys.argv:
        optional_notify_flood()
    sys.exit(0 if result.wasSuccessful() else 1)
