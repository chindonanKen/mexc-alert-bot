#!/usr/bin/env python3
"""Slice 7: book-only news + devastating-only alarm (no Telegram send)."""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mexc_bot.news.classify import (
    TRUST_AGGREGATE,
    TRUST_OFFICIAL,
    TRUST_REKT,
    evaluate_headline,
    should_alarm,
)
from mexc_bot.webapi.bad_intel import load_bad_intel_feed
from mexc_bot.webapi.news_book import filter_rows_to_book

JS = (ROOT / "mexc_bot/webapi/static/assets/desk.js").read_text()
HTML = (ROOT / "mexc_bot/webapi/static/index.html").read_text()
APP = (ROOT / "mexc_bot/webapi/app.py").read_text()
WATCH = (ROOT / "mexc_bot/news/watcher.py").read_text()


class TestRumorNoAlarm(unittest.TestCase):
    def test_rumor_headline_does_not_alarm(self):
        ev = evaluate_headline(
            "Rumor: SYN will be delisted next week, allegedly",
            source_trust=TRUST_AGGREGATE,
            symbol="SYNUSDT",
            book_bases={"SYN"},
        )
        self.assertFalse(ev["alarm"])
        self.assertFalse(ev["show"])
        self.assertIsNone(ev["cls"])

    def test_spam_generic_headline_does_not_alarm(self):
        ev = evaluate_headline(
            "Analyst says market might crash — price prediction",
            source_trust=TRUST_AGGREGATE,
            symbol="BTCUSDT",
            book_bases={"BTC"},
        )
        self.assertFalse(ev["alarm"])
        self.assertFalse(should_alarm("fatal", TRUST_OFFICIAL, on_book=True, cls=None, title="wow"))


class TestWatchedDevastatingAlarms(unittest.TestCase):
    def test_official_delist_on_book_alarms(self):
        ev = evaluate_headline(
            "MEXC Will Delist SYNUSDT Trading Pair",
            source_trust=TRUST_OFFICIAL,
            symbol="SYNUSDT",
            book_bases={"SYN"},
        )
        self.assertTrue(ev["on_book"])
        self.assertTrue(ev["show"])
        self.assertTrue(ev["alarm"])
        self.assertEqual(ev["cls"], "DELIST")

    def test_rekt_exploit_on_book_alarms(self):
        ev = evaluate_headline(
            "Protocol SYN exploited — funds drained",
            source_trust=TRUST_REKT,
            symbol="SYN",
            book_bases={"SYN"},
        )
        self.assertTrue(ev["alarm"])
        self.assertEqual(ev["cls"], "HACK")

    def test_halt_on_book_alarms_when_official(self):
        ev = evaluate_headline(
            "SYN trading halt announced by the exchange",
            source_trust=TRUST_OFFICIAL,
            symbol="SYNUSDT",
            book_bases={"SYN"},
        )
        self.assertEqual(ev["cls"], "HALT")
        self.assertTrue(ev["alarm"])


class TestUnwatchedNotShown(unittest.TestCase):
    def test_devastating_unwatched_is_hidden_and_silent(self):
        ev = evaluate_headline(
            "MEXC Will Delist XYZUSDT Trading Pair",
            source_trust=TRUST_OFFICIAL,
            symbol="XYZUSDT",
            book_bases={"SYN", "BTC"},
        )
        self.assertFalse(ev["on_book"])
        self.assertFalse(ev["show"])
        self.assertFalse(ev["alarm"])

    def test_feed_drops_unwatched_name(self):
        now = time.time()

        def fetch_all(sql, params=None):
            if "news_events" in sql:
                return [
                    {
                        "id": 1,
                        "symbol": "SYN",
                        "class": "DELIST",
                        "severity": "fatal",
                        "title": "Will delist SYNUSDT",
                        "url": "https://ex/s",
                        "source": "mexc",
                        "ts": now,
                    },
                    {
                        "id": 2,
                        "symbol": "XYZ",
                        "class": "HACK",
                        "severity": "fatal",
                        "title": "XYZ exploited and funds drained",
                        "url": "https://ex/x",
                        "source": "rekt",
                        "ts": now,
                    },
                ]
            return []

        rows = load_bad_intel_feed(fetch_all, limit=10, book_bases={"SYN"}, now=now)
        syms = " ".join((r.get("symbol") or "") + " " + (r.get("title") or "") for r in rows)
        self.assertTrue(any("SYN" in (r.get("title") or r.get("symbol") or "") for r in rows))
        self.assertFalse(any("XYZ" in (r.get("title") or "") for r in rows))
        self.assertNotIn("XYZ exploited", syms)

    def test_filter_rows_to_book_hides_stranger(self):
        kept = filter_rows_to_book(
            [
                {"symbol": "SYNUSDT", "title": "Will delist SYN", "bases": ["SYN"]},
                {"symbol": "NOPEUSDT", "title": "Will delist NOPE", "bases": ["NOPE"]},
            ],
            book_bases={"SYN"},
        )
        self.assertEqual(len(kept), 1)
        self.assertIn("SYN", (kept[0].get("title") or "") + (kept[0].get("symbol") or ""))


class TestDeskAndRoutes(unittest.TestCase):
    def test_cache_bust_slicelab7(self):
        self.assertIn("desk.js?v=slicelab7b", HTML)
        self.assertIn("desk.css?v=slicelab7b", HTML)

    def test_desk_alarms_news_and_no_query_token(self):
        self.assertIn("news_alarms", APP)
        self.assertIn("news_since_id", APP)
        self.assertIn("ingestNewsAlarms", JS)
        self.assertIn("news_devastating", JS)
        self.assertIn("function playAlarmSound", JS)
        self.assertNotIn("decision.get(\"alarm\") and trust != \"official\"", WATCH)
        self.assertIn("decision.get(\"alarm\")", WATCH)
        chunk = APP[APP.find('@app.get("/api/news")') : APP.find('@app.get("/api/prices")')]
        self.assertNotIn("token: Optional[str] = Query", chunk)
        self.assertIn("book_only", chunk)

    def test_slices_1_to_6_still_present(self):
        self.assertIn("function applySelectedSymbol", JS)
        self.assertIn("async function jumpToLesson", JS)
        self.assertIn("Teach-this-fire", JS)
        self.assertIn("function loadHunt", JS)
        self.assertIn("function playAlarmSound", JS)


class TestWatcherNoRealTelegram(unittest.TestCase):
    """NewsWatcher must not call a live notifier for rumor / off-book."""

    def setUp(self):
        from mexc_bot.news.store import NewsStore
        from mexc_bot.news.watcher import NewsWatcher

        self.tmp = tempfile.TemporaryDirectory()
        self.store = NewsStore(Path(self.tmp.name) / "n.db")
        self.sent = []
        self.NewsWatcher = NewsWatcher

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, items, book):
        w = self.NewsWatcher(
            self.store,
            notifier=lambda uid, msg, **kw: self.sent.append((uid, msg)),
            get_watch_bases=lambda: set(book),
            get_notify_user_ids=lambda: [8630949601],
        )
        with patch("mexc_bot.news.watcher.fetch_rekt_rss", return_value=items), patch(
            "mexc_bot.news.watcher.fetch_mexc_announcements", return_value=[]
        ):
            w._check_once()
        return w

    def test_rumor_does_not_notify(self):
        self._run(
            [
                {
                    "title": "Rumor: SYN will be delisted next week, allegedly",
                    "body": "",
                    "source": "twitter",
                    "source_trust": TRUST_AGGREGATE,
                    "symbol": "SYNUSDT",
                    "bases": ["SYN"],
                    "url": "http://x/rumor",
                    "ts": time.time(),
                }
            ],
            {"SYN"},
        )
        self.assertEqual(self.sent, [])

    def test_watched_devastating_notifies_mock_only(self):
        self._run(
            [
                {
                    "title": "MEXC Will Delist SYNUSDT Trading Pair",
                    "body": "",
                    "source": "mexc",
                    "source_trust": TRUST_OFFICIAL,
                    "symbol": "SYNUSDT",
                    "bases": ["SYN"],
                    "url": "http://x/delist",
                    "ts": time.time(),
                }
            ],
            {"SYN"},
        )
        self.assertEqual(len(self.sent), 1)
        self.assertIn("DEVASTATING NEWS", self.sent[0][1])
        self.assertIn("SYN", self.sent[0][1])

    def test_unwatched_devastating_does_not_notify(self):
        self._run(
            [
                {
                    "title": "MEXC Will Delist XYZUSDT Trading Pair",
                    "body": "",
                    "source": "mexc",
                    "source_trust": TRUST_OFFICIAL,
                    "symbol": "XYZUSDT",
                    "bases": ["XYZ"],
                    "url": "http://x/xyz",
                    "ts": time.time(),
                }
            ],
            {"SYN", "BTC"},
        )
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
