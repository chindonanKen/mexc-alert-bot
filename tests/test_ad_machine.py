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

    def test_ad_gap_frac_closer_is_smaller(self):
        from mexc_bot.machine.logic import ad_gap_frac

        self.assertIsNone(ad_gap_frac(None, 1.0))
        self.assertIsNone(ad_gap_frac(1.0, None))
        far = ad_gap_frac(10.0, 5.0)
        near = ad_gap_frac(5.2, 5.0)
        through = ad_gap_frac(4.9, 5.0)
        self.assertGreater(far, near)
        self.assertEqual(through, 0.0)
        penny = ad_gap_frac(0.010, 0.0095)
        stock = ad_gap_frac(80.0, 72.0)
        self.assertLess(penny, stock)

    def test_rank_hung_by_ad_bottom_not_kb(self):
        from mexc_bot.machine.engine import rank_plans
        from mexc_bot.machine.store import MachineStore

        class Fake:
            def list_plans(self, user_id):
                return [
                    {
                        "id": 1,
                        "user_id": 1,
                        "symbol": "FARUSDT",
                        "display_name": "FAR",
                        "market": "spot",
                        "ad_status": "known",
                        "ad_top": 12.0,
                        "ad_bottom": 10.0,
                        "last_price": 11.5,
                        "live": 0,
                        "layers_json": "[]",
                        "zones_json": "[]",
                    },
                    {
                        "id": 2,
                        "user_id": 1,
                        "symbol": "NEARUSDT",
                        "display_name": "NEAR",
                        "market": "spot",
                        "ad_status": "known",
                        "ad_top": 12.0,
                        "ad_bottom": 10.0,
                        "last_price": 10.2,
                        "live": 0,
                        "layers_json": "[]",
                        "zones_json": "[]",
                    },
                    {
                        "id": 3,
                        "user_id": 1,
                        "symbol": "UNKUSDT",
                        "display_name": "UNK",
                        "market": "spot",
                        "ad_status": "unknown",
                        "last_price": 1.0,
                        "live": 0,
                        "layers_json": "[]",
                        "zones_json": "[]",
                    },
                    {
                        "id": 4,
                        "user_id": 1,
                        "symbol": "AXTISTOCK_USDT",
                        "display_name": "AXTI",
                        "market": "futures",
                        "ad_status": "known",
                        "ad_top": 12.0,
                        "ad_bottom": 10.0,
                        "last_price": 10.05,
                        "status": "killed",
                        "live": 0,
                        "layers_json": "[]",
                        "zones_json": "[]",
                    },
                ]

            def list_kb(self, user_id, limit=200):
                return [
                    {"plan_id": 1, "bounce_or_fail": "bounce", "process_ok": 1, "money_pnl": 50},
                ]

            def list_orders(self, *args, **kwargs):
                return []

        ranked = rank_plans(Fake(), 1)
        hung = [p for p in ranked if not p.get("live")]
        self.assertEqual(hung[0]["symbol"], "NEARUSDT")
        self.assertEqual(hung[1]["symbol"], "FARUSDT")
        self.assertEqual(hung[2]["symbol"], "UNKUSDT")
        self.assertEqual(hung[-1]["symbol"], "AXTISTOCK_USDT")

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

    def test_reds_do_not_hang_three_as_law(self):
        from mexc_bot.machine.logic import reds_required, tf_meets_rules
        from mexc_bot.machine import settings as machine_settings

        self.assertFalse(hasattr(machine_settings, "DEFAULT_REDS_REQUIRED"))
        self.assertIsNone(reds_required(None))
        self.assertEqual(reds_required(5), 5)
        two = tf_meets_rules(tf="15m", reds=2, ad_known=True)
        self.assertFalse(two["complete"])
        self.assertFalse(two["reds_ok"])
        self.assertTrue(two["first_candle_sitout"])
        three = tf_meets_rules(tf="15m", reds=3, ad_known=True)
        self.assertFalse(three["complete"])
        self.assertFalse(three["reds_ok"])
        self.assertFalse(three["first_candle_sitout"])
        faster = tf_meets_rules(
            tf="15m", reds=3, ad_known=True, faster_tf=True, play_tf=False
        )
        self.assertFalse(faster["complete"])
        self.assertFalse(faster["first_candle_sitout"])
        self.assertTrue(faster["faster_tf_log_only"])

    def test_faster_tf_reds_do_not_pick_or_complete(self):
        from mexc_bot.machine.logic import pick_working_tf, tf_meets_rules

        states = [
            tf_meets_rules(
                tf="4h", reds=2, ad_known=True, heat_breadth=1, play_tf=True
            ),
            tf_meets_rules(
                tf="15m",
                reds=4,
                ad_known=True,
                heat_breadth=1,
                faster_tf=True,
            ),
        ]
        self.assertTrue(states[0]["first_candle_sitout"])
        self.assertFalse(states[0]["complete"])
        self.assertFalse(states[1]["complete"])
        self.assertFalse(states[1]["first_candle_sitout"])
        self.assertIsNone(pick_working_tf(states))
        pick = pick_working_tf(states, locked_tf="4h")
        self.assertEqual(pick["tf"], "4h")
        self.assertEqual(pick["pick_reason"], "kenneth_play_tf")
        self.assertFalse(pick["complete"])

    def test_two_tf_does_not_pick_faster_from_reds(self):
        from mexc_bot.machine.logic import pick_working_tf, tf_meets_rules

        states = [
            tf_meets_rules(tf="15m", reds=4, ad_known=True, faster_tf=True),
            tf_meets_rules(tf="4h", reds=4, ad_known=True, play_tf=True),
        ]
        pick = pick_working_tf(
            states, respected={"15m": 1.0, "4h": 1.0}, locked_tf="4h"
        )
        self.assertEqual(pick["tf"], "4h")
        self.assertEqual(pick["pick_reason"], "kenneth_play_tf")
        self.assertNotIn("average", str(pick).lower())
        self.assertFalse(states[0]["complete"])
        self.assertFalse(states[1]["complete"])

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

    def test_dump_depth_layers_not_equal_spread(self):
        from mexc_bot.machine.logic import (
            dump_depth_layers,
            equal_spread_prices,
        )

        layers = dump_depth_layers(10.0, 5.0, budget_usd=100)
        self.assertGreaterEqual(len(layers), 5)
        ad = [L for L in layers if L.get("band") == "ad"]
        panic = [L for L in layers if L.get("band") == "panic"]
        self.assertEqual(len(ad), 5)
        self.assertEqual(len(panic), 3)
        self.assertAlmostEqual(sum(x["usd"] for x in layers), 100.0, places=3)
        self.assertLess(ad[-1]["price"], 5.0)
        self.assertGreater(ad[-1]["price"], 0)
        asteroid = dump_depth_layers(0.00009, 0.00001942, budget_usd=100)
        ast_ad = [L for L in asteroid if L.get("band") == "ad"]
        self.assertAlmostEqual(ast_ad[0]["price"], 0.00002399, places=7)
        self.assertAlmostEqual(ast_ad[1]["price"], 0.00002271, places=7)
        self.assertAlmostEqual(ast_ad[2]["price"], 0.00002142, places=7)
        self.assertAlmostEqual(ast_ad[3]["price"], 0.00002014, places=7)
        self.assertAlmostEqual(ast_ad[4]["price"], 0.00001886, places=7)
        self.assertAlmostEqual(ast_ad[0]["usd"], 5.0, places=1)
        self.assertAlmostEqual(ast_ad[4]["usd"], 15.0, places=1)
        self.assertLess(ast_ad[4]["price"], 0.00001942)
        forbidden = equal_spread_prices(10.0, 5.0, 5)
        self.assertNotAlmostEqual(ad[0]["price"], forbidden[0], places=4)
        # Q1 = B − L × 0.10
        self.assertAlmostEqual(panic[0]["price"], 5.0 - 5.0 * 0.10, places=6)
        for L in layers:
            self.assertGreater(L["price"], 0)

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
            "MACHINE_TAPE_LOOP": os.environ.get("MACHINE_TAPE_LOOP"),
        }
        os.environ["ALERTS_FILE"] = str(self.db)
        os.environ["DESK_USER_ID"] = "8630949601"
        os.environ["MACHINE_TAPE_LOOP"] = "false"
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
        self.assertIn("git_sha", h)
        self.assertIn("image_tag", h)
        self.assertEqual(c.get("/api/machine/plans").status_code, 404)
        self.assertEqual(c.get("/api/machine/closes").status_code, 404)
        self.assertEqual(c.get("/api/machine/ranks").status_code, 404)
        self.assertEqual(c.get("/api/machine/trades?symbol=BTCUSDT").status_code, 404)
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
        self.assertIn("No written plan, sit.", us["decision"])
        self.assertEqual(us["decision_reason"], "sit_out")
        self.assertIn("decision", ansem)
        self.assertIn("decision_reason", ansem)
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
        css = c.get("/assets/machine.css?v=s7")
        self.assertEqual(css.status_code, 200)
        self.assertIn(".why", css.text)
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
        js = c.get("/assets/machine.js?v=s7")
        self.assertEqual(js.status_code, 200)
        self.assertIn("waiting · $200 book", js.text)
        self.assertIn("of 2 live", js.text)
        self.assertIn("${linePrice(p)}", js.text)
        self.assertIn("GLOSS.LINE", js.text)
        self.assertIn("working_orders", js.text)
        self.assertIn("function restClock", js.text)
        self.assertIn('+ "m"', js.text)
        self.assertIn("function fmtVol", js.text)
        self.assertIn('"M"', js.text)
        self.assertIn("official last price", js.text)
        self.assertIn("this copy, top → bottom", js.text)
        self.assertIn("next layer dollars, or paper money on a close", js.text)
        self.assertIn("function nextText", js.text)
        self.assertIn("p.money_pnl", js.text)
        self.assertIn("red candles on this TF", js.text)
        self.assertIn("last bar in dollars", js.text)
        self.assertIn("delist/scam or clear", js.text)
        self.assertIn("time since the play armed", js.text)
        self.assertIn("next layer price", js.text)
        self.assertIn("layer ${L.idx}", js.text)
        self.assertIn("unknown — no layers", js.text)
        self.assertIn("last_price", js.text)
        self.assertIn("p.decision", js.text)
        self.assertIn("whyLine", js.text)
        self.assertIn('class="why"', js.text)
        self.assertIn("btnDumpDepth", js.text)
        self.assertIn("/layers", js.text)
        self.assertIn("function patchStage", js.text)
        self.assertIn("setFig", js.text)
        self.assertIn("enterText", js.text)
        self.assertIn("metText", js.text)
        self.assertIn('data-k="AD"', js.text)
        self.assertIn('data-k="NEWS"', js.text)
        self.assertNotIn("GLOSS.WHY", js.text)
        self.assertNotIn("what the student", js.text.lower())
        self.assertNotIn('resting ? "rest"', js.text)
        self.assertNotIn("ASTEROID", js.text)
        self.assertNotIn("ORION", js.text)
        self.assertNotIn("ECLIPSE", js.text)
        self.assertNotIn("<canvas", page.text.lower())
        self.assertNotIn("tradingview", js.text.lower())

    def test_recut_kill_approve(self):
        c = self._client(True)
        plans = c.get("/api/machine/plans").json()["plans"]
        us = next(p for p in plans if p["symbol"] == "USUSDT")
        rec = c.post(
            f"/api/machine/plans/{us['id']}/recut",
            json={"ad_top": 2.0, "ad_bottom": 1.0, "remaining_layers": 4},
        )
        self.assertEqual(rec.status_code, 200, rec.text)
        rec_plan = rec.json()["plan"]
        self.assertEqual(rec_plan["ad_status"], "known")
        self.assertEqual(len(rec_plan["layers"]), 8)
        self.assertLess(rec_plan["layers"][4]["price"], 1.0)
        forbidden = [2.0 - ((i + 1) / 5) * 1.0 for i in range(5)]
        self.assertNotAlmostEqual(rec_plan["layers"][0]["price"], forbidden[0], places=4)
        layers_get = c.get(f"/api/machine/plans/{us['id']}/layers")
        self.assertEqual(layers_get.status_code, 200, layers_get.text)
        layers_post = c.post(
            f"/api/machine/plans/{us['id']}/layers",
            json={"ad_top": 2.0, "ad_bottom": 1.0},
        )
        self.assertEqual(layers_post.status_code, 200, layers_post.text)
        self.assertFalse(layers_post.json().get("live_orders_sent"))
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
                "last_price": 0.145,
                "reds": {"15m": 4},
                "heat_breadth": 1,
            },
            "AXTISTOCK_USDT|futures": {
                "last_price": 65.58,
                "reds": {"4h": 4},
                "heat_breadth": 1,
            },
            "BPUSDT|spot": {
                "reds": {"15m": 4},
                "heat_breadth": 1,
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
                        "last_price": 1.0,
                        "reds": {"15m": 5},
                        "heat_breadth": 1,
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
                            "last_price": 65.58,
                            "reds": {"4h": 6},
                            "heat_breadth": 1,
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

    def test_last_price_layers_paper_fill_and_no_send(self):
        from mexc_bot.machine.tape import official_last_price, official_reds
        from mexc_bot.webapi.actions import live_orders_allowed

        self.assertFalse(live_orders_allowed())
        self.assertEqual(official_last_price(ticker=0.182), 0.182)
        self.assertEqual(official_last_price(bars=[{"c": 0.171, "v": 9}]), 0.171)
        self.assertEqual(
            official_last_price(ticker=0.182, bars=[{"c": 0.171}]), 0.182
        )
        self.assertIsNone(official_last_price())
        self.assertIsNone(official_last_price(ticker="climax"))
        closed = [{"o": 3, "c": 2}, {"o": 2, "c": 1}]
        self.assertEqual(official_reds(closed + [{"o": 1, "c": 1.2}]), 2)
        self.assertEqual(official_reds(closed + [{"o": 1.2, "c": 1.0}]), 3)
        self.assertIsNone(official_reds([]))
        self.assertIsNone(official_reds(None))

        c = self._client(True)
        plans = {p["symbol"]: p for p in c.get("/api/machine/plans").json()["plans"]}
        ansem = plans["ANSEMUSDT"]
        self.assertTrue(ansem["layers"])
        self.assertLessEqual(sum(float(L["usd"]) for L in ansem["layers"]), 100.0001)
        for layer in ansem["layers"]:
            self.assertIn("idx", layer)
            self.assertIn("price", layer)
            self.assertIn("usd", layer)
        us = plans["USUSDT"]
        self.assertEqual(us["ad_status"], "unknown")
        self.assertEqual(us["layers"], [])
        self.assertTrue(us.get("last_price") in (None,))

        ev = c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "ANSEMUSDT|spot": {
                        "last_price": 0.182,
                        "reds": {"15m": 4},
                        "heat_breadth": 1,
                    }
                }
            },
        )
        self.assertEqual(ev.status_code, 200, ev.text)
        self.assertFalse(ev.json().get("live_orders_sent"))
        ansem2 = next(p for p in ev.json()["plans"] if p["symbol"] == "ANSEMUSDT")
        self.assertEqual(ansem2["last_price"], 0.182)
        self.assertNotAlmostEqual(float(ansem2["last_price"]), float(ansem2["ad_top"]))
        self.assertFalse(ansem2["live"])
        us2 = next(p for p in ev.json()["plans"] if p["symbol"] == "USUSDT")
        self.assertTrue(us2.get("last_price") in (None,))
        self.assertEqual(us2["layers"], [])

        ev2 = c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "ANSEMUSDT|spot": {
                        "last_price": 0.145,
                        "reds": {"15m": 4},
                        "heat_breadth": 1,
                    }
                }
            },
        )
        self.assertFalse(ev2.json().get("live_orders_sent"))
        self.assertTrue(
            any(
                a.get("action") in {"paper_fill", "paper-buy", "arm"}
                for a in ev2.json().get("actions") or []
            )
            or any(
                o.get("status") == "filled"
                for o in (
                    next(p for p in ev2.json()["plans"] if p["symbol"] == "ANSEMUSDT")
                    .get("layers")
                    or []
                )
            )
        )
        got = next(p for p in ev2.json()["plans"] if p["symbol"] == "ANSEMUSDT")
        self.assertLess(int(got.get("remaining_layers") or 0), len(ansem2["layers"]))

    def test_volume_n_from_official_bars_only(self):
        from mexc_bot.machine.hang import official_volume_n

        self.assertEqual(
            official_volume_n([{"c": 1, "v": 100}, {"c": 2, "v": 1_200_000}]),
            2_400_000.0,
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
        self.assertEqual(ansem["volume_n"], 240_000.0)
        self.assertEqual(ansem["volume"], "climax")
        axti = next(p for p in ev.json()["plans"] if p["symbol"] == "AXTISTOCK_USDT")
        self.assertTrue(axti.get("volume_n") in (None,))
        self.assertEqual(axti["volume"], "climax")
        us = next(p for p in ev.json()["plans"] if p["symbol"] == "USUSDT")
        self.assertTrue(us.get("volume_n") in (None,))
        got = c.get(f"/api/machine/plans/{ansem['id']}").json()["plan"]
        self.assertEqual(got["volume_n"], 240_000.0)
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


class TestMachineDecisionLine(unittest.TestCase):
    """Why-strings from evaluate gates, not UI chrome."""

    def test_decision_line_voice(self):
        from mexc_bot.machine.logic import decision_line, last_under_ad

        self.assertEqual(
            decision_line(kind="wait")["decision"],
            "Grind, no volume, wait.",
        )
        self.assertEqual(
            decision_line(
                kind="arm", reds=3, tf="15m", volume="climax"
            )["decision"],
            "3 red 15m, volume at the AD, no news, taking it.",
        )
        self.assertEqual(
            decision_line(kind="sit_out", reds=1)["decision"],
            "First red at the AD, sit out.",
        )
        self.assertEqual(
            decision_line(kind="sit_out", reds=2)["decision"],
            "Second red at the AD, sit out.",
        )
        self.assertEqual(decision_line(kind="news")["decision"], "News flatten.")
        self.assertEqual(
            decision_line(kind="fail")["decision"],
            "Reassess. Do not flatten from a clock.",
        )
        self.assertEqual(
            decision_line(kind="watch")["decision"],
            "Watch. Waiting for the line.",
        )
        self.assertEqual(
            decision_line(kind="cap")["decision"],
            "Two live, wait.",
        )
        self.assertEqual(decision_line(kind="kill")["decision"], "Kill.")
        self.assertTrue(last_under_ad(0.14, 0.145, ad_known=True))
        self.assertFalse(last_under_ad(0.20, 0.145, ad_known=True))
        self.assertFalse(last_under_ad(0.14, 0.145, ad_known=False))

    def test_failed_ad_clock_is_never_a_fail(self):
        from mexc_bot.machine.logic import failed_ad
        from mexc_bot.machine.settings import bounce_seconds

        clock = bounce_seconds("15m")
        self.assertEqual(clock, 45 * 60)
        self.assertFalse(
            failed_ad(armed_at=1_000.0, now=1_000.0 + clock + 1, tf="15m", bounced=False)
        )
        self.assertFalse(
            failed_ad(armed_at=1_000.0, now=1_000.0 + clock + 1, tf="15m", bounced=True)
        )

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "alerts.db"
        self._env = {
            "ALERTS_FILE": os.environ.get("ALERTS_FILE"),
            "DESK_USER_ID": os.environ.get("DESK_USER_ID"),
            "DESK_API_TOKEN": os.environ.get("DESK_API_TOKEN"),
            "WEB_UI_TOKEN": os.environ.get("WEB_UI_TOKEN"),
            "FEATURE_AD_MACHINE": os.environ.get("FEATURE_AD_MACHINE"),
            "MACHINE_TAPE_LOOP": os.environ.get("MACHINE_TAPE_LOOP"),
        }
        os.environ["ALERTS_FILE"] = str(self.db)
        os.environ["DESK_USER_ID"] = "8630949601"
        os.environ["FEATURE_AD_MACHINE"] = "true"
        os.environ["MACHINE_TAPE_LOOP"] = "false"
        os.environ.pop("DESK_API_TOKEN", None)
        os.environ.pop("WEB_UI_TOKEN", None)

    def tearDown(self):
        self.tmp.cleanup()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _client(self):
        from fastapi.testclient import TestClient
        from mexc_bot.webapi.app import create_app

        return TestClient(create_app())

    def test_evaluate_writes_why_and_persists(self):
        c = self._client()
        plans = {p["symbol"]: p for p in c.get("/api/machine/plans").json()["plans"]}
        self.assertIn("No written plan, sit.", plans["USUSDT"]["decision"])
        self.assertEqual(plans["USUSDT"]["decision_reason"], "sit_out")

        sit = c.post(
            "/api/machine/evaluate",
            json={"snapshot": {"ANSEMUSDT|spot": {"reds": {"15m": 1}}}},
        )
        self.assertEqual(sit.status_code, 200, sit.text)
        ansem = next(p for p in sit.json()["plans"] if p["symbol"] == "ANSEMUSDT")
        self.assertFalse(ansem["live"])
        self.assertIn("sit out", ansem["decision"].lower())
        self.assertEqual(ansem["decision_reason"], "sit_out")
        self.assertTrue(
            any(
                a.get("action") == "sit_out" and a.get("plan_id") == ansem["id"]
                for a in sit.json()["actions"]
            )
        )
        got = c.get(f"/api/machine/plans/{ansem['id']}").json()["plan"]
        self.assertIn("sit out", got["decision"].lower())

        sit2 = c.post(
            "/api/machine/evaluate",
            json={"snapshot": {"ANSEMUSDT|spot": {"reds": {"15m": 2}}}},
        )
        ansem2 = next(p for p in sit2.json()["plans"] if p["symbol"] == "ANSEMUSDT")
        self.assertIn("sit out", ansem2["decision"].lower())

        wait = c.post(
            "/api/machine/evaluate",
            json={"snapshot": {"ANSEMUSDT|spot": {"reds": {"15m": 0}}}},
        )
        ansem_w = next(p for p in wait.json()["plans"] if p["symbol"] == "ANSEMUSDT")
        self.assertIn("wait", ansem_w["decision"].lower())
        self.assertEqual(ansem_w["decision_reason"], "wait")

        no_law = c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "ANSEMUSDT|spot": {
                        "reds": {"15m": 3},
                        "volume": "climax",
                        "heat_breadth": 1,
                    }
                }
            },
        )
        ansem_n = next(p for p in no_law.json()["plans"] if p["symbol"] == "ANSEMUSDT")
        self.assertFalse(ansem_n["live"])
        self.assertNotEqual(ansem_n["decision_reason"], "arm")
        self.assertFalse(
            any(
                a.get("action") == "arm" and a.get("plan_id") == ansem_n["id"]
                for a in no_law.json()["actions"]
            )
        )

        arm = c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "ANSEMUSDT|spot": {
                        "last_price": 0.145,
                        "reds": {"15m": 1},
                        "heat_breadth": 1,
                    }
                }
            },
        )
        ansem_a = next(p for p in arm.json()["plans"] if p["symbol"] == "ANSEMUSDT")
        self.assertTrue(ansem_a["live"])
        self.assertTrue(ansem_a.get("met"))
        self.assertIn("at this chart's AD", ansem_a["decision"])
        self.assertIn("paper_buy", ansem_a["decision_reason"])
        self.assertTrue(
            any(
                a.get("action") in {"arm", "paper_fill", "paper-buy", "paper_buy"}
                and a.get("plan_id") == ansem_a["id"]
                for a in arm.json()["actions"]
            )
        )
        refresh = c.get("/api/machine/plans").json()
        ansem_r = next(p for p in refresh["plans"] if p["symbol"] == "ANSEMUSDT")
        self.assertIn("at this chart's AD", ansem_r["decision"])

        axti_arm = c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "AXTISTOCK_USDT|futures": {
                        "last_price": 65.58,
                        "reds": {"4h": 2},
                        "heat_breadth": 1,
                    }
                }
            },
        )
        axti_live = next(
            p for p in axti_arm.json()["plans"] if p["symbol"] == "AXTISTOCK_USDT"
        )
        self.assertTrue(axti_live["live"])
        pump_id = plans["PUMPUSDT"]["id"]
        c.post(
            f"/api/machine/plans/{pump_id}/recut",
            json={"ad_top": 1.0, "ad_bottom": 0.4, "remaining_layers": 5},
        )
        cap = c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "PUMPUSDT|spot": {
                        "last_price": 0.4,
                        "reds": {"15m": 4},
                        "heat_breadth": 1,
                    }
                }
            },
        )
        pump = next(p for p in cap.json()["plans"] if p["symbol"] == "PUMPUSDT")
        self.assertFalse(pump["live"])
        self.assertEqual(pump["decision"], "Two live, wait.")
        self.assertEqual(pump["decision_reason"], "cap")

        spent = c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "ANSEMUSDT|spot": {
                        "last_price": 0.145,
                        "reds": {"15m": 3},
                        "volume": "climax",
                    }
                }
            },
        )
        ansem_f = next(p for p in spent.json()["plans"] if p["symbol"] == "ANSEMUSDT")
        self.assertTrue(ansem_f["live"])
        self.assertNotEqual(ansem_f["decision"], "Last under the AD, spent.")
        self.assertNotEqual(ansem_f["decision_reason"], "fail")
        self.assertFalse(
            any(a.get("action") == "failed_ad" for a in spent.json()["actions"])
        )

        news = c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "AXTISTOCK_USDT|futures": {
                        "reds": {"4h": 4},
                        "volume": "climax",
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
        axti = next(p for p in news.json()["plans"] if p["symbol"] == "AXTISTOCK_USDT")
        self.assertIn("flatten", axti["decision"].lower())
        self.assertEqual(axti["decision_reason"], "news")

        killed = c.post(f"/api/machine/plans/{plans['BPUSDT']['id']}/kill")
        self.assertEqual(killed.status_code, 200)
        self.assertEqual(killed.json()["plan"]["decision"], "Kill.")
        self.assertEqual(killed.json()["plan"]["decision_reason"], "kill")
        again = c.get(f"/api/machine/plans/{plans['BPUSDT']['id']}").json()["plan"]
        self.assertEqual(again["decision"], "Kill.")


class TestMachineLockedPathAndFail(unittest.TestCase):
    """2026-08-27 recut: no 15m≥3 law, no bounce-clock flatten."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "alerts.db"
        self._env = {
            "ALERTS_FILE": os.environ.get("ALERTS_FILE"),
            "DESK_USER_ID": os.environ.get("DESK_USER_ID"),
            "DESK_API_TOKEN": os.environ.get("DESK_API_TOKEN"),
            "WEB_UI_TOKEN": os.environ.get("WEB_UI_TOKEN"),
            "FEATURE_AD_MACHINE": os.environ.get("FEATURE_AD_MACHINE"),
            "MACHINE_TAPE_LOOP": os.environ.get("MACHINE_TAPE_LOOP"),
        }
        os.environ["ALERTS_FILE"] = str(self.db)
        os.environ["DESK_USER_ID"] = "8630949601"
        os.environ["FEATURE_AD_MACHINE"] = "true"
        os.environ["MACHINE_TAPE_LOOP"] = "false"
        os.environ.pop("DESK_API_TOKEN", None)
        os.environ.pop("WEB_UI_TOKEN", None)

    def tearDown(self):
        self.tmp.cleanup()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _client(self):
        from fastapi.testclient import TestClient
        from mexc_bot.webapi.app import create_app

        return TestClient(create_app())

    def test_faster_tf_three_reds_does_not_auto_take_or_sit_as_law(self):
        c = self._client()
        plans = {p["symbol"]: p for p in c.get("/api/machine/plans").json()["plans"]}
        axti_id = plans["AXTISTOCK_USDT"]["id"]
        self.assertEqual(plans["AXTISTOCK_USDT"]["tf"], "4h")

        faster = c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "AXTISTOCK_USDT|futures": {
                        "reds": {"15m": 3, "4h": 4},
                        "volume": "climax",
                        "heat_breadth": 1,
                    }
                }
            },
        )
        self.assertEqual(faster.status_code, 200, faster.text)
        axti = next(
            p for p in faster.json()["plans"] if p["symbol"] == "AXTISTOCK_USDT"
        )
        self.assertFalse(axti["live"])
        self.assertNotEqual(axti["decision_reason"], "arm")
        self.assertNotEqual(axti["decision_reason"], "sit_out")
        axti_acts = [
            a for a in faster.json()["actions"] if a.get("plan_id") == axti["id"]
        ]
        self.assertFalse(
            any(a.get("action") in {"arm", "sit_out"} for a in axti_acts)
        )
        gate = axti.get("gate") or {}
        faster_states = [
            s
            for s in (gate.get("tf_states") or [])
            if s.get("tf") == "15m"
        ]
        self.assertTrue(faster_states)
        self.assertTrue(faster_states[0].get("faster_tf_log_only"))
        self.assertFalse(faster_states[0].get("complete"))
        self.assertFalse(faster_states[0].get("first_candle_sitout"))
        self.assertEqual(gate.get("faster_tf_reds"), [{"tf": "15m", "reds": 3}])
        self.assertFalse(gate.get("kenneth_override"))

        sit_play = c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "AXTISTOCK_USDT|futures": {
                        "reds": {"15m": 3, "4h": 2},
                        "volume": "climax",
                        "heat_breadth": 1,
                    }
                }
            },
        )
        axti2 = next(
            p for p in sit_play.json()["plans"] if p["symbol"] == "AXTISTOCK_USDT"
        )
        self.assertFalse(axti2["live"])
        self.assertEqual(axti2["decision_reason"], "sit_out")
        play_state = next(
            s
            for s in (axti2.get("gate") or {}).get("tf_states") or []
            if s.get("tf") == "4h"
        )
        self.assertTrue(play_state.get("first_candle_sitout"))
        faster_state = next(
            s
            for s in (axti2.get("gate") or {}).get("tf_states") or []
            if s.get("tf") == "15m"
        )
        self.assertFalse(faster_state.get("first_candle_sitout"))
        self.assertFalse(faster_state.get("complete"))
        self.assertEqual(axti2["id"], axti_id)

    def test_bounce_clock_expiry_does_not_close_a_plan(self):
        from mexc_bot.machine.settings import bounce_seconds

        c = self._client()
        plans = {p["symbol"]: p for p in c.get("/api/machine/plans").json()["plans"]}
        armed_at = 1_700_000_000.0
        arm = c.post(
            "/api/machine/evaluate",
            json={
                "now": armed_at,
                "snapshot": {
                    "ANSEMUSDT|spot": {
                        "last_price": 0.145,
                        "reds": {"15m": 3},
                        "heat_breadth": 1,
                    }
                },
            },
        )
        self.assertEqual(arm.status_code, 200, arm.text)
        ansem = next(p for p in arm.json()["plans"] if p["symbol"] == "ANSEMUSDT")
        self.assertTrue(ansem["live"])
        clock = bounce_seconds(ansem.get("tf") or "15m")
        later = c.post(
            "/api/machine/evaluate",
            json={
                "now": armed_at + clock + 1,
                "snapshot": {
                    "ANSEMUSDT|spot": {
                        "last_price": 0.145,
                        "reds": {"15m": 3},
                        "volume": "climax",
                        "bounced": False,
                    }
                },
            },
        )
        self.assertEqual(later.status_code, 200, later.text)
        still = next(p for p in later.json()["plans"] if p["symbol"] == "ANSEMUSDT")
        self.assertTrue(still["live"])
        self.assertEqual(still["status"], "live")
        self.assertFalse(
            any(a.get("action") == "failed_ad" for a in later.json()["actions"])
        )
        self.assertNotEqual(still["decision_reason"], "fail")
        self.assertNotEqual(still["decision"], "Last under the AD, spent.")


class TestMachinePaperReact(unittest.TestCase):
    """Done-bar: at-AD take, quiet grind, fast dump, no rebuy."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "alerts.db"
        self._env = {
            "ALERTS_FILE": os.environ.get("ALERTS_FILE"),
            "DESK_USER_ID": os.environ.get("DESK_USER_ID"),
            "DESK_API_TOKEN": os.environ.get("DESK_API_TOKEN"),
            "WEB_UI_TOKEN": os.environ.get("WEB_UI_TOKEN"),
            "FEATURE_AD_MACHINE": os.environ.get("FEATURE_AD_MACHINE"),
            "MACHINE_TAPE_LOOP": os.environ.get("MACHINE_TAPE_LOOP"),
        }
        os.environ["ALERTS_FILE"] = str(self.db)
        os.environ["DESK_USER_ID"] = "8630949601"
        os.environ["FEATURE_AD_MACHINE"] = "true"
        os.environ["MACHINE_TAPE_LOOP"] = "false"
        os.environ.pop("DESK_API_TOKEN", None)
        os.environ.pop("WEB_UI_TOKEN", None)

    def tearDown(self):
        self.tmp.cleanup()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _client(self):
        from fastapi.testclient import TestClient
        from mexc_bot.webapi.app import create_app

        return TestClient(create_app())

    def test_at_ad_takes_on_sideways_no_volume_no_override(self):
        c = self._client()
        ev = c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "ANSEMUSDT|spot": {
                        "last_price": 0.145,
                        "reds": {"15m": 1},
                        "heat_breadth": 1,
                    }
                }
            },
        )
        self.assertEqual(ev.status_code, 200, ev.text)
        self.assertFalse(ev.json().get("live_orders_sent"))
        ansem = next(p for p in ev.json()["plans"] if p["symbol"] == "ANSEMUSDT")
        self.assertTrue(ansem["live"])
        self.assertTrue(ansem.get("met"))
        self.assertIn("at this chart's AD", ansem["decision"])
        self.assertTrue(ansem.get("filled_entry") or ansem.get("intended_entry"))
        log = c.get("/api/machine/log").json()["log"]
        self.assertTrue(any("atad.take" in str(r.get("rule_ids")) for r in log))
        feed = c.get("/api/machine/feed").json()["feed"]
        self.assertTrue(feed)

    def test_quiet_grind_buys_nothing(self):
        c = self._client()
        ev = c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "ANSEMUSDT|spot": {
                        "last_price": 0.22,
                        "reds": {"15m": 0},
                        "heat_breadth": 1,
                        "quiet_grind": True,
                    }
                }
            },
        )
        ansem = next(p for p in ev.json()["plans"] if p["symbol"] == "ANSEMUSDT")
        self.assertFalse(ansem["live"])
        self.assertIn("wait", ansem["decision"].lower())
        self.assertFalse(
            any(
                L.get("status") == "filled"
                for L in (ansem.get("layers") or [])
            )
        )

    def test_fast_dump_starts_at_first_real_volume_layer(self):
        from mexc_bot.machine.logic import dump_depth_layers

        c = self._client()
        dump_depth_layers(0.356, 0.145, budget_usd=100)
        ev = c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "ANSEMUSDT|spot": {
                        "last_price": 0.157,
                        "reds": {"15m": 4},
                        "heat_breadth": 1,
                        "fast_dump_volume": True,
                        "vol_spike": True,
                    }
                }
            },
        )
        self.assertEqual(ev.status_code, 200, ev.text)
        ansem = next(p for p in ev.json()["plans"] if p["symbol"] == "ANSEMUSDT")
        self.assertTrue(ansem["live"])
        self.assertIn("fast dump", ansem["decision"].lower())
        filled = [L for L in (ansem.get("layers") or []) if L.get("status") == "filled"]
        self.assertTrue(filled)
        self.assertLessEqual(max(int(L["idx"]) for L in filled), 3)

    def test_sold_bounce_does_not_rebuy_quiet_walk(self):
        c = self._client()
        c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "ANSEMUSDT|spot": {
                        "last_price": 0.145,
                        "reds": {"15m": 3},
                        "heat_breadth": 1,
                    }
                }
            },
        )
        sold = c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "ANSEMUSDT|spot": {
                        "last_price": 0.18,
                        "reds": {"15m": 0},
                        "bounced": True,
                        "money_pnl": 1.0,
                    }
                }
            },
        )
        sold_row = next(p for p in sold.json()["plans"] if p["symbol"] == "ANSEMUSDT")
        self.assertTrue(sold_row["live"])
        self.assertTrue(sold_row.get("filled_exit"))
        self.assertAlmostEqual(float(sold_row["filled_exit"]["price"]), 0.18)
        self.assertGreater(float(sold_row.get("allocated_usd") or 0), 0)
        self.assertTrue(
            any(
                a.get("action") == "paper-sell" and a.get("plan_id") == sold_row["id"]
                for a in sold.json()["actions"]
            )
        )

    def test_live_tape_panic_up_sells_without_bounced_flag(self):
        from mexc_bot.machine.tape import panic_up_from_tape, snapshot_for_plan

        plan = {
            "live": True,
            "tf": "15m",
            "ad_bottom": 0.145,
            "leftover_avg": 0.145,
            "play_json": {},
        }
        bars_1m = [
            {"o": 0.14, "c": 0.141, "v": 10, "q": 100},
            {"o": 0.14, "c": 0.141, "v": 10, "q": 100},
            {"o": 0.14, "c": 0.141, "v": 10, "q": 100},
            {"o": 0.14, "c": 0.141, "v": 10, "q": 100},
            {"o": 0.15, "c": 0.18, "v": 80, "q": 400},
        ]
        self.assertTrue(
            panic_up_from_tape(
                plan, last=0.18, bars_1m=bars_1m, vol_usd_fast=400
            )
        )
        snap = snapshot_for_plan(plan, ticker=0.18, bars_1m=bars_1m)
        self.assertTrue(snap.get("panic_up_volume"))
        c = self._client()
        c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "ANSEMUSDT|spot": {
                        "last_price": 0.145,
                        "reds": {"15m": 3},
                        "heat_breadth": 1,
                    }
                }
            },
        )
        ev = c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "ANSEMUSDT|spot": {
                        "last_price": 0.18,
                        "reds": {"15m": 0},
                        "heat_breadth": 1,
                        "bars_1m": bars_1m,
                        "vol_usd_fast": 400,
                        "panic_up_volume": True,
                    }
                }
            },
        )
        ansem = next(p for p in ev.json()["plans"] if p["symbol"] == "ANSEMUSDT")
        self.assertTrue(ansem["live"])
        self.assertTrue(ansem.get("filled_exit"))
        self.assertAlmostEqual(float(ansem["filled_exit"]["price"]), 0.18)
        self.assertGreater(float(ansem.get("allocated_usd") or 0), 0)
        walk = c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "ANSEMUSDT|spot": {
                        "last_price": 0.145,
                        "reds": {"15m": 1},
                        "heat_breadth": 1,
                        "sold_bounce": True,
                    }
                }
            },
        )
        ansem = next(p for p in walk.json()["plans"] if p["symbol"] == "ANSEMUSDT")
        self.assertIn("not buying the quiet walk", ansem["decision"].lower())
        self.assertLess(float(ansem.get("allocated_usd") or 0), 50)

    def test_live_payload_has_exit_and_filtered_log(self):
        c = self._client()
        ev = c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "ANSEMUSDT|spot": {
                        "last_price": 0.145,
                        "reds": {"15m": 3},
                        "heat_breadth": 1,
                    }
                }
            },
        )
        ansem = next(p for p in ev.json()["plans"] if p["symbol"] == "ANSEMUSDT")
        self.assertTrue(ansem["live"])
        self.assertTrue(ansem.get("intended_exit"))
        self.assertEqual(ansem["intended_exit"].get("note"), "re-read this TF")
        self.assertNotIn("leftover_avg", ansem)
        self.assertIn("paper_leftover_avg", ansem)
        log = c.get("/api/machine/plans").json()["log"]
        self.assertTrue(log)
        self.assertTrue(
            any(
                str(r.get("action") or "") in ("paper-buy", "paper_fill", "arm")
                for r in log
            )
        )

    def test_into_base_sells_invested_bag(self):
        c = self._client()
        c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "ANSEMUSDT|spot": {
                        "last_price": 0.145,
                        "reds": {"15m": 3},
                        "heat_breadth": 1,
                    }
                }
            },
        )
        ev = c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "ANSEMUSDT|spot": {
                        "last_price": 0.22,
                        "reds": {"15m": 0},
                        "into_base": True,
                    }
                }
            },
        )
        ansem = next(p for p in ev.json()["plans"] if p["symbol"] == "ANSEMUSDT")
        self.assertFalse(ansem["live"])
        self.assertTrue(ansem.get("filled_exit"))
        self.assertIn("big base", ansem["decision"].lower())

    def test_add_panic_on_fast_dump_past_b_not_flatten(self):
        c = self._client()
        c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "ANSEMUSDT|spot": {
                        "last_price": 0.145,
                        "reds": {"15m": 3},
                        "heat_breadth": 1,
                    }
                }
            },
        )
        ev = c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "ANSEMUSDT|spot": {
                        "last_price": 0.12,
                        "reds": {"15m": 4},
                        "heat_breadth": 1,
                        "fast_dump_volume": True,
                        "vol_spike": True,
                    }
                }
            },
        )
        ansem = next(p for p in ev.json()["plans"] if p["symbol"] == "ANSEMUSDT")
        self.assertTrue(ansem["live"])
        self.assertIn("panic", ansem["decision"].lower())
        panic_filled = [
            L
            for L in (ansem.get("layers") or [])
            if L.get("band") == "panic" and L.get("status") == "filled"
        ]
        self.assertTrue(panic_filled)

    def test_grind_does_not_sit_out_at_ad(self):
        c = self._client()
        ev = c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "ANSEMUSDT|spot": {
                        "last_price": 0.145,
                        "reds": {"15m": 1},
                        "heat_breadth": 1,
                        "board": {
                            "grind": True,
                            "panic": False,
                            "names": 5,
                            "slow": 4,
                            "fast": 1,
                        },
                    }
                }
            },
        )
        ansem = next(p for p in ev.json()["plans"] if p["symbol"] == "ANSEMUSDT")
        self.assertTrue(ansem["live"])
        self.assertIn("at this chart's AD", ansem["decision"])

    def test_lower_pack_on_drop_past_ad_without_panic(self):
        c = self._client()
        first = c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "ANSEMUSDT|spot": {
                        "last_price": 0.145,
                        "reds": {"15m": 3},
                        "heat_breadth": 1,
                    }
                }
            },
        )
        before = next(p for p in first.json()["plans"] if p["symbol"] == "ANSEMUSDT")
        bot = float(before["ad_bottom"])
        ev = c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "ANSEMUSDT|spot": {
                        "last_price": 0.10,
                        "reds": {"15m": 0},
                        "heat_breadth": 1,
                        "quiet_grind": True,
                    }
                }
            },
        )
        ansem = next(p for p in ev.json()["plans"] if p["symbol"] == "ANSEMUSDT")
        self.assertTrue(ansem["live"])
        self.assertAlmostEqual(float(ansem["ad_bottom"]), bot)
        self.assertAlmostEqual(float(ansem["ad_top"]), float(before["ad_top"]))

    def test_nibble_fills_on_board_grind_approach(self):
        c = self._client()
        ev = c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "ANSEMUSDT|spot": {
                        "last_price": 0.17,
                        "reds": {"15m": 0},
                        "heat_breadth": 1,
                        "board": {
                            "grind": True,
                            "panic": False,
                            "names": 5,
                            "slow": 4,
                            "fast": 1,
                        },
                    }
                }
            },
        )
        ansem = next(p for p in ev.json()["plans"] if p["symbol"] == "ANSEMUSDT")
        self.assertTrue(ansem["live"])
        self.assertIn("nibble", ansem["decision"].lower())
        self.assertTrue(ansem.get("filled_entry"))
        self.assertAlmostEqual(float(ansem["filled_entry"]["usd"]), 10.0, places=1)
        self.assertAlmostEqual(float(ansem.get("allocated_usd") or 0), 10.0, places=1)
        acct = c.get("/api/machine/plans").json().get("account") or {}
        self.assertAlmostEqual(float(acct.get("allocated_usd") or 0), 10.0, places=1)
        self.assertEqual(int(ansem.get("remaining_layers") or 0), 0)

    def test_nibble_then_at_ad_still_fills(self):
        c = self._client()
        c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "ANSEMUSDT|spot": {
                        "last_price": 0.17,
                        "reds": {"15m": 0},
                        "heat_breadth": 1,
                        "board": {
                            "grind": True,
                            "panic": False,
                            "names": 5,
                            "slow": 4,
                            "fast": 1,
                        },
                    }
                }
            },
        )
        ev = c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "ANSEMUSDT|spot": {
                        "last_price": 0.145,
                        "reds": {"15m": 0},
                        "heat_breadth": 1,
                        "board": {
                            "grind": True,
                            "panic": False,
                            "names": 5,
                            "slow": 4,
                            "fast": 1,
                        },
                    }
                }
            },
        )
        ansem = next(p for p in ev.json()["plans"] if p["symbol"] == "ANSEMUSDT")
        self.assertTrue(ansem["live"])
        self.assertIn("at this chart's AD", ansem["decision"])
        self.assertGreater(float(ansem.get("allocated_usd") or 0), 10.0)
        filled = [L for L in (ansem.get("layers") or []) if L.get("status") == "filled"]
        self.assertGreaterEqual(len(filled), 1)

    def test_nibble_shows_on_ladder_and_recut_does_not_rearm(self):
        c = self._client()
        ev = c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "ANSEMUSDT|spot": {
                        "last_price": 0.17,
                        "reds": {"15m": 0},
                        "heat_breadth": 1,
                        "board": {
                            "grind": True,
                            "panic": False,
                            "names": 5,
                            "slow": 4,
                            "fast": 1,
                        },
                    }
                }
            },
        )
        ansem = next(p for p in ev.json()["plans"] if p["symbol"] == "ANSEMUSDT")
        nibble = next(
            (L for L in (ansem.get("layers") or []) if L.get("band") == "nibble"),
            None,
        )
        self.assertIsNotNone(nibble)
        self.assertEqual(nibble.get("status"), "filled")
        self.assertFalse(any(L.get("next") for L in (ansem.get("layers") or [])))
        rec = c.post(
            f"/api/machine/plans/{ansem['id']}/recut",
            json={"ad_top": 0.356, "ad_bottom": 0.145},
        )
        self.assertEqual(rec.status_code, 200, rec.text)
        after = rec.json()["plan"]
        self.assertAlmostEqual(float(after.get("allocated_usd") or 0), 10.0, places=1)
        self.assertEqual(int(after.get("remaining_layers") or 0), 0)

    def test_live_plan_does_not_nibble(self):
        c = self._client()
        c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "ANSEMUSDT|spot": {
                        "last_price": 0.145,
                        "reds": {"15m": 3},
                        "heat_breadth": 1,
                    }
                }
            },
        )
        ev = c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "ANSEMUSDT|spot": {
                        "last_price": 0.17,
                        "reds": {"15m": 0},
                        "heat_breadth": 1,
                        "board": {
                            "grind": True,
                            "panic": False,
                            "names": 5,
                            "slow": 4,
                            "fast": 1,
                        },
                    }
                }
            },
        )
        ansem = next(p for p in ev.json()["plans"] if p["symbol"] == "ANSEMUSDT")
        self.assertTrue(ansem["live"])
        self.assertNotIn("nibble", ansem["decision"].lower())
        self.assertGreater(float(ansem.get("allocated_usd") or 0), 10.0)

    def test_post_layers_writes_dump_depth_on_closed(self):
        c = self._client()
        plans = c.get("/api/machine/plans").json()["plans"]
        ansem = next(p for p in plans if p["symbol"] == "ANSEMUSDT")
        c.post(f"/api/machine/plans/{ansem['id']}/kill")
        post = c.post(
            f"/api/machine/plans/{ansem['id']}/layers",
            json={"ad_top": 0.00009, "ad_bottom": 0.00001942},
        )
        self.assertEqual(post.status_code, 200, post.text)
        layers = post.json()["layers"]
        ad = [L for L in layers if L.get("band") == "ad"]
        self.assertAlmostEqual(float(ad[0]["price"]), 0.00002399, places=7)
        self.assertAlmostEqual(float(ad[4]["price"]), 0.00001886, places=7)
        self.assertNotAlmostEqual(float(ad[0]["price"]), 0.00007588, places=8)

    def test_faster_tf_is_not_12h_on_1d(self):
        from mexc_bot.machine.facts import faster_tf_for

        self.assertEqual(faster_tf_for("1d"), "15m")
        self.assertNotEqual(faster_tf_for("1d"), "12h")
        self.assertEqual(faster_tf_for("4h"), "15m")
        self.assertEqual(faster_tf_for("15m"), "1m")

    def test_volume_n_prefers_quote_dollars(self):
        from mexc_bot.machine.hang import official_volume_n

        n = official_volume_n(
            [{"v": 1_000_000_000, "c": 2e-5, "q": 4321.0}]
        )
        self.assertEqual(n, 4321.0)

    def test_killed_under_ad_is_not_wait(self):
        c = self._client()
        plans = c.get("/api/machine/plans").json()["plans"]
        axti = next(p for p in plans if "AXTI" in p["symbol"])
        c.post(f"/api/machine/plans/{axti['id']}/kill")
        ev = c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "AXTISTOCK_USDT|futures": {
                        "last_price": 60.0,
                        "reds": {"4h": 3},
                        "heat_breadth": 1,
                    }
                }
            },
        )
        row = next(p for p in ev.json()["plans"] if "AXTI" in p["symbol"])
        self.assertEqual(row["status"], "killed")
        self.assertIn("killed", row["decision"].lower())
        self.assertNotEqual(row.get("decision_reason"), "wait")

    def test_pull_pack_does_not_log_every_poll(self):
        c = self._client()
        first = c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "ANSEMUSDT|spot": {
                        "last_price": 0.145,
                        "reds": {"1h": 3},
                        "heat_breadth": 1,
                    }
                }
            },
        )
        ansem = next(p for p in first.json()["plans"] if p["symbol"] == "ANSEMUSDT")
        snap = {
            "ANSEMUSDT|spot": {
                "last_price": 0.12,
                "reds": {"1h": 3},
                "heat_breadth": 1,
                "quiet_grind": True,
            }
        }
        c.post("/api/machine/evaluate", json={"snapshot": snap})
        c.post("/api/machine/evaluate", json={"snapshot": snap})
        log = c.get("/api/machine/plans").json()["log"]
        pulls = [r for r in log if r.get("action") == "pull-pack"]
        self.assertEqual(len(pulls), 0, pulls)

    def test_one_minute_reds_are_not_play_sitout(self):
        from mexc_bot.machine.facts import facts_from

        facts = facts_from(
            {
                "tf": "1d",
                "ad_status": "known",
                "ad_top": 0.356,
                "ad_bottom": 0.145,
                "status": "watch",
                "live": False,
            },
            {"last_price": 0.22, "reds": {"1m": 1}},
        )
        self.assertFalse(facts.get("first_or_second_red"))

    def test_agi_sit_off_buy_line_is_cleared_from_tape(self):
        from mexc_bot.machine.engine import public_tape_rows, purge_sit_not_at_line
        from mexc_bot.machine.store import MachineStore
        from mexc_bot.machine.hang import manila_label
        import time as _t

        store = MachineStore(self.db)
        uid = 8630949601
        c = self._client()
        plans = c.get("/api/machine/plans").json()["plans"]
        ansem = next(p for p in plans if p["symbol"] == "ANSEMUSDT")
        now = _t.time()
        store.insert_log(
            uid,
            {
                "plan_id": ansem["id"],
                "ts": now,
                "manila": manila_label(now),
                "symbol": "ANSEMUSDT",
                "tf": "1d",
                "last_price": 0.22,
                "action": "sit-out",
                "why": "First or second red on this TF, sit out on a normal dump. (path.sit_reds)",
            },
        )
        n = purge_sit_not_at_line(store, uid)
        self.assertGreaterEqual(n, 1)
        tape = public_tape_rows(store, uid, limit=40)
        self.assertFalse(
            any(
                r.get("action") == "sit-out" and r.get("symbol") == "ANSEMUSDT"
                for r in tape
            )
        )

    def test_fast_dump_far_from_ad_does_not_occupy_live_slot(self):
        c = self._client()
        ev = c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "ANSEMUSDT|spot": {
                        "last_price": 0.30,
                        "reds": {"1h": 4},
                        "heat_breadth": 1,
                        "fast_dump_volume": True,
                        "vol_spike": True,
                    }
                }
            },
        )
        ansem = next(p for p in ev.json()["plans"] if p["symbol"] == "ANSEMUSDT")
        self.assertFalse(ansem["live"])
        self.assertFalse(ansem.get("filled_entry"))
        self.assertFalse(ansem.get("intended_entry"))
        log = c.get("/api/machine/plans").json()["log"]
        self.assertFalse(
            any(
                str(r.get("action") or "") == "paper-buy"
                and str(r.get("symbol") or "") == "ANSEMUSDT"
                for r in log
            )
        )

    def test_unreached_buy_rows_are_deleted(self):
        from mexc_bot.machine.engine import public_tape_rows, purge_unreached_buys
        from mexc_bot.machine.store import MachineStore
        from mexc_bot.machine.hang import manila_label
        import time as _t

        store = MachineStore(self.db)
        uid = 8630949601
        c = self._client()
        plans = c.get("/api/machine/plans").json()["plans"]
        ansem = next(p for p in plans if p["symbol"] == "ANSEMUSDT")
        now = _t.time()
        for _ in range(8):
            store.insert_log(
                uid,
                {
                    "plan_id": ansem["id"],
                    "ts": now,
                    "manila": manila_label(now),
                    "symbol": "ANSEMUSDT",
                    "action": "paper-buy",
                    "last_price": 0.00596,
                    "intended_price": 0.004404,
                    "filled_price": 0.004404,
                    "size_pct": 5,
                    "why": "ghost unreached",
                },
            )
        n = purge_unreached_buys(store, uid)
        self.assertGreaterEqual(n, 8)
        tape = public_tape_rows(store, uid, limit=40)
        self.assertFalse(
            any(
                r.get("action") == "paper-buy"
                and r.get("intended_price") == 0.004404
                for r in tape
            )
        )

    def test_met_stays_met_after_bounce_above_band(self):
        c = self._client()
        c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "ANSEMUSDT|spot": {
                        "last_price": 0.145,
                        "reds": {"1h": 3},
                        "heat_breadth": 1,
                    }
                }
            },
        )
        bounce = c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "ANSEMUSDT|spot": {
                        "last_price": 0.30,
                        "reds": {"1h": 0},
                        "heat_breadth": 1,
                    }
                }
            },
        )
        ansem = next(p for p in bounce.json()["plans"] if p["symbol"] == "ANSEMUSDT")
        self.assertTrue(ansem.get("met"))

    def test_kline_low_in_band_sets_met(self):
        c = self._client()
        ev = c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "ANSEMUSDT|spot": {
                        "last_price": 0.30,
                        "reds": {"1h": 0},
                        "bars": [{"l": 0.145, "c": 0.20, "o": 0.21, "v": 1, "q": 10}],
                    }
                }
            },
        )
        ansem = next(p for p in ev.json()["plans"] if p["symbol"] == "ANSEMUSDT")
        self.assertTrue(ansem.get("met"))

    def test_tape_omits_wait_and_sitout_far_from_ad(self):
        c = self._client()
        c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "ANSEMUSDT|spot": {
                        "last_price": 0.30,
                        "reds": {"1h": 1},
                        "heat_breadth": 1,
                    }
                }
            },
        )
        log = c.get("/api/machine/plans").json()["log"]
        self.assertFalse(any(str(r.get("action") or "") == "wait" for r in log))
        self.assertFalse(
            any(
                str(r.get("action") or "") == "sit-out"
                and str(r.get("symbol") or "") == "ANSEMUSDT"
                for r in log
            )
        )

    def test_seconds_dump_through_hung_layer(self):
        import time as _t
        from mexc_bot.machine.tape import hung_seconds_dump, _hung_last

        _hung_last["X"] = 0.16
        now = _t.time()
        dump = hung_seconds_dump(
            "X",
            0.14,
            [{"band": "ad", "price": 0.15}],
            [
                {"ts": now - 1, "quote": 8000, "price": 0.14},
                {"ts": now - 20, "quote": 100, "price": 0.16},
            ],
            now=now,
        )
        self.assertTrue(dump["through_layer"])
        self.assertTrue(dump["spike"])
        self.assertTrue(dump["fast_dump"])
        _hung_last.pop("Y", None)
        dump2 = hung_seconds_dump(
            "Y",
            0.14,
            [{"band": "ad", "price": 0.15}],
            [
                {"ts": now - 1, "quote": 8000, "price": 0.14},
                {"ts": now - 2, "quote": 100, "price": 0.16},
            ],
            now=now,
        )
        self.assertTrue(dump2["through_layer"])
        self.assertTrue(dump2["fast_dump"])

    def test_board_flip_logs_once(self):
        from mexc_bot.machine import engine as eng
        from mexc_bot.machine.store import MachineStore

        eng._board_prev = {}
        store = MachineStore(self.db)
        uid = 8630949601
        eng.log_board_flip(store, uid, {"grind": False, "panic": False, "names": 40})
        eng.log_board_flip(store, uid, {"grind": True, "panic": False, "names": 40})
        eng.log_board_flip(store, uid, {"grind": True, "panic": False, "names": 40})
        eng.log_board_flip(store, uid, {"grind": True, "panic": False, "names": 40})
        rows = store.list_log(uid, limit=20)
        ons = [r for r in rows if r.get("action") == "grind-on"]
        self.assertEqual(len(ons), 1)

    def test_owner_fill_row_has_intended_filled_size(self):
        c = self._client()
        ev = c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "ANSEMUSDT|spot": {
                        "last_price": 0.145,
                        "reds": {"1h": 3},
                        "heat_breadth": 1,
                    }
                }
            },
        )
        self.assertTrue(
            any(p["symbol"] == "ANSEMUSDT" and p["live"] for p in ev.json()["plans"])
        )
        log = c.get("/api/machine/plans").json()["log"]
        buys = [r for r in log if r.get("action") == "paper-buy" and r.get("symbol") == "ANSEMUSDT"]
        self.assertTrue(buys)
        row = buys[0]
        self.assertIsNotNone(row.get("intended_price"))
        self.assertIsNotNone(row.get("filled_price"))
        self.assertAlmostEqual(float(row["filled_price"]), 0.145, places=5)
        self.assertIsNotNone(row.get("size_pct"))
        sold = c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "ANSEMUSDT|spot": {
                        "last_price": 0.18,
                        "reds": {"1h": 0},
                        "bounced": True,
                        "bounce_strong": True,
                    }
                }
            },
        )
        slog = c.get("/api/machine/plans").json()["log"]
        sells = [
            r
            for r in slog
            if r.get("action") == "paper-sell" and r.get("symbol") == "ANSEMUSDT"
        ]
        self.assertTrue(sells, sold.json())
        srow = sells[0]
        self.assertIsNotNone(srow.get("intended_price"))
        self.assertIsNotNone(srow.get("filled_price"))
        self.assertIsNotNone(srow.get("size_pct"))
        self.assertIsNotNone(srow.get("money_pnl"))
        self.assertNotEqual(float(srow["filled_price"]), 0.0)
        self.assertGreater(float(srow["money_pnl"]), 0.0)

    def test_ghost_same_why_does_not_block_real_fill_on_evaluate(self):
        from mexc_bot.machine.store import MachineStore
        from mexc_bot.machine.hang import manila_label
        import time as _t

        store = MachineStore(self.db)
        uid = 8630949601
        c = self._client()
        plans = c.get("/api/machine/plans").json()["plans"]
        ansem = next(p for p in plans if p["symbol"] == "ANSEMUSDT")
        now = _t.time()
        why = "Plan written and last at this chart's AD, taking the at-AD layer. (atad.take)"
        store.insert_log(
            uid,
            {
                "plan_id": ansem["id"],
                "ts": now,
                "manila": manila_label(now),
                "symbol": "ANSEMUSDT",
                "action": "paper-buy",
                "why": why,
                "filled_price": None,
            },
        )
        c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "ANSEMUSDT|spot": {
                        "last_price": 0.145,
                        "reds": {"1h": 3},
                        "heat_breadth": 1,
                    }
                }
            },
        )
        rows = store.list_log(uid, plan_id=int(ansem["id"]), actions=("paper-buy",), limit=20)
        complete = [r for r in rows if r.get("filled_price") is not None]
        self.assertTrue(complete)
        self.assertIsNotNone(complete[0].get("intended_price"))
        self.assertIsNotNone(complete[0].get("size_pct"))

    def test_hung_poll_does_not_need_1m_bars_for_dump(self):
        from mexc_bot.machine.tape import snapshot_for_plan

        plan = {
            "symbol": "ANSEMUSDT",
            "market": "spot",
            "tf": "1d",
            "ad_status": "known",
            "ad_top": 0.356,
            "ad_bottom": 0.145,
            "live": True,
            "layers_json": '[{"band":"ad","price":0.15,"idx":1}]',
        }
        import time as _t
        from mexc_bot.machine.tape import _hung_last

        _hung_last["ANSEMUSDT"] = 0.16
        now = _t.time()
        snap = snapshot_for_plan(
            plan,
            ticker=0.14,
            trades=[
                {"ts": now - 0.5, "quote": 9000, "price": 0.14},
                {"ts": now - 2, "quote": 80, "price": 0.16},
            ],
        )
        self.assertTrue(snap.get("trade_dump") or snap.get("fast_dump_volume"))
        self.assertAlmostEqual(float(snap["last_price"]), 0.14)
        self.assertEqual(snap.get("faster_tf"), "trades")
        self.assertAlmostEqual(float(snap.get("vol_usd_fast") or 0), 9080.0)
        self.assertNotIn("1m", snap.get("reds") or {})

    def test_hung_last_is_print_not_15m_close(self):
        from mexc_bot.machine.tape import snapshot_for_plan
        import time as _t

        plan = {
            "symbol": "ASTEROIDUSDT",
            "market": "spot",
            "tf": "1d",
            "ad_status": "known",
            "ad_top": 0.00003,
            "ad_bottom": 0.000019,
        }
        now = _t.time()
        bars_15m = [{"o": 0.03, "c": 0.03, "h": 0.03, "l": 0.029, "q": 10}]
        bars_1m = [{"o": 0.021, "c": 0.021, "h": 0.022, "l": 0.020, "q": 4}]
        snap = snapshot_for_plan(
            plan,
            ticker=0.0205,
            bars=bars_15m,
            bars_1m=bars_1m,
            trades=[{"ts": now, "price": 0.00002217, "quote": 12}],
        )
        self.assertAlmostEqual(float(snap["last_price"]), 0.00002217)
        self.assertEqual(snap.get("faster_tf"), "trades")
        self.assertAlmostEqual(float(snap.get("vol_usd_fast") or 0), 12.0)
        self.assertNotIn("1m", snap.get("reds") or {})
        snap2 = snapshot_for_plan(
            plan, ticker=None, bars=bars_15m, bars_1m=bars_1m, trades=[]
        )
        self.assertAlmostEqual(float(snap2["last_price"]), 0.021)
        snap3 = snapshot_for_plan(
            plan, ticker=None, bars=bars_15m, bars_1m=[], trades=[]
        )
        self.assertIsNone(snap3.get("last_price"))

    def test_asteroid_close_pnl_is_nine_cents_not_zero(self):
        from mexc_bot.machine.engine import (
            _close_fill_pnl,
            paper_pnl,
            public_plan,
            recompute_closed_money,
        )
        from mexc_bot.machine.store import MachineStore

        store = MachineStore(self.db)
        uid = 8630949601
        c = self._client()
        plans = c.get("/api/machine/plans").json()["plans"]
        row = next(p for p in plans if p["symbol"] == "ANSEMUSDT")
        pid = int(row["id"])
        store.insert_order(
            uid,
            pid,
            layer_idx=0,
            price=0.00002213,
            usd=51.6129,
            status="filled",
            side="buy",
            filled_price=0.00002213,
            size_pct=25.8,
            band="ad",
        )
        store.patch_plan(uid, pid, leftover_avg=0.00002213, live=True, status="live")
        store.insert_order(
            uid,
            pid,
            layer_idx=0,
            price=0.00002213,
            usd=51.6129,
            status="filled",
            side="sell",
            filled_price=0.00002217,
            size_pct=25.8,
            band="exit",
        )
        store.insert_close(
            uid,
            {
                "plan_id": pid,
                "symbol": "ANSEMUSDT",
                "market": "spot",
                "reason": "bounce",
                "bounce_or_fail": "bounce",
                "process_ok": True,
                "money_pnl": 0.0,
            },
        )
        store.patch_plan(uid, pid, live=False, status="closed", leftover_avg=0.00002213)
        tick = _close_fill_pnl(
            [{"filled_price": 0.00002217, "price": 0.00002217, "usd": 51.6129}],
            0.00002213,
        )
        self.assertGreater(tick, 0.05)
        self.assertLess(tick, 0.15)
        mark = paper_pnl(store, uid, pid, leftover_avg=0.00002213)
        self.assertGreater(mark, 0.05)
        self.assertLess(mark, 0.15)
        n = recompute_closed_money(store, uid)
        self.assertGreaterEqual(n, 1)
        pub = public_plan(store, store.get_plan(uid, pid))
        self.assertGreater(float(pub.get("money_pnl") or 0), 0.05)
        self.assertLess(float(pub["money_pnl"]), 0.15)
        closes = store.list_closes(uid)
        ast = next(x for x in closes if int(x.get("plan_id") or 0) == pid)
        self.assertGreater(float(ast["money_pnl"]), 0.05)

    def test_hung_poll_fetches_1m_not_15m_faster(self):
        from mexc_bot.machine import loop as lp
        from mexc_bot.machine.engine import seed_plans
        from mexc_bot.machine.store import MachineStore

        called = []

        def fake_klines(market, symbol, tf, client=None):
            called.append(str(tf))
            return [{"o": 0.03, "c": 0.021, "h": 0.03, "l": 0.02, "q": 8}]

        def fake_ticker(*_a, **_k):
            return 0.0205

        def fake_trades(*_a, **_k):
            return [{"ts": 1.0, "price": 0.00002217, "quote": 20}]

        orig = (
            lp.fetch_official_klines,
            lp.fetch_official_ticker,
            lp.fetch_recent_trades,
        )
        lp.fetch_official_klines = fake_klines
        lp.fetch_official_ticker = fake_ticker
        lp.fetch_recent_trades = fake_trades
        try:
            store = MachineStore(self.db)
            uid = 8630949601
            seed_plans(store, uid)
            for p in store.list_plans(uid):
                if p["symbol"] != "ANSEMUSDT":
                    continue
                store.patch_plan(
                    uid,
                    int(p["id"]),
                    tf="1d",
                    ad_status="known",
                    ad_top=0.356,
                    ad_bottom=0.145,
                )
            out = lp.poll_once(store=store, user_id=uid, fetch_klines=True)
        finally:
            lp.fetch_official_klines, lp.fetch_official_ticker, lp.fetch_recent_trades = orig
        self.assertIn("1m", called)
        self.assertIn("1d", called)
        ansem = next(p for p in out["plans"] if p["symbol"] == "ANSEMUSDT")
        self.assertAlmostEqual(float(ansem["last_price"]), 0.00002217)
        gate = ansem.get("gate") or {}
        self.assertEqual(gate.get("faster_tf"), "trades")
        self.assertNotIn(gate.get("faster_tf"), ("15m", "5m", "1m"))

    def test_machine_trades_json_not_html(self):
        from mexc_bot.machine import tape as tp

        orig = tp.fetch_recent_trades
        tp.fetch_recent_trades = lambda *a, **k: [
            {
                "ts": 1_700_000_013.0,
                "price": 0.00002217,
                "qty": 1000.0,
                "quote": 0.02217,
            }
        ]
        try:
            c = self._client()
            missing = c.get("/api/machine/trades")
            self.assertEqual(missing.status_code, 400)
            r = c.get("/api/machine/trades?symbol=ASTEROID")
        finally:
            tp.fetch_recent_trades = orig
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("application/json", r.headers.get("content-type", ""))
        self.assertNotIn("<html", r.text.lower())
        body = r.json()
        self.assertTrue(body.get("ok"))
        self.assertFalse(body.get("live_orders_sent"))
        self.assertFalse(body.get("live_orders_allowed"))
        self.assertTrue(body.get("trades"))
        row = body["trades"][0]
        self.assertIn("PHT", str(row.get("manila") or ""))
        self.assertAlmostEqual(float(row["price"]), 0.00002217)
        self.assertAlmostEqual(float(row["qty"]), 1000.0)
        self.assertAlmostEqual(float(row["quote"]), 0.02217)

    def test_1d_print_dump_buys_layer_not_waiting_on_play_tf(self):
        c = self._client()
        import time as _t

        now = _t.time()
        ev = c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "ANSEMUSDT|spot": {
                        "last_price": 0.16,
                        "print_low": 0.14,
                        "reds": {"1d": 1},
                        "faster_tf": "trades",
                        "trade_dump": True,
                        "fast_dump_volume": True,
                        "vol_usd_fast": 9000,
                        "vol_spike": True,
                    }
                },
                "now": now,
            },
        )
        self.assertEqual(ev.status_code, 200, ev.text)
        ansem = next(p for p in ev.json()["plans"] if p["symbol"] == "ANSEMUSDT")
        gate = ansem.get("gate") or {}
        self.assertEqual(gate.get("faster_tf"), "trades")
        self.assertNotIn(gate.get("faster_tf"), ("15m", "5m", "1m"))
        self.assertTrue(ansem.get("live") or ansem.get("filled_entry"))
        self.assertFalse(ev.json().get("live_orders_sent"))
        self.assertFalse(ev.json().get("live_orders_allowed"))
        log = c.get("/api/machine/plans").json()["log"]
        buys = [
            r
            for r in log
            if r.get("symbol") == "ANSEMUSDT" and r.get("action") == "paper-buy"
        ]
        self.assertTrue(buys, log)

    def test_same_add_why_logs_once(self):
        c = self._client()
        c.post(
            "/api/machine/evaluate",
            json={
                "snapshot": {
                    "ANSEMUSDT|spot": {
                        "last_price": 0.145,
                        "reds": {"1h": 3},
                        "heat_breadth": 1,
                    }
                }
            },
        )
        snap = {
            "ANSEMUSDT|spot": {
                "last_price": 0.10,
                "reds": {"1h": 4},
                "heat_breadth": 1,
                "fast_dump_volume": True,
                "vol_spike": True,
            }
        }
        c.post("/api/machine/evaluate", json={"snapshot": snap})
        c.post("/api/machine/evaluate", json={"snapshot": snap})
        log = c.get("/api/machine/plans").json()["log"]
        adds = [
            r
            for r in log
            if r.get("action") == "add-panic" and r.get("symbol") == "ANSEMUSDT"
        ]
        self.assertLessEqual(len(adds), 1)


if __name__ == "__main__":
    unittest.main()
