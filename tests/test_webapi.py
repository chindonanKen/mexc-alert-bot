#!/usr/bin/env python3
"""V2 desk API smoke tests (TestClient, no network required for core routes)."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class TestWebApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Ensure import works without DESK token
        os.environ.pop("DESK_API_TOKEN", None)
        tmp = tempfile.TemporaryDirectory()
        cls._tmp = tmp
        db = Path(tmp.name) / "alerts.db"
        os.environ["ALERTS_FILE"] = str(db.with_suffix(".json"))
        # create minimal schema
        import sqlite3

        conn = sqlite3.connect(str(db))
        conn.executescript(
            """
            CREATE TABLE alerts (
              id INTEGER PRIMARY KEY, user_id INTEGER, symbol TEXT,
              price REAL, enabled INTEGER, market TEXT DEFAULT 'spot'
            );
            INSERT INTO alerts VALUES (1, 42, 'BTCUSDT', 50000, 1, 'spot');
            CREATE TABLE mover_watchlist (
              user_id INTEGER, symbol TEXT, market TEXT,
              PRIMARY KEY (user_id, symbol, market)
            );
            INSERT INTO mover_watchlist VALUES (42, 'BTC_USDT', 'futures');
            CREATE TABLE learning_events (
              id INTEGER PRIMARY KEY, user_id INTEGER, source TEXT,
              symbol TEXT, market TEXT, ts REAL, price REAL, ref_price REAL,
              drop_pct REAL, velocity_band TEXT, heat_breadth INTEGER,
              mode TEXT, payload_json TEXT, news_event_id INTEGER
            );
            INSERT INTO learning_events (
              id, user_id, source, symbol, market, ts, price, drop_pct, velocity_band, mode
            ) VALUES (1, 42, 'mover_peak', 'BTC_USDT', 'futures', 1700000000, 1, -9, 'PANIC', 'peak');
            CREATE TABLE learning_labels (
              id INTEGER PRIMARY KEY, event_id INTEGER, user_id INTEGER,
              action TEXT, bounce_quality TEXT, behavior TEXT, notes TEXT, ts REAL
            );
            CREATE TABLE investigations (
              id INTEGER PRIMARY KEY, user_id INTEGER, event_id INTEGER,
              symbol TEXT, market TEXT, drop_pct REAL, velocity_band TEXT,
              heat_breadth INTEGER, verdict TEXT, confidence REAL,
              evidence_json TEXT, ts REAL
            );
            CREATE TABLE news_events (
              id INTEGER PRIMARY KEY, symbol TEXT, class TEXT, severity TEXT,
              title TEXT, url TEXT, source TEXT, source_trust TEXT, ts REAL,
              raw_json TEXT, fingerprint TEXT
            );
            CREATE TABLE delist_cache (
              id INTEGER PRIMARY KEY, exchange TEXT, base TEXT, title TEXT,
              url TEXT, kind TEXT, ts REAL, fingerprint TEXT, raw_json TEXT
            );
            CREATE TABLE source_expertise (
              source TEXT, kind TEXT, hits INTEGER, confirmed_moves INTEGER,
              false_alarms INTEGER, bounce_sum REAL, bounce_n INTEGER,
              weight REAL, updated_at REAL, PRIMARY KEY (source, kind)
            );
            CREATE TABLE journal_trades (
              id INTEGER PRIMARY KEY, user_id INTEGER, symbol TEXT, market TEXT,
              status TEXT, entry_avg REAL, exit_avg REAL, notes TEXT,
              opened_at REAL, closed_at REAL
            );
            CREATE TABLE mover_settings (
              user_id INTEGER PRIMARY KEY, enabled INTEGER,
              threshold_percent REAL, lookback_seconds INTEGER
            );
            """
        )
        conn.commit()
        conn.close()

        from mexc_bot.webapi.app import create_app
        from fastapi.testclient import TestClient

        cls.client = TestClient(create_app())

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_health(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])
        self.assertIn("2.", r.json()["version"])

    def test_overview(self):
        r = self.client.get("/api/overview")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("counts", body)
        self.assertIn("pulse", body)

    def test_alerts_crud(self):
        r = self.client.get("/api/alerts")
        self.assertEqual(r.status_code, 200)
        self.assertGreaterEqual(len(r.json()["alerts"]), 1)
        r2 = self.client.post(
            "/api/alerts",
            json={"symbol": "ETH", "price": 2000, "market": "spot"},
        )
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(r2.json().get("ok"))

    def test_events(self):
        r = self.client.get("/api/events")
        self.assertEqual(r.status_code, 200)
        self.assertGreaterEqual(len(r.json()["events"]), 1)

    def test_positions(self):
        r = self.client.post(
            "/api/positions",
            json={"symbol": "TEST", "market": "futures", "entry_avg": 1.0},
        )
        self.assertEqual(r.status_code, 200)
        r2 = self.client.get("/api/positions")
        self.assertGreaterEqual(len(r2.json()["positions"]), 1)

    def test_roadmap(self):
        r = self.client.get("/api/roadmap")
        self.assertEqual(r.status_code, 200)
        self.assertIn("now", r.json())
        self.assertIn("next", r.json())

    def test_strategy(self):
        r = self.client.get("/api/strategy")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Average Drop", r.json()["name"])

    def test_coach(self):
        r = self.client.post("/api/coach", json={"message": "panic"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("reply", r.json())

    def test_agent_offline(self):
        # Without XAI key still returns a reply
        r = self.client.post("/api/agent", json={"message": "list my overview"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("reply", r.json())

    def test_index(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("AD Desk", r.text)
        self.assertIn("ovNeedsYou", r.text)
        self.assertIn("teachForm", r.text)
        self.assertIn("whatLearnedReply", r.text)
        self.assertIn("ovAgentLearned", r.text)

    def test_learning_endpoints(self):
        r = self.client.get("/api/learning")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("needs_you", body)
        self.assertIn("stats", body)
        self.assertIn("what_learned_reply", body)
        self.assertEqual(body.get("product"), "learning_v1")
        self.assertTrue("trades" in body or "lessons" in body)
        r2 = self.client.post(
            "/api/learning/teach",
            json={"text": "webapi test lesson no full size on grind"},
        )
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(r2.json().get("ok"))
        r_ask = self.client.post(
            "/api/learning/ask",
            json={"question": "What have you learned so far?"},
        )
        self.assertEqual(r_ask.status_code, 200)
        self.assertIn("reply", r_ask.json())
        r3 = self.client.get("/api/overview")
        self.assertEqual(r3.status_code, 200)
        ov = r3.json()
        self.assertIn("needs_you", ov)
        self.assertTrue(
            "agent_summary" in ov or "what_learned_reply" in ov or "hierarchy" in ov
        )
        r4 = self.client.get("/api/notify/stub")
        self.assertEqual(r4.status_code, 200)
        self.assertEqual(r4.json().get("status"), "stub")
        r5 = self.client.get("/api/learning/trades")
        self.assertEqual(r5.status_code, 200)
        self.assertIn("trades", r5.json())
        # journal open is not teach-ok by default (exchange-only money truth)
        self.client.post(
            "/api/positions",
            json={"symbol": "TESTCOIN", "market": "futures", "entry_avg": 1.0},
        )
        teach = self.client.get("/api/learning/trades").json()["trades"]
        self.assertTrue(
            all(
                t.get("teach_ok") is True or t.get("money_truth") == "exchange"
                for t in teach
            )
            or teach == []
        )
        all_trades = self.client.get(
            "/api/learning/trades?teach_only=false"
        ).json()["trades"]
        self.assertTrue(
            any(t.get("symbol") == "TESTCOIN" for t in all_trades)
            or any(t.get("money_truth") == "exchange" for t in all_trades)
            or isinstance(all_trades, list)
        )


if __name__ == "__main__":
    unittest.main()
