#!/usr/bin/env python3
"""Fold lock: PASS the trunk; FAIL if a mixed process book is folded in.

Honor this fail: do not treat workspace AD-desk-rules.md (or any file that
mixes tickers / dollars / live plays) as the rules book.
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PROCESS = ROOT / "docs" / "AD_PROCESS.md"
# Tickers / live plays that must stay out of the process book.
_BANNED_IN_PROCESS = (
    "PRL",
    "GUA",
    "ANSEM",
    "AXTI",
    "MRNA",
    "PUMP",
    "TSLA",
)
_DOLLAR_AMT = re.compile(r"\$\s*\d")


class TestFoldLock(unittest.TestCase):
    def test_no_mixed_desk_rules_file(self):
        """FAIL if a mixed AD-desk-rules book is in the repo."""
        hits = []
        for p in ROOT.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(ROOT).as_posix()
            if "/.git/" in f"/{rel}/" or rel.startswith(".git/"):
                continue
            name = p.name.lower()
            if "ad-desk-rules" in name or name in {
                "ad-desk-rules.md",
                "ad_desk_rules.md",
            }:
                hits.append(rel)
        self.assertEqual(
            hits,
            [],
            "do not fold workspace AD-desk-rules.md (tickers/dollars/plays) "
            f"as the process book: {hits}",
        )

    def test_process_book_is_ticker_free(self):
        self.assertTrue(PROCESS.is_file(), "docs/AD_PROCESS.md is the process book")
        text = PROCESS.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# AD trading rules\n"))
        self.assertIn("This book is process only", text)
        self.assertIn("## Chart", text)
        self.assertIn("## Path", text)
        self.assertIn("## Size", text)
        self.assertIn("## Fail", text)
        self.assertIn("## Exit", text)
        self.assertNotIn("AD_AGENT_PLAN", text)
        self.assertFalse(text.lstrip().startswith("---"))
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("docs/AD_PROCESS.md", agents)
        self.assertIn("Process book (ticker-free)", agents)
        for tok in _BANNED_IN_PROCESS:
            self.assertNotIn(tok, text)
        self.assertNotIn("$", text)
        self.assertIsNone(_DOLLAR_AMT.search(text))
        # Kenneth locked these old lines dead. Do not merge them back.
        dead = (
            "Last at or under AD bottom is spent",
            "lean / one layer",
            "Default 5–10 layers",
            "Sit-out path: grind, isolated dump, dry volume",
            "No volume big moves",
            "exit to preserve capital",
        )
        for line in dead:
            self.assertNotIn(line, text)
        self.assertIn("A timer is not a fail", text)
        self.assertIn("No volume = size down, not skip", text)
        self.assertIn("Breaking the AD is usually add, not flatten", text)
        self.assertIn("Sideways too long is not a clock", text)

    def test_telegram_position_ping_off(self):
        from mexc_bot.learning.fill_lifecycle import (
            NOTIFY_POSITION_OPENED,
            fill_starts_position,
            maybe_notify_position_opened,
            position_opened_message,
        )

        self.assertFalse(NOTIFY_POSITION_OPENED)
        self.assertFalse(
            fill_starts_position({"symbol": "X", "side": "buy", "market": "futures"})
        )
        self.assertIsNone(position_opened_message({"side": "buy"}))
        self.assertFalse(maybe_notify_position_opened({"side": "buy"}))
        main = (ROOT / "mexc_bot" / "main.py").read_text(encoding="utf-8")
        fills = (ROOT / "mexc_bot" / "learning" / "fills.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("write_auto_journal=False", main)
        self.assertIn("DESK_ALLOW_LIVE_ORDERS=false", (ROOT / ".env.example").read_text())
        # Live-bind desk path, not the git alert-bot fill sync.
        self.assertNotIn("maybe_notify_position_opened", fills)
        self.assertNotIn("from .fill_lifecycle import", fills)

    def test_no_data_or_env_in_tree_root(self):
        forbidden = [
            ROOT / ".env",
            ROOT / "DESK_OPEN.txt",
            ROOT / "data" / "alerts.db",
        ]
        present = [str(p.relative_to(ROOT)) for p in forbidden if p.exists()]
        self.assertEqual(present, [])


if __name__ == "__main__":
    unittest.main()
