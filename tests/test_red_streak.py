#!/usr/bin/env python3
"""Rule 2.5: consecutive closed reds per TF."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mexc_bot.learning.red_streak import (
    consecutive_red_streak,
    entry_window,
    first_or_second_red,
    is_red_bar,
    streak_label,
    streak_pack,
)


def bar(o: float, c: float) -> dict:
    return {"o": o, "h": max(o, c), "l": min(o, c), "c": c, "v": 1}


class TestRedStreak(unittest.TestCase):
    def test_is_red_strict(self) -> None:
        self.assertTrue(is_red_bar(bar(10, 9)))
        self.assertFalse(is_red_bar(bar(10, 10)))  # doji breaks
        self.assertFalse(is_red_bar(bar(9, 10)))

    def test_count_newest_backward(self) -> None:
        # green, red, red, red → 3
        bars = [bar(8, 9), bar(9, 8), bar(8, 7), bar(7, 6)]
        self.assertEqual(consecutive_red_streak(bars), 3)
        self.assertEqual(streak_label(3), "3rd")
        self.assertTrue(entry_window(3))
        self.assertFalse(first_or_second_red(3))

    def test_first_red_not_entry(self) -> None:
        bars = [bar(8, 9), bar(9, 8)]
        self.assertEqual(consecutive_red_streak(bars), 1)
        pack = streak_pack(bars)
        self.assertEqual(pack["red_label"], "1st")
        self.assertTrue(pack["first_red"])
        self.assertFalse(pack["entry_red_window"])

    def test_doji_breaks(self) -> None:
        bars = [bar(10, 9), bar(9, 9), bar(9, 8)]
        self.assertEqual(consecutive_red_streak(bars), 1)

    def test_skip_forming(self) -> None:
        bars = [bar(10, 9), bar(9, 8), bar(8, 7)]
        # last is forming red — if include_forming skip it → 2
        self.assertEqual(consecutive_red_streak(bars, include_forming=True), 2)

    def test_six_plus(self) -> None:
        bars = [bar(10 - i, 9 - i) for i in range(7)]
        n = consecutive_red_streak(bars)
        self.assertGreaterEqual(n, 6)
        self.assertEqual(streak_label(n), "6plus")
        self.assertFalse(entry_window(n))


if __name__ == "__main__":
    unittest.main()
