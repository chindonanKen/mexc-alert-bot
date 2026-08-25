#!/usr/bin/env python3
"""Week-1 student DECIDE: skip-on-no-repeat, one copy, Manila bar times.

Fixtures only — no live MEXC network.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mexc_bot.learning.store import EventStore
from mexc_bot.learning.student_decide import (
    TZ_NAME,
    decide_book,
    decide_from_bars,
    decide_symbol,
    live_copy_text,
    manila_label,
    should_paper_fill,
)
from mexc_bot.learning.student_paper import (
    StudentPaperBook,
    entry_notice_text,
    watch_once,
)

MANILA = ZoneInfo("Asia/Manila")
# First 15m bar open = 2026-08-20 10:00 PHT. Peak of first dump is 14:00 PHT.
T0 = datetime(2026, 8, 20, 10, 0, tzinfo=MANILA)
TF_SEC = 15 * 60
PEAK1_I = 16  # 10:00 + 16*15m = 14:00 PHT


def _bar(i: int, o: float, h: float, l: float, c: float, v: float = 100.0) -> dict:
    ts = (T0 + timedelta(seconds=i * TF_SEC)).timestamp()
    return {"ts": ts, "o": o, "h": h, "l": l, "c": c, "v": v}


def _green(i: int, lo: float, hi: float, v: float = 100.0) -> dict:
    return _bar(i, lo, hi, lo, hi, v)


def _red(i: int, hi: float, lo: float, v: float = 100.0) -> dict:
    return _bar(i, hi, hi, lo, lo, v)


def fixture_repeat() -> list:
    """Two finished dump-and-bounce cycles, then a new pump high at 1.25."""
    bars = []
    # Quiet pad 10:00–13:45, then rise into 14:00 PHT high 1.20
    px = 1.08
    for i in range(PEAK1_I):
        nxt = px + 0.004
        bars.append(_green(i, px, min(nxt, 1.19), 80))
        px = min(nxt, 1.19)
    bars.append(_green(PEAK1_I, 1.19, 1.20, 90))  # 14:00 PHT high
    # Dump 1.20 → 1.00 (4 reds, expand vol), then pump
    dump1 = [(1.20, 1.14), (1.14, 1.09), (1.09, 1.04), (1.04, 1.00)]
    for k, (hi, lo) in enumerate(dump1, start=PEAK1_I + 1):
        bars.append(_red(k, hi, lo, 220))
    i = PEAK1_I + 1 + len(dump1)
    bounce1 = [(1.00, 1.06), (1.06, 1.11), (1.11, 1.14)]
    for hi, lo in [(a, b) for a, b in bounce1]:
        bars.append(_green(i, hi, lo, 110))
        i += 1
    # Second cycle: pump 1.16, dump to 0.96 (4 reds), bounce
    bars.append(_green(i, 1.14, 1.16, 100))
    high2_i = i
    i += 1
    dump2 = [(1.16, 1.10), (1.10, 1.05), (1.05, 1.00), (1.00, 0.96)]
    for hi, lo in dump2:
        bars.append(_red(i, hi, lo, 210))
        i += 1
    bounce2 = [(0.96, 1.04), (1.04, 1.10), (1.10, 1.16), (1.16, 1.22), (1.22, 1.25)]
    for lo, hi in bounce2:
        bars.append(_green(i, lo, hi, 120))
        i += 1
    # Live: two reds, still above the copy bottom (wait)
    bars.append(_red(i, 1.25, 1.22, 130))
    i += 1
    bars.append(_red(i, 1.22, 1.19, 130))
    _ = high2_i
    return bars


def fixture_tagged_habit() -> list:
    """Repeat + 4 live reds that tag the copy with this chart's expand finish."""
    bars = fixture_repeat()
    i = len(bars)
    bars.append(_red(i, 1.19, 1.12, 220))
    bars.append(_red(i + 1, 1.12, 1.05, 220))
    return bars


def fixture_tagged_short_of_habit() -> list:
    """Copy tags, but live reds are short of THIS chart's finish."""
    bars = fixture_repeat()[:-2]
    i = len(bars)
    bars.append(_red(i, 1.25, 1.10, 220))
    bars.append(_red(i + 1, 1.10, 1.05, 220))
    return bars


def fixture_no_repeat_grind() -> list:
    """Slow bleed — dumps never bounce. No line."""
    bars = []
    px = 1.20
    bars.append(_green(0, 1.18, 1.20, 80))
    for i in range(1, 40):
        nxt = px - 0.006
        bars.append(_red(i, px, nxt, 70))
        px = nxt
    return bars


def fixture_one_bounce_only() -> list:
    """One dump-bounce is not a repeat."""
    bars = []
    px = 1.08
    for i in range(PEAK1_I):
        nxt = px + 0.004
        bars.append(_green(i, px, min(nxt, 1.19), 80))
        px = min(nxt, 1.19)
    bars.append(_green(PEAK1_I, 1.19, 1.20, 90))
    dump1 = [(1.20, 1.14), (1.14, 1.09), (1.09, 1.04), (1.04, 1.00)]
    for k, (hi, lo) in enumerate(dump1, start=PEAK1_I + 1):
        bars.append(_red(k, hi, lo, 200))
    i = PEAK1_I + 1 + len(dump1)
    for lo, hi in [(1.00, 1.08), (1.08, 1.13)]:
        bars.append(_green(i, lo, hi, 100))
        i += 1
    return bars


class TestManilaTimes(unittest.TestCase):
    def test_known_utc_is_pht(self) -> None:
        # 2026-08-20 06:00 UTC = 14:00 Asia/Manila
        utc = datetime(2026, 8, 20, 6, 0, tzinfo=ZoneInfo("UTC"))
        self.assertEqual(manila_label(utc.timestamp()), "2026-08-20 14:00 PHT")

    def test_peak_bar_named_manila(self) -> None:
        bars = fixture_repeat()
        peak = bars[PEAK1_I]
        self.assertEqual(manila_label(peak["ts"]), "2026-08-20 14:00 PHT")
        d = decide_from_bars(bars, symbol="FOO_USDT", market="futures")
        self.assertEqual(d["tz"], TZ_NAME)
        self.assertEqual(d["action"], "line")
        high_label = (d["initial_drop"]["high_bar"] or {}).get("label")
        self.assertEqual(high_label, "2026-08-20 14:00 PHT")
        self.assertIn("PHT", d["initial_drop"]["text"])
        self.assertIn("14:00", d["initial_drop"]["text"])
        low_label = (d["initial_drop"]["low_bar"] or {}).get("label")
        self.assertTrue(low_label.endswith("PHT"))
        self.assertIn("2026-08-20", low_label)


class TestSkipOnNoRepeat(unittest.TestCase):
    def test_empty_tape_skips(self) -> None:
        d = decide_from_bars([], symbol="HUNT", market="spot")
        self.assertEqual(d["action"], "skip")
        self.assertEqual(d["reason"], "no_tape")
        self.assertIsNone(d["live_copy"])
        self.assertIsNone(d["initial_drop"])
        self.assertFalse(d["live_orders"])

    def test_grind_skips(self) -> None:
        d = decide_from_bars(
            fixture_no_repeat_grind(), symbol="GRIND", market="futures"
        )
        self.assertEqual(d["action"], "skip")
        self.assertEqual(d["reason"], "no_repeat")
        self.assertIsNone(d["live_copy"])
        self.assertEqual(d["cycles"], 0)

    def test_single_bounce_skips(self) -> None:
        d = decide_from_bars(
            fixture_one_bounce_only(), symbol="ONCE", market="spot"
        )
        self.assertEqual(d["action"], "skip")
        self.assertEqual(d["reason"], "no_repeat")
        self.assertIsNone(d["live_copy"])
        self.assertLess(d["cycles"], 2)

    def test_hunt_name_without_walk_skips(self) -> None:
        d = decide_symbol(
            "NEWCOIN",
            "futures",
            fetch_bars=lambda *a, **k: [],
        )
        self.assertEqual(d["action"], "skip")
        self.assertEqual(d["reason"], "no_tape")
        self.assertIsNone(d["live_copy"])


class TestOneCopyFormat(unittest.TestCase):
    def test_one_live_copy_on_current_pump(self) -> None:
        bars = fixture_repeat()
        d = decide_from_bars(bars, symbol="FOO_USDT", market="futures", tf="15m")
        self.assertEqual(d["action"], "line")
        self.assertEqual(d["reason"], "walked")
        drop = d["initial_drop"]
        self.assertAlmostEqual(drop["high"], 1.20, places=6)
        self.assertAlmostEqual(drop["low"], 1.00, places=6)
        copy = d["live_copy"]
        self.assertIsInstance(copy, dict)
        self.assertNotIn("live_copies", d)
        self.assertAlmostEqual(copy["top"], 1.25, places=6)
        self.assertAlmostEqual(copy["bottom"], 1.05, places=6)
        self.assertEqual(copy["text"], "top 1.25 → bottom 1.05")
        self.assertEqual(copy["text"], live_copy_text(copy["top"], copy["bottom"]))
        self.assertEqual(d["tag"], "wait")
        self.assertGreaterEqual(d["path_habit"]["n"], 2)
        self.assertIn(d["path_habit"]["vol"], ("expand", "flat", "dry", "unknown"))
        self.assertIsInstance(d["live_streak"]["reds"], int)
        self.assertIn(d["live_streak"]["vs_habit"], ("short", "at", "long", "unknown"))

    def test_tagged_and_through(self) -> None:
        bars = fixture_repeat()
        last_i = len(bars)
        # Tag the 1.05 copy (low touches, close holds)
        bars.append(_bar(last_i, 1.10, 1.10, 1.05, 1.07, 180))
        d = decide_from_bars(bars, symbol="FOO_USDT", market="futures")
        self.assertEqual(d["live_copy"]["text"], "top 1.25 → bottom 1.05")
        self.assertEqual(d["tag"], "tagged")
        bars.append(_bar(last_i + 1, 1.07, 1.07, 1.00, 1.01, 200))
        d2 = decide_from_bars(bars, symbol="FOO_USDT", market="futures")
        self.assertEqual(d2["tag"], "through")
        self.assertEqual(d2["live_copy"]["text"], "top 1.25 → bottom 1.05")


class TestBookAndInject(unittest.TestCase):
    def test_book_walk_uses_injected_tape(self) -> None:
        tape = {"FOO": fixture_repeat(), "GRIND": fixture_no_repeat_grind()}

        def fetch(market, symbol, tf, limit):
            return list(tape.get(symbol, []))

        out = decide_book(
            [{"symbol": "FOO", "market": "futures"}, {"symbol": "GRIND", "market": "spot"}],
            tf="15m",
            fetch_bars=fetch,
            walk=True,
        )
        self.assertTrue(out["ok"])
        self.assertFalse(out["live_orders"])
        self.assertEqual(out["n"], 2)
        by = {r["symbol"]: r for r in out["decides"]}
        self.assertEqual(by["FOO"]["action"], "line")
        self.assertEqual(by["FOO"]["live_copy"]["text"], "top 1.25 → bottom 1.05")
        self.assertEqual(by["GRIND"]["action"], "skip")
        self.assertIsNone(by["GRIND"]["live_copy"])

    def test_book_names_only_does_not_invent(self) -> None:
        out = decide_book(
            [{"symbol": "HUNT", "market": "futures"}],
            walk=False,
        )
        self.assertEqual(out["decides"], [])
        self.assertEqual(out["names"][0]["symbol"], "HUNT")

    def test_decide_symbol_does_not_call_network_when_bars_given(self) -> None:
        with patch("mexc_bot.learning.student_decide._default_fetch") as fetch:
            d = decide_symbol(
                "FOO_USDT",
                "futures",
                bars=fixture_repeat(),
            )
            fetch.assert_not_called()
        self.assertEqual(d["live_copy"]["text"], "top 1.25 → bottom 1.05")


class TestPaperFillOnTag(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = EventStore(Path(self.tmp.name) / "p.db")
        self.book = StudentPaperBook(self.store)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_tag_and_path_ok_writes_paper_row_else_not(self) -> None:
        """Contract: tag + this-chart path ok → paper row; no repeat / waiting → none."""
        ok = decide_from_bars(
            fixture_tagged_habit(), symbol="FOO_USDT", market="futures"
        )
        self.assertEqual(ok["tag"], "tagged")
        self.assertEqual(ok["live_streak"]["vs_habit"], "at")
        self.assertTrue(should_paper_fill(ok))
        row = self.book.open_on_tag(3, ok)
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "open")
        self.assertFalse(row["live_order"])
        self.assertEqual(row["copy_text"], "top 1.25 → bottom 1.05")

        waiting = decide_from_bars(
            fixture_repeat(), symbol="FOO_USDT", market="futures"
        )
        self.assertEqual(waiting["tag"], "wait")
        self.assertFalse(should_paper_fill(waiting))
        self.assertIsNone(self.book.open_on_tag(3, waiting))

        no_repeat = decide_from_bars(
            fixture_no_repeat_grind(), symbol="GRIND", market="spot"
        )
        self.assertEqual(no_repeat["reason"], "no_repeat")
        self.assertFalse(should_paper_fill(no_repeat))
        self.assertIsNone(self.book.open_on_tag(3, no_repeat))
        self.assertEqual(len(self.book.list_open(3)), 1)

    def test_wait_and_skip_do_not_fill(self) -> None:
        wait = decide_from_bars(
            fixture_repeat(), symbol="FOO", market="futures"
        )
        self.assertEqual(wait["tag"], "wait")
        self.assertFalse(should_paper_fill(wait))
        skip = decide_from_bars(
            fixture_no_repeat_grind(), symbol="GRIND", market="spot"
        )
        self.assertFalse(should_paper_fill(skip))
        self.assertIsNone(self.book.open_on_tag(1, wait))
        self.assertIsNone(self.book.open_on_tag(1, skip))

    def test_tag_without_this_chart_habit_does_not_fill(self) -> None:
        d = decide_from_bars(
            fixture_tagged_short_of_habit(), symbol="FOO", market="futures"
        )
        self.assertEqual(d["action"], "line")
        self.assertEqual(d["tag"], "tagged")
        self.assertEqual(d["live_streak"]["vs_habit"], "short")
        self.assertFalse(should_paper_fill(d))
        # Not a global 3–5 rule: 2 reds can still be a tag, but habit here is 4
        self.assertLess(d["live_streak"]["reds"], 3)

    def test_tag_plus_habit_opens_one_paper_row(self) -> None:
        d = decide_from_bars(
            fixture_tagged_habit(), symbol="FOO_USDT", market="futures"
        )
        self.assertEqual(d["tag"], "tagged")
        self.assertEqual(d["live_streak"]["vs_habit"], "at")
        self.assertTrue(should_paper_fill(d))
        row = self.book.open_on_tag(7, d)
        self.assertIsNotNone(row)
        self.assertEqual(row["symbol"], "FOO_USDT")
        self.assertEqual(row["copy_text"], "top 1.25 → bottom 1.05")
        self.assertFalse(row["live_order"])
        self.assertEqual(row["status"], "open")
        again = self.book.open_on_tag(7, d)
        self.assertIsNone(again)
        self.assertEqual(len(self.book.list_open(7)), 1)

    def test_notice_says_paper_and_recut(self) -> None:
        d = decide_from_bars(
            fixture_tagged_habit(), symbol="FOO_USDT", market="futures"
        )
        row = self.book.open_on_tag(7, d)
        text = entry_notice_text(row)
        self.assertIn("Student entered (paper)", text)
        self.assertIn("No live order", text)
        self.assertIn("Recut in the morning", text)
        self.assertIn("top 1.25 → bottom 1.05", text)
        self.assertNotIn("sniper", text.lower())

    def test_watch_once_fills_and_notifies(self) -> None:
        notes = []

        def fetch(market, symbol, tf, limit):
            if symbol == "FOO":
                return fixture_tagged_habit()
            return fixture_no_repeat_grind()

        def notify(uid, text, parse_mode=None):
            notes.append((uid, text, parse_mode))

        out = watch_once(
            self.book,
            9,
            names=[
                {"symbol": "FOO", "market": "futures"},
                {"symbol": "GRIND", "market": "spot"},
            ],
            fetch_bars=fetch,
            notifier=notify,
        )
        self.assertEqual(out["n_filled"], 1)
        self.assertFalse(out["live_orders"])
        self.assertEqual(notes[0][0], 9)
        self.assertIsNone(notes[0][2])
        self.assertIn("Student entered (paper)", notes[0][1])
        recut = self.book.recut(9, out["filled"][0]["id"])
        self.assertEqual(recut["status"], "recut")
        self.assertEqual(self.book.list_open(9), [])


if __name__ == "__main__":
    unittest.main()
