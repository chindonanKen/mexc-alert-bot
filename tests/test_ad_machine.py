#!/usr/bin/env python3
"""Week-1 AD Machine: isolated book, flag-off 404, gates, caps, seeds."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class TestMachineLogic(unittest.TestCase):
    def test_first_candle_sitout_isolated(self):
        from mexc_bot.machine.logic import first_candle_sitout

        self.assertTrue(first_candle_sitout(1, heat_breadth=1, panic_board=False))
        self.assertTrue(first_candle_sitout(2, heat_breadth=1, panic_board=False))
        self.assertFalse(first_candle_sitout(3, heat_breadth=1, panic_board=False))
        self.assertFalse(first_candle_sitout(1, heat_breadth=3, panic_board=False))
        self.assertFalse(first_candle_sitout(2, heat_breadth=3, panic_board=False))
        self.assertFalse(first_candle_sitout(1, heat_breadth=1, panic_board=True))
        self.assertFalse(first_candle_sitout(2, heat_breadth=1, panic_board=True))

    def test_news_kill_not_rumor(self):
        from mexc_bot.machine.logic import news_kill

        self.assertIsNone(
            news_kill(
                [{"class": "DELIST", "title": "rumor of delist", "severity": "fatal"}]
            )
        )
        hit = news_kill(
            [{"class": "SCAM", "title": "Confirmed scam", "severity": "fatal"}]
        )
        self.assertIsNotNone(hit)
        self.assertEqual(hit["class"], "SCAM")

    def test_3plus_default_until_habit(self):
        from mexc_bot.machine.logic import reds_required, tf_meets_rules

        self.assertEqual(reds_required(None), 3)
        self.assertEqual(reds_required(5), 5)
        two = tf_meets_rules(tf="15m", reds=2, ad_known=True)
        self.assertFalse(two["complete"])
        self.assertFalse(two["reds_ok"])
        self.assertTrue(two["first_candle_sitout"])
        three = tf_meets_rules(tf="15m", reds=3, ad_known=True)
        self.assertTrue(three["complete"])
        self.assertFalse(three["first_candle_sitout"])

    def test_one_tf_complete_vs_higher_first_candle(self):
        from mexc_bot.machine.logic import pick_working_tf, tf_meets_rules

        states = [
            tf_meets_rules(tf="1h", reds=2, ad_known=True, heat_breadth=1),
            tf_meets_rules(tf="15m", reds=4, ad_known=True, heat_breadth=1),
        ]
        self.assertTrue(states[0]["first_candle_sitout"])
        self.assertFalse(states[0]["complete"])
        self.assertTrue(states[1]["complete"])
        pick = pick_working_tf(states)
        self.assertEqual(pick["tf"], "15m")
        self.assertEqual(pick["pick_reason"], "one_tf_complete")

    def test_two_tf_tie_picks_slower_never_average(self):
        from mexc_bot.machine.logic import pick_working_tf, tf_meets_rules

        states = [
            tf_meets_rules(tf="15m", reds=4, ad_known=True),
            tf_meets_rules(tf="4h", reds=4, ad_known=True),
        ]
        pick = pick_working_tf(states, respected={"15m": 1.0, "4h": 1.0})
        self.assertEqual(pick["tf"], "4h")
        self.assertEqual(pick["pick_reason"], "tie_slower")
        self.assertNotIn("average", str(pick).lower())

    def test_room_state_tones(self):
        from mexc_bot.machine.engine import room_state

        empty = room_state([], [])
        self.assertTrue(empty["empty"])
        self.assertEqual(empty["tone"], "empty")
        self.assertEqual(empty["open_slots"], 2)
        self.assertIn("Quiet book", empty["invitation"])
        self.assertIn("PHT", empty["manila"])
        watch = room_state(
            [{"live": False, "ad_status": "known", "ad_top": 1, "name": "ANSEM"}],
            [],
        )
        self.assertEqual(watch["tone"], "watch")
        self.assertEqual(watch["hung_count"], 1)
        self.assertIn("ANSEM", watch["invitation"])
        live = room_state([{"live": True}, {"live": False}], [])
        self.assertEqual(live["tone"], "live")
        self.assertEqual(live["open_slots"], 1)
        self.assertIn("play is on", live["invitation"])
        need = room_state([{"live": True}], [{"id": 1}])
        self.assertEqual(need["tone"], "needs_you")
        self.assertFalse(need["needs_you_clear"])
        self.assertIn("waiting on you", need["invitation"])

    def test_exponential_layers_sum_and_deeper_larger(self):
        from mexc_bot.machine.logic import exponential_layers

        layers = exponential_layers(10.0, 5.0, count=5, budget_usd=100)
        self.assertEqual(len(layers), 5)
        self.assertAlmostEqual(sum(x["usd"] for x in layers), 100.0, places=3)
        self.assertLess(layers[0]["usd"], layers[-1]["usd"])
        self.assertGreater(layers[0]["price"], layers[-1]["price"])
        self.assertAlmostEqual(layers[-1]["price"], 5.0, places=6)

    def test_play_cap_math(self):
        from mexc_bot.machine.logic import can_open_play

        self.assertTrue(can_open_play(0, 0)["ok"])
        self.assertTrue(can_open_play(1, 100)["ok"])
        self.assertEqual(can_open_play(1, 100)["budget_usd"], 100.0)
        third = can_open_play(2, 200)
        self.assertFalse(third["ok"])
        self.assertEqual(third["reason"], "max_2_live_plays")


class TestMachineIsolationAndApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "alerts.db"
        self._env = {
            "ALERTS_FILE": os.environ.get("ALERTS_FILE"),
            "DESK_USER_ID": os.environ.get("DESK_USER_ID"),
            "DESK_API_TOKEN": os.environ.get("DESK_API_TOKEN"),
            "WEB_UI_TOKEN": os.environ.get("WEB_UI_TOKEN"),
            "FEATURE_AD_MACHINE": os.environ.get("FEATURE_AD_MACHINE"),
        }
        os.environ["ALERTS_FILE"] = str(self.db)
        os.environ["DESK_USER_ID"] = "8630949601"
        os.environ.pop("DESK_API_TOKEN", None)
        os.environ.pop("WEB_UI_TOKEN", None)
        self.uid = 8630949601
        self._seed_foreign_rows()

    def tearDown(self):
        self.tmp.cleanup()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _seed_foreign_rows(self) -> None:
        conn = sqlite3.connect(str(self.db))
        conn.executescript(
            """
            CREATE TABLE alerts (
              id INTEGER PRIMARY KEY, user_id INTEGER, symbol TEXT,
              price REAL, enabled INTEGER, market TEXT DEFAULT 'spot'
            );
            INSERT INTO alerts VALUES (1, 8630949601, 'BTCUSDT', 50000, 1, 'spot');
            CREATE TABLE journal_trades (
              id INTEGER PRIMARY KEY, user_id INTEGER, symbol TEXT, market TEXT,
              status TEXT, entry_avg REAL, exit_avg REAL, notes TEXT,
              opened_at REAL, closed_at REAL
            );
            INSERT INTO journal_trades (
              user_id, symbol, market, status, entry_avg, notes, opened_at
            ) VALUES (8630949601, 'NESUSDT', 'spot', 'open', 0.14, 'live book', 1);
            CREATE TABLE learning_lessons (
              id INTEGER PRIMARY KEY, user_id INTEGER, text TEXT, tags_json TEXT,
              weight REAL, created_at REAL
            );
            INSERT INTO learning_lessons (user_id, text, tags_json, weight, created_at)
            VALUES (8630949601, 'do not touch', '[]', 1, 1);
            CREATE TABLE mover_watchlist (
              user_id INTEGER, symbol TEXT, market TEXT,
              PRIMARY KEY (user_id, symbol, market)
            );
            INSERT INTO mover_watchlist VALUES (8630949601, 'BTC_USDT', 'futures');
            CREATE TABLE position_flags (
              id INTEGER PRIMARY KEY, user_id INTEGER, entity_key TEXT,
              symbol TEXT, market TEXT, book TEXT
            );
            """
        )
        conn.commit()
        conn.close()

    def _counts(self) -> dict:
        conn = sqlite3.connect(str(self.db))
        out = {}
        for t in (
            "alerts",
            "journal_trades",
            "learning_lessons",
            "mover_watchlist",
            "position_flags",
        ):
            out[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        lesson = conn.execute("SELECT text FROM learning_lessons").fetchone()[0]
        entry = conn.execute("SELECT entry_avg FROM journal_trades").fetchone()[0]
        conn.close()
        out["lesson_text"] = lesson
        out["entry_avg"] = entry
        return out

    def _client(self, on: bool):
        if on:
            os.environ["FEATURE_AD_MACHINE"] = "true"
        else:
            os.environ["FEATURE_AD_MACHINE"] = "false"
        from fastapi.testclient import TestClient
        from mexc_bot.webapi.app import create_app

        return TestClient(create_app())

    def test_rejected_student_modules_absent(self):
        self.assertFalse((ROOT / "mexc_bot" / "student_decide.py").exists())
        self.assertFalse((ROOT / "mexc_bot" / "student_paper.py").exists())
        self.assertFalse((ROOT / "mexc_bot" / "learning" / "student_decide.py").exists())

    def test_flag_off_hides_page_and_404s_apis(self):
        c = self._client(False)
        h = c.get("/api/health").json()
        self.assertFalse(h.get("feature_ad_machine"))
        self.assertFalse(h.get("live_orders_allowed"))
        self.assertEqual(c.get("/api/machine/plans").status_code, 404)
        self.assertEqual(c.get("/api/machine/closes").status_code, 404)
        self.assertEqual(c.get("/api/machine/ranks").status_code, 404)
        self.assertEqual(c.post("/api/machine/plans/1/recut", json={}).status_code, 404)
        self.assertEqual(c.post("/api/machine/names", json={"symbol": "X"}).status_code, 404)
        self.assertEqual(c.get("/machine").status_code, 404)
        index = c.get("/")
        self.assertEqual(index.status_code, 200)
        self.assertIn('id="navMachine"', index.text)
        self.assertIn("hidden", index.text)
        self.assertNotIn('id="view-machine"', index.text)

    def test_flag_on_seeds_six_and_page(self):
        c = self._client(True)
        r = c.get("/api/machine/plans")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        names = {(p["symbol"], p["market"]) for p in body["plans"]}
        self.assertEqual(
            names,
            {
                ("USUSDT", "spot"),
                ("BPUSDT", "spot"),
                ("AXTISTOCK_USDT", "futures"),
                ("MRNASTOCK_USDT", "futures"),
                ("ANSEMUSDT", "spot"),
                ("PUMPUSDT", "spot"),
            },
        )
        self.assertEqual(len(body["plans"]), 6)
        ansem = next(p for p in body["plans"] if p["symbol"] == "ANSEMUSDT")
        self.assertEqual(ansem["ad_status"], "known")
        self.assertAlmostEqual(ansem["ad_top"], 0.356)
        self.assertAlmostEqual(ansem["ad_bottom"], 0.145)
        axti = next(p for p in body["plans"] if p["symbol"] == "AXTISTOCK_USDT")
        self.assertAlmostEqual(axti["ad_top"], 97.97)
        self.assertAlmostEqual(axti["ad_bottom"], 65.58)
        us = next(p for p in body["plans"] if p["symbol"] == "USUSDT")
        self.assertEqual(us["ad_status"], "unknown")
        self.assertEqual(us["ad"], "unknown")
        self.assertIn("room", body)
        self.assertEqual(body["room"]["live_count"], 0)
        self.assertEqual(body["room"]["open_slots"], 2)
        self.assertTrue(body["room"]["needs_you_clear"])
        self.assertEqual(body["room"]["hung_count"], 2)
        self.assertGreaterEqual(body["room"]["uncut_count"], 4)
        self.assertIn("PHT", body["room"]["manila"])
        self.assertTrue(body["room"]["invitation"])
        page = c.get("/machine")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Ranked", page.text)
        self.assertIn('class="room"', page.text)
        self.assertIn("liveStages", page.text)
        self.assertIn("rankList", page.text)
        self.assertIn("planSheet", page.text)
        self.assertIn("needsStack", page.text)
        self.assertIn("$200 book idle", page.text)
        self.assertIn("SLOT 1", page.text)
        self.assertIn("waiting · $200 book", page.text)
        self.assertIn("waiting · max 2 live", page.text)
        self.assertIn("skeleton", page.text)
        self.assertIn("IBM+Plex", page.text)
        self.assertNotIn("desk.css", page.text)
        self.assertNotIn("Instrument Sans", page.text)
        self.assertNotIn("Newsreader", page.text)
        self.assertNotIn("Rankings", page.text)
        self.assertNotIn("Log out", page.text)
        self.assertNotIn("ASTEROID", page.text)
        self.assertNotIn("leather", page.text.lower())
        self.assertNotIn("<table", page.text.lower())
        self.assertNotIn("<canvas", page.text.lower())
        self.assertNotIn("tradingview", page.text.lower())
        css = c.get("/assets/machine.css?v=s5")
        self.assertEqual(css.status_code, 200)
        self.assertIn("#010207", css.text)
        self.assertIn("#e8d5a3", css.text)
        self.assertIn("#f5b942", css.text)
        self.assertIn("#fb7185", css.text)
        self.assertIn("IBM Plex", css.text)
        self.assertIn("rail-breath 1.6s", css.text)
        self.assertIn("rank-slide 180ms", css.text)
        self.assertIn("80ms", css.text)
        self.assertNotIn("Instrument Sans", css.text)
        self.assertNotIn("Newsreader", css.text)
        self.assertNotIn("#22d3ee", css.text)
        self.assertNotIn("#38bdf8", css.text)
        self.assertIn("stage-col", css.text)
        self.assertIn("flex-direction: column", css.text)
        js = c.get("/assets/machine.js?v=s5")
        self.assertEqual(js.status_code, 200)
        self.assertIn("waiting · $200 book", js.text)
        self.assertIn("of 2 live", js.text)
        self.assertIn("LINE ${linePrice(p)}", js.text)
        self.assertIn("working_orders", js.text)
        self.assertIn("function restClock", js.text)
        self.assertIn('+ "m"', js.text)
        self.assertIn("function fmtVol", js.text)
        self.assertIn('"M"', js.text)
        self.assertNotIn('resting ? "rest"', js.text)
        self.assertNotIn("ASTEROID", js.text)
        self.assertNotIn("ORION", js.text)
        self.assertNotIn("ECLIPSE", js.text)

    def test_recut_kill_approve(self):
        c = self._client(True)
        plans = c.get("/api/machine/plans").json()["plans"]
        us = next(p for p in plans if p["symbol"] == "USUSDT")
        rec = c.post(
            f"/api/machine/plans/{us['id']}/recut",
            json={"ad_top": 2.0, "ad_bottom": 1.0, "remaining_layers": 4},
        )
        self.assertEqual(rec.status_code, 200, rec.text)
        self.assertEqual(rec.json()["plan"]["remaining_layers"], 4)
        self.assertEqual(rec.json()["plan"]["ad_status"], "known")
        killed = c.post(f"/api/machine/plans/{us['id']}/kill")
        self.assertEqual(killed.status_code, 200)
        self.assertEqual(killed.json()["plan"]["status"], "killed")
        closes = c.get("/api/machine/closes").json()
        self.assertGreaterEqual(len(closes["closes"]), 1)
        self.assertGreaterEqual(len(closes["kb"]), 1)

        prop = c.post("/api/machine/names", json={"symbol": "FOOUSDT", "market": "spot"})
        self.assertEqual(prop.status_code, 200, prop.text)
        need = prop.json()["need"]
        rej = c.post(f"/api/machine/needs-you/{need['id']}/reject")
        self.assertEqual(rej.status_code, 200)
        plans = c.get("/api/machine/plans").json()["plans"]
        self.assertFalse(any(p["symbol"] == "FOOUSDT" for p in plans))

        prop2 = c.post("/api/machine/names", json={"symbol": "BARUSDT", "market": "spot"})
        nid = prop2.json()["need"]["id"]
        acc = c.post(f"/api/machine/needs-you/{nid}/accept")
        self.assertEqual(acc.status_code, 200, acc.text)
        plans = c.get("/api/machine/plans").json()["plans"]
        self.assertTrue(any(p["symbol"] == "BARUSDT" for p in plans))

        axti = next(p for p in plans if p["symbol"] == "AXTISTOCK_USDT")
        line = c.post(
            f"/api/machine/plans/{axti['id']}/propose-line",
            json={"ad_top": 90.0, "ad_bottom": 60.0},
        )
        self.assertEqual(line.status_code, 200)
        lid = line.json()["need"]["id"]
        c.post(f"/api/machine/needs-you/{lid}/accept")
        got = c.get(f"/api/machine/plans/{axti['id']}").json()["plan"]
        self.assertAlmostEqual(got["ad_top"], 90.0)
        self.assertAlmostEqual(got["ad_bottom"], 60.0)

    def test_caps_first_candle_news_and_isolation(self):
        before = self._counts()
        c = self._client(True)
        plans = {p["symbol"]: p for p in c.get("/api/machine/plans").json()["plans"]}
        snap = {
            "ANSEMUSDT|spot": {
                "reds": {"15m": 4},
                "heat_breadth": 4,
                "volume": "climax",
            },
            "AXTISTOCK_USDT|futures": {
                "reds": {"4h": 4},
                "heat_breadth": 4,
                "volume": "climax",
            },
            "BPUSDT|spot": {
                "reds": {"15m": 4},
                "heat_breadth": 4,
                "volume": "climax",
            },
        }
        ev = c.post("/api/machine/evaluate", json={"snapshot": snap})
        self.assertEqual(ev.status_code, 200, ev.text)
        self.assertFalse(ev.json().get("live_orders_sent"))
        live = [p for p in ev.json()["plans"] if p["live"]]
        self.assertEqual(len(live), 2)
        self.assertTrue(all(p["allocated_usd"] <= 100.0001 for p in live))
        self.assertLessEqual(sum(p["allocated_usd"] for p in live), 200.0001)
        # BP has unknown AD — must not invent a line or become a 3rd live.
        bp = next(p for p in ev.json()["plans"] if p["symbol"] == "BPUSDT")
        self.assertEqual(bp["ad_status"], "unknown")
        self.assertFalse(bp["live"])

        # Recut BP so it could play, then cap must block a 3rd live.
        rec = c.post(
            f"/api/machine/plans/{plans['BPUSDT']['id']}/recut",
            json={"ad_top": 3.0, "ad_bottom": 1.0, "remaining_layers": 5},
        )
        self.assertEqual(rec.status_code, 200)
        ev2 = c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "BPUSDT|spot": {
                        "reds": {"15m": 5},
                        "heat_breadth": 4,
                        "volume": "climax",
                    }
                }
            },
        )
        live2 = [p for p in ev2.json()["plans"] if p["live"]]
        self.assertEqual(len(live2), 2)
        cap_actions = [a for a in ev2.json()["actions"] if a.get("reason") == "max_2_live_plays"]
        self.assertTrue(cap_actions)

        # First-candle sit-out on a fresh name after killing one live.
        ansem = next(p for p in ev.json()["plans"] if p["symbol"] == "ANSEMUSDT")
        c.post(f"/api/machine/plans/{ansem['id']}/kill")
        pump_id = next(p["id"] for p in c.get("/api/machine/plans").json()["plans"] if p["symbol"] == "PUMPUSDT")
        c.post(
            f"/api/machine/plans/{pump_id}/recut",
            json={"ad_top": 1.0, "ad_bottom": 0.4, "remaining_layers": 5},
        )
        ev3 = c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "PUMPUSDT|spot": {"reds": {"15m": 1}, "heat_breadth": 1},
                }
            },
        )
        pump = next(p for p in ev3.json()["plans"] if p["symbol"] == "PUMPUSDT")
        self.assertFalse(pump["live"])
        gate = pump.get("gate") or {}
        states = gate.get("tf_states") or []
        self.assertTrue(any(s.get("first_candle_sitout") for s in states))
        ev3b = c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "PUMPUSDT|spot": {"reds": {"15m": 2}, "heat_breadth": 1},
                }
            },
        )
        pump2 = next(p for p in ev3b.json()["plans"] if p["symbol"] == "PUMPUSDT")
        self.assertFalse(pump2["live"])
        gate2 = pump2.get("gate") or {}
        self.assertTrue(
            any(s.get("first_candle_sitout") for s in (gate2.get("tf_states") or []))
        )

        # News kill flattens a live play even with later reds.
        axti = next(p for p in c.get("/api/machine/plans").json()["plans"] if p["symbol"] == "AXTISTOCK_USDT")
        if not axti["live"]:
            c.post(
                "/api/machine/evaluate",
                json={
                    "snapshot": {
                        "AXTISTOCK_USDT|futures": {
                            "reds": {"4h": 6},
                            "heat_breadth": 5,
                            "volume": "climax",
                        }
                    }
                },
            )
        ev4 = c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "AXTISTOCK_USDT|futures": {
                        "reds": {"4h": 8},
                        "heat_breadth": 5,
                        "news": [
                            {
                                "class": "DELIST",
                                "title": "Official delist",
                                "severity": "fatal",
                            }
                        ],
                    }
                }
            },
        )
        axti2 = next(p for p in ev4.json()["plans"] if p["symbol"] == "AXTISTOCK_USDT")
        self.assertFalse(axti2["live"])
        self.assertIn(axti2["status"], ("blocked", "killed", "closed"))
        self.assertTrue(any(a.get("action") == "news_kill" for a in ev4.json()["actions"]))

        after = self._counts()
        self.assertEqual(before, after)

        pos = c.get("/api/positions")
        self.assertEqual(pos.status_code, 200)
        blob = pos.text.lower()
        self.assertNotIn("machine_plans", blob)
        self.assertNotIn('"book": "machine"', blob)
        for p in pos.json().get("positions") or []:
            self.assertNotEqual(p.get("book"), "machine")

        conn = sqlite3.connect(str(self.db))
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        self.assertTrue({"machine_plans", "machine_orders", "machine_closes", "machine_kb"} <= tables)
        lesson_n = conn.execute("SELECT COUNT(*) FROM learning_lessons").fetchone()[0]
        self.assertEqual(lesson_n, 1)
        conn.close()

    def test_store_refuses_foreign_writes(self):
        os.environ["FEATURE_AD_MACHINE"] = "true"
        from mexc_bot.machine.store import MachineStore

        store = MachineStore(self.db)
        with self.assertRaises(RuntimeError):
            store._exec("UPDATE learning_lessons SET text='hacked'")
        with self.assertRaises(RuntimeError):
            store._exec("DELETE FROM journal_trades")
        with self.assertRaises(RuntimeError):
            store._exec("UPDATE alerts SET enabled=0")

    def test_volume_n_from_official_bars_only(self):
        from mexc_bot.machine.hang import official_volume_n

        self.assertEqual(
            official_volume_n([{"c": 1, "v": 100}, {"c": 2, "v": 1_200_000}]),
            1_200_000.0,
        )
        self.assertEqual(official_volume_n([{"v": 50}, {"c": 2}]), 50.0)
        self.assertIsNone(official_volume_n(None))
        self.assertIsNone(official_volume_n([]))
        self.assertIsNone(official_volume_n([{"c": 1}, {"c": 2}]))
        self.assertIsNone(official_volume_n([{"volume": "climax"}]))

        c = self._client(True)
        seeded = c.get("/api/machine/plans").json()["plans"]
        self.assertEqual(len(seeded), 6)
        for p in seeded:
            self.assertIn("volume", p)
            self.assertTrue(p.get("volume_n") in (None,))

        ev = c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "ANSEMUSDT|spot": {
                        "reds": {"15m": 4},
                        "heat_breadth": 4,
                        "volume": "climax",
                        "bars": [{"t": i, "c": 0.2, "v": 1_000} for i in range(8)]
                        + [{"t": 9, "c": 0.2, "v": 1_200_000}],
                    },
                    "AXTISTOCK_USDT|futures": {
                        "reds": {"4h": 4},
                        "heat_breadth": 4,
                        "volume": "climax",
                    },
                }
            },
        )
        self.assertEqual(ev.status_code, 200, ev.text)
        self.assertFalse(ev.json().get("live_orders_sent"))
        ansem = next(p for p in ev.json()["plans"] if p["symbol"] == "ANSEMUSDT")
        self.assertEqual(ansem["volume_n"], 1_200_000)
        self.assertEqual(ansem["volume"], "climax")
        axti = next(p for p in ev.json()["plans"] if p["symbol"] == "AXTISTOCK_USDT")
        self.assertTrue(axti.get("volume_n") in (None,))
        self.assertEqual(axti["volume"], "climax")
        us = next(p for p in ev.json()["plans"] if p["symbol"] == "USUSDT")
        self.assertTrue(us.get("volume_n") in (None,))
        got = c.get(f"/api/machine/plans/{ansem['id']}").json()["plan"]
        self.assertEqual(got["volume_n"], 1_200_000)
        self.assertEqual(got["volume"], "climax")

    def test_named_bar_match_does_not_invent(self):
        from mexc_bot.machine.hang import hang_ad, match_named_bar

        bars = [
            {"ts": 1718510400, "o": 97.0, "h": 97.97, "l": 90.0, "c": 92.0, "v": 10},
            {"ts": 1718600000, "o": 70.0, "h": 71.0, "l": 65.58, "c": 66.0, "v": 12},
        ]
        top = match_named_bar(bars, 97.97, side="top")
        bot = match_named_bar(bars, 65.58, side="bottom")
        self.assertIsNotNone(top)
        self.assertIsNotNone(bot)
        self.assertIn("PHT", top["label"])
        self.assertIsNone(match_named_bar(bars, 98.50, side="top"))
        hung = hang_ad(
            "AXTISTOCK_USDT",
            "futures",
            klines_by_tf={"4h": bars},
        )
        self.assertEqual(hung["ad_status"], "known")
        self.assertEqual(hung["bar_top_label"], top["label"])


if __name__ == "__main__":
    unittest.main()
