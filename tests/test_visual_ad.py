"""Learning-only visual AD: storage, preserve-on-refreeze, path safety."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mexc_bot.learning.cases import case_public_view, freeze_case
from mexc_bot.learning.store import EventStore
from mexc_bot.learning.visual_ad import (
    extract_visual_ad,
    merge_visual_ad_into_features,
    resolve_visual_ad_image,
    safe_image_basename,
    sanitize_visual_ad,
    visual_ad_dir,
)


class TestVisualAdSanitize(unittest.TestCase):
    def test_old_features_extract_null(self):
        self.assertIsNone(extract_visual_ad({"ok": True, "ad_zone": "at_ad"}))
        self.assertIsNone(extract_visual_ad({}))
        self.assertIsNone(extract_visual_ad(None))

    def test_sanitize_requires_useful_field(self):
        with self.assertRaises(ValueError):
            sanitize_visual_ad({})
        with self.assertRaises(ValueError):
            sanitize_visual_ad({"source": "staff", "ts": 1.0})

    def test_sanitize_keeps_optional_fields(self):
        out = sanitize_visual_ad(
            {
                "tf": "15m",
                "high": 1.25,
                "low": 1.01,
                "note": "staff marked",
                "source": "staff",
                "ts": 1700000000.0,
            }
        )
        self.assertEqual(out["tf"], "15m")
        self.assertEqual(out["high"], 1.25)
        self.assertEqual(out["low"], 1.01)
        self.assertEqual(out["note"], "staff marked")

    def test_path_traversal_rejected(self):
        bad = (
            "../secret.png",
            "../../etc/passwd.png",
            "/etc/passwd.png",
            "foo/../../../etc/x.png",
            "..",
            "foo/bar.png",
            "\\windows\\x.png",
            "~/x.png",
            "ok..sneak.png",
        )
        for rel in bad:
            with self.subTest(rel=rel):
                with self.assertRaises(ValueError):
                    safe_image_basename(rel)
                with self.assertRaises(ValueError):
                    sanitize_visual_ad({"image_relpath": rel})

    def test_basename_ok(self):
        self.assertEqual(safe_image_basename("case12.png"), "case12.png")
        out = sanitize_visual_ad({"image_relpath": "case12.png"})
        self.assertEqual(out["image_relpath"], "case12.png")

    def test_resolve_stays_in_visual_ad_dir(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = Path(tmp.name) / "alerts.db"
        dest = resolve_visual_ad_image(db, "shot.webp")
        base = visual_ad_dir(db).resolve()
        self.assertEqual(dest.parent, base)
        self.assertEqual(dest.name, "shot.webp")
        dest.relative_to(base)


class TestVisualAdStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = EventStore(Path(self.tmp.name) / "c.db")
        self.uid = 1

    def tearDown(self):
        self.tmp.cleanup()

    def _freeze(self, eid, feats, **kwargs):
        with patch(
            "mexc_bot.learning.cases.build_features_for_event",
            return_value=feats,
        ):
            return freeze_case(
                self.store,
                self.uid,
                symbol="FOO_USDT",
                market="futures",
                event_id=eid,
                fire_ts=time.time(),
                fire_price=1.0,
                ref_price=1.2,
                drop_pct=-16.6,
                velocity_band="PANIC",
                **kwargs,
            )

    def test_old_case_public_view_visual_ad_null(self):
        eid = self.store.log_event(
            self.uid, "mover_peak", "FOO_USDT", "futures", price=1.0, drop_pct=-10
        )
        view = self._freeze(
            eid,
            {
                "ok": True,
                "band": "PANIC",
                "dd_pct": 10.0,
                "ad_zone": "at_ad",
                "vol_ratio": 1.8,
            },
            chips=["plan_ok", "ad_met"],
            note="classic case",
            lesson_id=7,
            source="teach",
        )
        self.assertIsNone(view.get("visual_ad"))
        self.assertEqual(view.get("ad_zone"), "at_ad")
        self.assertEqual(view.get("note"), "classic case")
        self.assertEqual(view.get("lesson_id"), 7)
        self.assertIn("plan_ok", view.get("chips") or [])
        self.assertEqual(view.get("dd_pct"), 10.0)
        row = self.store.get_setup_case(self.uid, event_id=eid)
        pub = case_public_view(row)
        self.assertIsNone(pub.get("visual_ad"))
        self.assertEqual(pub.get("ad_zone"), "at_ad")
        self.assertEqual(pub.get("note"), "classic case")
        self.assertEqual(pub.get("lesson_id"), 7)

    def test_merge_does_not_clobber_chips_note_lesson_or_formula(self):
        eid = self.store.log_event(
            self.uid, "mover_peak", "FOO_USDT", "futures", price=1.0, drop_pct=-10
        )
        view = self._freeze(
            eid,
            {
                "ok": True,
                "band": "PANIC",
                "dd_pct": 12.5,
                "ad_zone": "extension",
                "vol_ratio": 2.2,
                "vol_flag": "surge",
            },
            chips=["ad_missed", "hesitant"],
            note="never hit zone",
            lesson_id=9,
            source="teach",
        )
        cid = view["id"]
        row = self.store.merge_visual_ad(
            self.uid,
            cid,
            {"tf": "15m", "high": 1.4, "low": 1.05, "note": "visual from Reed"},
        )
        self.assertIsNotNone(row)
        self.assertEqual(row.get("chips_json"), json.dumps(["ad_missed", "hesitant"]))
        self.assertEqual(row.get("note"), "never hit zone")
        self.assertEqual(row.get("lesson_id"), 9)
        feats = json.loads(row["features_json"])
        self.assertEqual(feats["ad_zone"], "extension")
        self.assertEqual(feats["dd_pct"], 12.5)
        self.assertEqual(feats["vol_ratio"], 2.2)
        self.assertEqual(feats["vol_flag"], "surge")
        self.assertEqual(feats["visual_ad"]["tf"], "15m")
        self.assertEqual(feats["visual_ad"]["high"], 1.4)
        pub = case_public_view(row)
        self.assertEqual(pub["visual_ad"]["low"], 1.05)
        self.assertEqual(pub["chips"], ["ad_missed", "hesitant"])
        self.assertEqual(pub["note"], "never hit zone")
        self.assertEqual(pub["lesson_id"], 9)
        self.assertEqual(pub["ad_zone"], "extension")

    def test_refreeze_keeps_visual_ad(self):
        eid = self.store.log_event(
            self.uid, "mover_peak", "FOO_USDT", "futures", price=1.0, drop_pct=-10
        )
        first = self._freeze(
            eid,
            {"ok": True, "band": "PANIC", "dd_pct": 8.0, "ad_zone": "approach"},
            chips=["plan_ok"],
            note="first freeze",
            source="teach",
        )
        self.store.merge_visual_ad(
            self.uid,
            first["id"],
            {"tf": "5m", "high": 2.0, "low": 1.5, "note": "keep me"},
        )
        second = self._freeze(
            eid,
            {
                "ok": True,
                "band": "FAST",
                "dd_pct": 11.0,
                "ad_zone": "at_ad",
                "vol_ratio": 1.1,
            },
            source="fire",
        )
        self.assertEqual(first["id"], second["id"])
        vad = second.get("visual_ad") or {}
        self.assertEqual(vad.get("tf"), "5m")
        self.assertEqual(vad.get("high"), 2.0)
        self.assertEqual(vad.get("low"), 1.5)
        self.assertEqual(vad.get("note"), "keep me")
        self.assertEqual(second.get("ad_zone"), "at_ad")
        self.assertEqual(second.get("dd_pct"), 11.0)
        row = self.store.get_setup_case(self.uid, event_id=eid)
        feats = json.loads(row["features_json"])
        self.assertEqual(feats["visual_ad"]["tf"], "5m")
        self.assertEqual(feats["ad_zone"], "at_ad")
        # chips/note from first teach stay unless new values passed
        self.assertEqual(row.get("note"), "first freeze")

    def test_merge_visual_ad_only_updates_visual_ad_key(self):
        feats = {
            "ok": True,
            "ad_zone": "at_ad",
            "dd_pct": 9.0,
            "vol_ratio": 1.4,
        }
        out = merge_visual_ad_into_features(
            feats, {"tf": "1h", "high": 3.0, "low": 2.2}
        )
        self.assertEqual(out["ok"], True)
        self.assertEqual(out["ad_zone"], "at_ad")
        self.assertEqual(out["dd_pct"], 9.0)
        self.assertEqual(out["vol_ratio"], 1.4)
        self.assertEqual(out["visual_ad"]["tf"], "1h")
        self.assertEqual(set(feats.keys()), {"ok", "ad_zone", "dd_pct", "vol_ratio"})

    def test_merge_rejects_traversal_and_leaves_row(self):
        eid = self.store.log_event(
            self.uid, "mover_peak", "FOO_USDT", "futures", price=1.0
        )
        view = self._freeze(
            eid,
            {"ok": True, "ad_zone": "at_ad"},
            chips=["plan_ok"],
            note="safe",
            lesson_id=3,
        )
        with self.assertRaises(ValueError):
            self.store.merge_visual_ad(
                self.uid,
                view["id"],
                {"image_relpath": "../../etc/passwd.png"},
            )
        row = self.store.get_setup_case(self.uid, case_id=view["id"])
        self.assertEqual(row.get("note"), "safe")
        self.assertEqual(row.get("lesson_id"), 3)
        feats = json.loads(row["features_json"])
        self.assertNotIn("visual_ad", feats)


class TestVisualAdApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "desk.db"
        os.environ["ALERTS_FILE"] = str(self.db)
        os.environ["DESK_USER_ID"] = "8630949601"
        os.environ.pop("DESK_API_TOKEN", None)
        os.environ.pop("WEB_UI_TOKEN", None)
        self.uid = 8630949601
        self.store = EventStore(self.db)
        from fastapi.testclient import TestClient
        from mexc_bot.webapi.app import create_app

        self.client = TestClient(create_app())

    def tearDown(self):
        self.tmp.cleanup()

    def _case(self):
        eid = self.store.log_event(
            self.uid, "mover_peak", "BAR_USDT", "futures", price=2.0, drop_pct=-8
        )
        cid = self.store.upsert_setup_case(
            self.uid,
            symbol="BAR_USDT",
            market="futures",
            event_id=eid,
            fire_price=2.0,
            drop_pct=-8,
            features={"ok": True, "ad_zone": "at_ad", "dd_pct": 8.0},
            chips=["plan_ok"],
            note="api case",
            lesson_id=4,
            source="teach",
        )
        return cid, eid

    def test_post_and_preview_and_image(self):
        cid, eid = self._case()
        r = self.client.post(
            f"/api/learning/cases/{cid}/visual-ad",
            json={"tf": "15m", "high": 2.2, "low": 1.9, "note": "staff"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body.get("ok"))
        self.assertEqual(body["visual_ad"]["tf"], "15m")
        self.assertEqual(body["note"], "api case")
        self.assertEqual(body["lesson_id"], 4)
        self.assertEqual(body["chips"], ["plan_ok"])
        self.assertEqual(body["ad_zone"], "at_ad")

        prev = self.client.get(f"/api/learning/case-preview?event_id={eid}")
        self.assertEqual(prev.status_code, 200)
        snap = prev.json()
        self.assertEqual(snap["visual_ad"]["low"], 1.9)
        self.assertEqual(snap["ad_zone"], "at_ad")

        missing = self.client.get(f"/api/learning/cases/{cid}/visual-ad/image")
        self.assertEqual(missing.status_code, 404)

        vdir = visual_ad_dir(self.db)
        vdir.mkdir(parents=True, exist_ok=True)
        img = vdir / "reed.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"fake")
        r2 = self.client.post(
            f"/api/learning/cases/{cid}/visual-ad",
            json={"image_relpath": "reed.png"},
        )
        self.assertEqual(r2.status_code, 200, r2.text)
        got = self.client.get(f"/api/learning/cases/{cid}/visual-ad/image")
        self.assertEqual(got.status_code, 200)
        self.assertTrue(got.content.startswith(b"\x89PNG"))

    def test_post_traversal_rejected(self):
        cid, _ = self._case()
        r = self.client.post(
            f"/api/learning/cases/{cid}/visual-ad",
            json={"image_relpath": "../secret.png"},
        )
        self.assertEqual(r.status_code, 400)
        row = self.store.get_setup_case(self.uid, case_id=cid)
        feats = json.loads(row["features_json"])
        self.assertNotIn("visual_ad", feats)
        self.assertEqual(row.get("note"), "api case")

    def test_unknown_case_404(self):
        r = self.client.post(
            "/api/learning/cases/99999/visual-ad",
            json={"tf": "15m", "high": 1, "low": 0.5},
        )
        self.assertEqual(r.status_code, 404)


class TestDeskJsVisualAd(unittest.TestCase):
    def test_slot_is_optional_and_chart_post_unused(self):
        js = (ROOT / "mexc_bot/webapi/static/assets/desk.js").read_text()
        self.assertIn("visual_ad", js)
        self.assertIn("_visualAdSlotHtml", js)
        self.assertIn("renderCaseSnap", js)
        self.assertNotIn("/api/learning/chart", js)
        self.assertIn("/api/learning/incident-candles", js)
        self.assertIn("learnIncidentCanvas", js)
        self.assertIn("Click two prices", js)
        self.assertIn("/api/learning/case-preview", js)
        # Writer posts to the existing merge pocket only.
        self.assertIn("/api/learning/cases/", js)
        self.assertIn("/visual-ad", js)
        self.assertIn("_visualAdWriteHtml", js)
        self.assertIn("visualAdSave", js)
        # Live preview (no frozen id) must not pretend Save AD works.
        self.assertIn("Live preview has no frozen id", js)
        # Lesson save stays independent of a visual AD mark.
        self.assertIn('await api("/api/learning/teach"', js)
        teach = js.split('await api("/api/learning/teach"')[1].split("});")[0]
        self.assertNotIn("visual_ad", teach)
        self.assertNotIn("visual-ad", teach)
        self.assertNotIn("visualAd", teach)

    def test_write_html_gated_on_case_id(self):
        js = (ROOT / "mexc_bot/webapi/static/assets/desk.js").read_text()
        start = js.index("function _visualAdWriteHtml")
        end = js.index("function _visualAdPayloadFromForm")
        chunk = js[start:end]
        self.assertIn('if (!c || c.id == null || c.id === "") return ""', chunk)
        self.assertIn("Save AD", chunk)
        self.assertIn("visualAdTf", chunk)
        self.assertIn("visualAdHigh", chunk)
        self.assertIn("visualAdLow", chunk)
        self.assertIn("visualAdNote", chunk)

    def test_chart_html_shows_without_frozen_id(self):
        js = (ROOT / "mexc_bot/webapi/static/assets/desk.js").read_text()
        start = js.index("function _incidentChartCanShow")
        end = js.index("function _incidentChartHtml")
        chunk = js[start:end]
        self.assertIn("c.fire_ts || c.incident_ts", chunk)
        self.assertIn("sel.event_id", chunk)
        self.assertNotIn("c.id == null", chunk)

    def test_css_keeps_write_block_small(self):
        css = (ROOT / "mexc_bot/webapi/static/assets/desk.css").read_text()
        self.assertIn(".learn-visual-ad-write", css)
        self.assertIn(".learn-visual-ad-write-row", css)
        html = (ROOT / "mexc_bot/webapi/static/index.html").read_text()
        self.assertIn("desk.js?v=slicelab6", html)
        self.assertIn("desk.css?v=slicelab6", html)
        self.assertIn("learn-incident-chart", css)
        self.assertIn("learn-lesson-edit-ad", css)

    def test_lesson_edit_reuses_same_writer(self):
        js = (ROOT / "mexc_bot/webapi/static/assets/desk.js").read_text()
        self.assertIn("data-edit-ad", js)
        self.assertIn("fillLessonEditAd", js)
        self.assertIn("renderVisualAdTeachTools", js)
        self.assertIn("function _lessonCanShowAd", js)
        self.assertIn("case-preview?event_id=", js)
        can = js[js.index("function _lessonCanShowAd") : js.index("function _lessonAdSel")]
        self.assertIn("l.case_id", can)
        self.assertIn("l.event_id", can)
        save = js.split('$$("[data-save-lesson]"')[1].split(
            '$$("[data-del-lesson]"'
        )[0]
        self.assertIn("/api/learning/lessons/", save)
        self.assertIn("text, behaviors", save)
        self.assertNotIn("visual_ad", save)
        self.assertNotIn("visual-ad", save)


FIRE_TS = 1_700_000_000.0
FAKE_BARS = [
    {
        "ts": FIRE_TS - 6 * 3600 + i * 900,
        "o": 2.0 + (i % 3) * 0.01,
        "h": 2.2,
        "l": 1.7,
        "c": 1.95,
        "v": 12.0,
    }
    for i in range(40)
]


class TestIncidentOhlc(unittest.TestCase):
    def test_normalize_tf_rejects_garbage(self):
        from mexc_bot.learning.incident_ohlc import normalize_tf

        self.assertEqual(normalize_tf("15m"), "15m")
        self.assertEqual(normalize_tf("1H"), "1h")
        self.assertEqual(normalize_tf("1D"), "1d")
        for bad in ("nope", "15min", "../etc", "1;drop", "", None, "hour"):
            with self.subTest(tf=bad):
                with self.assertRaises(ValueError):
                    normalize_tf(bad)

    def test_lookback_keeps_structure_on_htf(self):
        from mexc_bot.learning.incident_ohlc import lookback_seconds

        self.assertGreaterEqual(lookback_seconds("15m", 6 * 3600), 6 * 3600)
        self.assertGreaterEqual(lookback_seconds("1h", 6 * 3600), 32 * 3600)

    def test_response_omits_visual_ad(self):
        from mexc_bot.learning.incident_ohlc import _pack_response

        body = _pack_response(
            symbol="FOO_USDT",
            market="futures",
            tf="15m",
            fire_ts=FIRE_TS,
            fire_price=2.0,
            lookback=21600,
            bars=FAKE_BARS[:3],
            case_id=1,
            event_id=2,
        )
        self.assertNotIn("visual_ad", body)
        self.assertNotIn("ad_median", body)
        self.assertNotIn("ad_zone", body)
        self.assertFalse(body.get("live"))
        self.assertEqual(body["symbol"], "FOO_USDT")


class TestIncidentCandlesApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "desk.db"
        os.environ["ALERTS_FILE"] = str(self.db)
        os.environ["DESK_USER_ID"] = "8630949601"
        os.environ.pop("DESK_API_TOKEN", None)
        os.environ.pop("WEB_UI_TOKEN", None)
        self.uid = 8630949601
        self.store = EventStore(self.db)
        from fastapi.testclient import TestClient
        from mexc_bot.webapi.app import create_app

        self.client = TestClient(create_app())

    def tearDown(self):
        self.tmp.cleanup()

    def _case(self, symbol="BAR_USDT"):
        eid = self.store.log_event(
            self.uid,
            "mover_peak",
            symbol,
            "futures",
            ts=FIRE_TS,
            price=2.0,
            drop_pct=-8,
        )
        cid = self.store.upsert_setup_case(
            self.uid,
            symbol=symbol,
            market="futures",
            event_id=eid,
            fire_ts=FIRE_TS,
            fire_price=2.0,
            drop_pct=-8,
            features={
                "ok": True,
                "ad_zone": "at_ad",
                "dd_pct": 8.0,
                "ad_median": 6.5,
                "tf_hint": "15m",
                "incident": {
                    "ts": FIRE_TS,
                    "price": 2.0,
                    "chart_tfs": ["5m", "15m", "1h"],
                    "chart_lookback_seconds": 6 * 3600,
                },
            },
            chips=["plan_ok"],
            note="api case",
            lesson_id=4,
            source="teach",
        )
        return cid, eid

    def test_get_serves_case_symbol_only_and_skips_visual_ad(self):
        cid, eid = self._case("BAR_USDT")
        with patch(
            "mexc_bot.learning.incident_ohlc.fetch_incident_bars",
            return_value=FAKE_BARS,
        ) as fetch:
            r = self.client.get(
                f"/api/learning/incident-candles?case_id={cid}"
                f"&symbol=BTCUSDT&market=spot&tf=15m"
            )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body.get("ok"))
        self.assertEqual(body["symbol"], "BAR_USDT")
        self.assertEqual(body["market"], "futures")
        self.assertEqual(body["tf"], "15m")
        self.assertEqual(body["fire_ts"], FIRE_TS)
        self.assertEqual(body["event_id"], eid)
        self.assertEqual(len(body["bars"]), 40)
        self.assertEqual(body["bars"][0]["o"], FAKE_BARS[0]["o"])
        self.assertNotIn("visual_ad", body)
        self.assertNotIn("ad_median", body)
        self.assertFalse(body.get("live"))
        args, kwargs = fetch.call_args
        self.assertEqual(kwargs["symbol"], "BAR_USDT")
        self.assertEqual(kwargs["market"], "futures")
        self.assertEqual(kwargs["tf"], "15m")
        self.assertEqual(kwargs["fire_ts"], FIRE_TS)

    def test_garbage_tf_rejected(self):
        cid, _ = self._case()
        with patch(
            "mexc_bot.learning.incident_ohlc.fetch_incident_bars",
            return_value=FAKE_BARS,
        ):
            r = self.client.get(
                f"/api/learning/incident-candles?case_id={cid}&tf=nope"
            )
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("tf", (r.json().get("detail") or "").lower())

    def test_event_path_cannot_retarget_symbol(self):
        _, eid = self._case("BAR_USDT")
        with patch(
            "mexc_bot.learning.incident_ohlc.fetch_incident_bars",
            return_value=FAKE_BARS,
        ) as fetch:
            r = self.client.get(
                f"/api/learning/incident-candles?event_id={eid}&symbol=ETHUSDT"
            )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["symbol"], "BAR_USDT")
        self.assertNotIn("visual_ad", body)
        self.assertEqual(fetch.call_args.kwargs["symbol"], "BAR_USDT")

    def test_unknown_case_404(self):
        r = self.client.get("/api/learning/incident-candles?case_id=99999&tf=15m")
        self.assertEqual(r.status_code, 404)

    def test_missing_identity_400(self):
        r = self.client.get("/api/learning/incident-candles?tf=15m")
        self.assertEqual(r.status_code, 400)

    def test_save_ad_still_merges_only_visual_ad(self):
        cid, _ = self._case()
        r = self.client.post(
            f"/api/learning/cases/{cid}/visual-ad",
            json={"tf": "15m", "high": 2.2, "low": 1.9, "note": "staff"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["visual_ad"]["high"], 2.2)
        self.assertEqual(body["note"], "api case")
        self.assertEqual(body["lesson_id"], 4)
        self.assertEqual(body["chips"], ["plan_ok"])
        self.assertEqual(body["ad_zone"], "at_ad")
        row = self.store.get_setup_case(self.uid, case_id=cid)
        feats = json.loads(row["features_json"])
        self.assertEqual(feats["ad_zone"], "at_ad")
        self.assertEqual(feats["ad_median"], 6.5)
        self.assertEqual(feats["visual_ad"]["low"], 1.9)


class TestLessonEditVisualAdApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "desk.db"
        os.environ["ALERTS_FILE"] = str(self.db)
        os.environ["DESK_USER_ID"] = "8630949601"
        os.environ.pop("DESK_API_TOKEN", None)
        os.environ.pop("WEB_UI_TOKEN", None)
        self.uid = 8630949601
        self.store = EventStore(self.db)
        from fastapi.testclient import TestClient
        from mexc_bot.webapi.app import create_app

        self.client = TestClient(create_app())

    def tearDown(self):
        self.tmp.cleanup()

    def _fire_lesson(self, *, visual=None):
        eid = self.store.log_event(
            self.uid, "mover_peak", "FOO_USDT", "futures", price=1.5, drop_pct=-12
        )
        lid = self.store.teach_lesson(
            self.uid,
            "Took the first layer at AD",
            tags=[
                "sym:FOO_USDT",
                "mkt:futures",
                f"ev:{eid}",
                "plan_ok",
                "ad_met",
            ],
            evidence_event_ids=[eid],
        )
        feats = {
            "ok": True,
            "ad_zone": "at_ad",
            "dd_pct": 12.0,
            "tf_hint": "15m",
        }
        if visual:
            feats["visual_ad"] = visual
        cid = self.store.upsert_setup_case(
            self.uid,
            symbol="FOO_USDT",
            market="futures",
            event_id=eid,
            fire_ts=FIRE_TS,
            fire_price=1.5,
            drop_pct=-12,
            features=feats,
            chips=["plan_ok", "ad_met"],
            note="classic panic",
            lesson_id=lid,
            source="teach",
        )
        return lid, cid, eid

    def _lesson_from_home(self, lid):
        r = self.client.get("/api/learning")
        self.assertEqual(r.status_code, 200, r.text)
        rows = (r.json().get("lessons") or [])
        return next((x for x in rows if int(x.get("id") or 0) == int(lid)), None)

    def test_learning_get_stamps_case_id_and_prefill(self):
        lid, cid, eid = self._fire_lesson(
            visual={"tf": "15m", "high": 1.8, "low": 1.4, "note": "marked"}
        )
        row = self._lesson_from_home(lid)
        self.assertIsNotNone(row)
        self.assertEqual(row.get("event_id"), eid)
        self.assertEqual(row.get("case_id"), cid)
        self.assertEqual((row.get("visual_ad") or {}).get("tf"), "15m")
        self.assertEqual((row.get("case") or {}).get("id"), cid)
        self.assertEqual(row.get("text"), "Took the first layer at AD")
        tags = row.get("tags") or []
        self.assertIn("plan_ok", tags)
        self.assertIn("ad_met", tags)

    def test_plain_lesson_has_no_case_or_zone(self):
        lid = self.store.teach_lesson(
            self.uid, "Never chase a bounce", tags=["plan_ok"]
        )
        row = self._lesson_from_home(lid)
        self.assertIsNotNone(row)
        self.assertIsNone(row.get("case_id"))
        self.assertIsNone(row.get("event_id"))
        self.assertIsNone(row.get("visual_ad"))
        self.assertFalse(row.get("case"))

    def test_event_without_case_stays_ungated_for_save_ad(self):
        eid = self.store.log_event(
            self.uid, "mover_peak", "BAZ_USDT", "futures", price=3.0, drop_pct=-9
        )
        lid = self.store.teach_lesson(
            self.uid,
            "Need to mark AD later",
            tags=["sym:BAZ_USDT", "mkt:futures", f"ev:{eid}"],
            evidence_event_ids=[eid],
        )
        row = self._lesson_from_home(lid)
        self.assertIsNotNone(row)
        self.assertEqual(row.get("event_id"), eid)
        self.assertIsNone(row.get("case_id"))
        self.assertIsNone(row.get("visual_ad"))

    def test_post_visual_ad_does_not_clobber_lesson_or_chips(self):
        lid, cid, eid = self._fire_lesson()
        r = self.client.post(
            f"/api/learning/cases/{cid}/visual-ad",
            json={"tf": "5m", "high": 1.7, "low": 1.35, "note": "from edit"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["visual_ad"]["tf"], "5m")
        self.assertEqual(body["note"], "classic panic")
        self.assertEqual(body["lesson_id"], lid)
        self.assertEqual(body["chips"], ["plan_ok", "ad_met"])
        self.assertEqual(body["ad_zone"], "at_ad")

        lesson = self.store.get_lesson(self.uid, lid)
        self.assertEqual(lesson["text"], "Took the first layer at AD")
        tags = json.loads(lesson["tags_json"] or "[]")
        self.assertIn("plan_ok", tags)
        self.assertIn("ad_met", tags)

        case = self.store.get_setup_case(self.uid, case_id=cid)
        self.assertEqual(case.get("note"), "classic panic")
        self.assertEqual(case.get("lesson_id"), lid)
        self.assertEqual(json.loads(case["chips_json"]), ["plan_ok", "ad_met"])
        feats = json.loads(case["features_json"])
        self.assertEqual(feats["ad_zone"], "at_ad")
        self.assertEqual(feats["dd_pct"], 12.0)
        self.assertEqual(feats["visual_ad"]["low"], 1.35)

        home = self._lesson_from_home(lid)
        self.assertEqual(home.get("case_id"), cid)
        self.assertEqual(home.get("text"), "Took the first layer at AD")
        self.assertEqual((home.get("visual_ad") or {}).get("high"), 1.7)

        patch = self.client.patch(
            f"/api/learning/lessons/{lid}",
            json={"text": "Took the first layer at AD — edited", "behaviors": ["hesitant", "ad_missed"]},
        )
        self.assertEqual(patch.status_code, 200, patch.text)
        after = self.store.get_lesson(self.uid, lid)
        self.assertEqual(after["text"], "Took the first layer at AD — edited")
        case2 = self.store.get_setup_case(self.uid, case_id=cid)
        feats2 = json.loads(case2["features_json"])
        self.assertEqual(feats2["visual_ad"]["tf"], "5m")
        self.assertEqual(feats2["ad_zone"], "at_ad")

    def test_lookup_by_lesson_id_without_ev_tag(self):
        eid = self.store.log_event(
            self.uid, "mover_peak", "Q_USDT", "futures", price=2.0, drop_pct=-8
        )
        lid = self.store.teach_lesson(
            self.uid, "Linked only via case.lesson_id", tags=["sym:Q_USDT"]
        )
        cid = self.store.upsert_setup_case(
            self.uid,
            symbol="Q_USDT",
            market="futures",
            event_id=eid,
            fire_price=2.0,
            features={"ok": True, "ad_zone": "approach"},
            lesson_id=lid,
        )
        row = self.store.get_setup_case(self.uid, lesson_id=lid)
        self.assertIsNotNone(row)
        self.assertEqual(int(row["id"]), cid)
        home = self._lesson_from_home(lid)
        self.assertEqual(home.get("case_id"), cid)


if __name__ == "__main__":
    unittest.main()
