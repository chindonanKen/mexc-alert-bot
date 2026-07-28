#!/usr/bin/env python3
"""Assistant UX: callback parse + plain-language intents (no Telegram network)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mexc_bot.assistant.ux import (
    desk_text,
    parse_callback,
    parse_plain_intent,
)


class TestAssistantUx(unittest.TestCase):
    def test_parse_callback_actions(self):
        self.assertEqual(parse_callback("L:t:42"), ("took", 42))
        self.assertEqual(parse_callback("L:s:7"), ("skip", 7))
        self.assertEqual(parse_callback("L:w:1"), ("watch", 1))
        self.assertEqual(parse_callback("L:bs:9"), ("bounce_strong", 9))
        self.assertEqual(parse_callback("L:bf:3"), ("bounce_failed", 3))
        self.assertIsNone(parse_callback("nope"))
        self.assertIsNone(parse_callback("L:x:1"))

    def test_plain_intents(self):
        self.assertEqual(parse_plain_intent("took")["intent"], "took")
        self.assertEqual(parse_plain_intent("SKIP that")["intent"], "skip")
        self.assertEqual(parse_plain_intent("later")["intent"], "watch")
        self.assertEqual(parse_plain_intent("brief")["intent"], "brief")
        self.assertEqual(parse_plain_intent("coach panic")["intent"], "coach")
        self.assertEqual(parse_plain_intent("pride")["intent"], "pride")
        self.assertIsNone(parse_plain_intent("/j took"))
        self.assertIsNone(parse_plain_intent("hello how are you today this is long"))

    def test_desk_mentions_buttons(self):
        t = desk_text(learning_on=True, recent_n=3, open_trades_n=0)
        self.assertIn("Took", t)
        self.assertIn("Skip", t)
        self.assertIn("desk", t.lower())


if __name__ == "__main__":
    unittest.main()
