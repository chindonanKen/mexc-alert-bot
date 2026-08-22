#!/usr/bin/env python3
"""Daily target report: hits merge, near-miss math, fire log durability."""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mexc_bot.reports.daily_targets import (
    closest_approach_in_bars,
    generate_daily_target_report,
    merge_hits,
    report_window,
    TargetHit,
)
from mexc_bot.reports.fire_log import TargetFireLog
from mexc_bot.storage import AlertStore


class TestReportWindow(unittest.TestCase):
    def test_window_is_24h_manila(self) -> None:
        # Fixed instant: 2026-08-12 10:00 Manila → last 6am is today 06:00 PHT
        import datetime
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("Asia/Manila")
        wall = datetime.datetime(2026, 8, 12, 10, 0, 0, tzinfo=tz)
        t0, t1, label = report_window(
            now=wall.timestamp(), tz_name="Asia/Manila", hour=6
        )
        self.assertEqual(label, "2026-08-12")
        self.assertAlmostEqual(t1 - t0, 86400.0, places=0)
        # End boundary is 06:00 Manila
        end = datetime.datetime.fromtimestamp(t1, tz=tz)
        self.assertEqual(end.hour, 6)
        self.assertEqual(end.minute, 0)


class TestClosestApproach(unittest.TestCase):
    def test_within_band(self) -> None:
        bars = [
            {"ts": 1000.0, "o": 100, "h": 105, "l": 99, "c": 102},
            {"ts": 2000.0, "o": 102, "h": 108, "l": 101, "c": 107},
        ]
        # target 110 → closest is 108 at ts 2000
        px, ts, dist = closest_approach_in_bars(bars, 110.0, 0, 3000)
        self.assertEqual(ts, 2000.0)
        self.assertEqual(px, 108.0)
        self.assertAlmostEqual(dist, (2 / 110) * 100, places=4)

    def test_outside_window_ignored(self) -> None:
        bars = [{"ts": 50.0, "o": 100, "h": 100, "l": 100, "c": 100}]
        self.assertIsNone(closest_approach_in_bars(bars, 100.0, 1000, 2000))


class TestFireLogAndReport(unittest.TestCase):
    def test_hits_and_open_targets(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "alerts.db"
            store = AlertStore(db)
            uid = 99
            store.add_alert(uid, "BTCUSDT", 100_000.0, market="spot")
            store.add_alert(uid, "ETHUSDT", 3_000.0, market="spot")

            fl = TargetFireLog(db)
            fl.log(
                uid,
                "SOLUSDT",
                "spot",
                target_price=140.0,
                fire_price=140.1,
                reason="crossed",
                ts=time.time() - 3600,
            )

            # Fake klines: ETH near 3% away
            class FakeK:
                def get_ohlcv(self, market, symbol, tf, limit=96):
                    if "ETH" in symbol:
                        now = time.time()
                        return [
                            {
                                "ts": now - 7200,
                                "o": 2910,
                                "h": 2920,
                                "l": 2900,
                                "c": 2915,
                            }
                        ]
                    return []

                def close(self):
                    pass

            now = time.time()
            report = generate_daily_target_report(
                db_path=db,
                user_id=uid,
                window_start=now - 86400,
                window_end=now,
                tz_name="UTC",
                near_pct=5.0,
                klines=FakeK(),
            )
            self.assertEqual(report.open_targets, 2)
            self.assertEqual(len(report.hits), 1)
            self.assertEqual(report.hits[0].symbol, "SOLUSDT")
            self.assertTrue(any(n.symbol == "ETHUSDT" for n in report.near_misses))
            text = report.to_text()
            self.assertIn("TARGETS HIT", text)
            self.assertIn("NEAR MISSES", text)
            self.assertIn("SOLUSDT", text)

    def test_merge_dedupe(self) -> None:
        ts = 1_700_000_000.0  # fixed — time.time() flakes at minute boundaries
        a = TargetHit("X", "spot", 1, 1, ts, source="target_fire_log")
        b = TargetHit("X", "spot", 1, 1, ts + 10, source="learning_events")
        m = merge_hits([a], [b])
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0].source, "target_fire_log")


if __name__ == "__main__":
    unittest.main()
