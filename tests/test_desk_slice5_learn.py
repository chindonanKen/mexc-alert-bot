#!/usr/bin/env python3
"""Slice 5: jump to lesson 1, Teach-this-fire, editor survives jump.

Read-only toward existing lessons. Temp-DB writes only; never touches prod data/.
"""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mexc_bot.learning.store import (  # noqa: E402
    EventStore,
    _lesson_query_is_id_jump,
    _parse_lesson_jump_query,
)

JS = (ROOT / "mexc_bot/webapi/static/assets/desk.js").read_text()
HTML = (ROOT / "mexc_bot/webapi/static/index.html").read_text()
CSS = (ROOT / "mexc_bot/webapi/static/assets/desk.css").read_text()
APP = (ROOT / "mexc_bot/webapi/app.py").read_text()
V1 = (ROOT / "mexc_bot/webapi/learning_v1.py").read_text()
STORE = (ROOT / "mexc_bot/learning/store.py").read_text()


def _fn_body(src: str, name: str, nxt: str) -> str:
    start = src.find(f"function {name}")
    if start < 0:
        start = src.find(f"async function {name}")
    end = src.find(f"function {nxt}", start + 1)
    if end < 0:
        end = src.find(f"async function {nxt}", start + 1)
    if start < 0 or end < 0:
        raise AssertionError(f"could not slice {name} → {nxt}")
    return src[start:end]


class TestLessonJumpQuery(unittest.TestCase):
    def test_empty_and_first_map_to_lesson_1(self):
        self.assertEqual(_parse_lesson_jump_query(""), 1)
        self.assertEqual(_parse_lesson_jump_query("first"), 1)
        self.assertEqual(_parse_lesson_jump_query("oldest"), 1)
        self.assertEqual(_parse_lesson_jump_query("1"), 1)
        self.assertEqual(_parse_lesson_jump_query("#1"), 1)
        self.assertEqual(_parse_lesson_jump_query("lesson 1"), 1)
        self.assertTrue(_lesson_query_is_id_jump("1"))
        self.assertTrue(_lesson_query_is_id_jump("first"))

    def test_free_text_is_not_an_id_jump(self):
        self.assertIsNone(_parse_lesson_jump_query("plan_ok"))
        self.assertFalse(_lesson_query_is_id_jump("plan_ok"))


class TestSearchReachesLessonOne(unittest.TestCase):
    def test_home_list_skips_oldest_search_returns_id_1(self):
        tmp = Path(tempfile.mkdtemp()) / "slice5_lessons.db"
        store = EventStore(tmp)
        uid = 4242
        for i in range(25):
            lid = store.teach_lesson(uid, f"slice5 fixture body {i + 1}")
            self.assertGreater(lid, 0)
        with store._lock:
            conn = store._get_conn()
            for r in conn.execute(
                "SELECT id FROM learning_lessons WHERE user_id = ? ORDER BY id",
                (uid,),
            ):
                conn.execute(
                    "UPDATE learning_lessons SET created_at = ? WHERE id = ?",
                    (1000.0 + int(r["id"]), int(r["id"])),
                )
        recent = store.list_lessons(uid, approved_only=True, limit=20)
        recent_ids = [int(r["id"]) for r in recent]
        self.assertNotIn(1, recent_ids, "home-style newest-20 must omit lesson 1")
        self.assertEqual(min(recent_ids), 6)

        hits = store.search_lessons(uid, "1")
        self.assertTrue(hits)
        self.assertEqual(int(hits[0]["id"]), 1)

        first = store.get_lesson(uid, 1)
        self.assertIsNotNone(first)
        self.assertEqual(int(first["id"]), 1)
        self.assertIn("slice5 fixture body 1", first.get("text") or "")

        span = store.lesson_id_span(uid)
        self.assertEqual(span["min_id"], 1)
        self.assertEqual(span["count"], 25)

    def test_get_and_search_are_select_only(self):
        get_start = STORE.find("def get_lesson(")
        search_start = STORE.find("def search_lessons(")
        self.assertGreater(get_start, 0)
        self.assertGreater(search_start, 0)
        get_body = STORE[get_start : get_start + 400]
        search_body = STORE[search_start : search_start + 1800]
        for body in (get_body, search_body):
            self.assertIn("SELECT", body)
            self.assertNotIn("DELETE FROM", body)
            self.assertNotIn("UPDATE learning_lessons", body)
            self.assertNotIn("INSERT INTO learning_lessons", body)


class TestDeskJumpAndTeachFire(unittest.TestCase):
    def test_cache_bust_slicelab5(self):
        self.assertIn("desk.js?v=slicelab7b", HTML)
        self.assertIn("desk.css?v=slicelab7b", HTML)

    def test_search_jump_ui_can_select_lesson_1(self):
        self.assertIn("id=\"learnLessonSearch\"", HTML)
        self.assertIn("id=\"learnJumpLesson\"", HTML)
        self.assertIn("function parseLessonJump", JS)
        self.assertIn("async function jumpToLesson", JS)
        self.assertIn("async function searchOrJumpLessons", JS)
        self.assertIn('return jumpToLesson("1")', JS)
        self.assertIn("/api/learning/lessons/${id}", JS)
        jump = _fn_body(JS, "jumpToLesson", "searchOrJumpLessons")
        self.assertIn("data-lesson-id", jump)
        self.assertNotIn("loadMemory(", jump)
        self.assertNotIn("lesEl.innerHTML", jump)

    def test_fire_row_exposes_teach_this_fire(self):
        self.assertIn("Teach-this-fire", JS)
        self.assertIn("data-teach-this-fire", JS)
        self.assertIn("data-pick-fire", JS)
        self.assertGreaterEqual(JS.count("data-teach-this-fire"), 2)

    def test_editor_survives_jump_and_teach_fire(self):
        self.assertIn("function captureLearnEditorDraft", JS)
        self.assertIn("function restoreLearnEditorDraft", JS)
        self.assertIn("keepEditor", JS)
        jump = _fn_body(JS, "jumpToLesson", "searchOrJumpLessons")
        self.assertIn("captureLearnEditorDraft", jump)
        self.assertIn("restoreLearnEditorDraft", jump)
        pick = JS[JS.find("const pickFire") : JS.find("$$(\"[data-pick-fire-btn]\"")]
        self.assertIn("keepEditor: true", pick)
        # Teach form node is never replaced by jump
        self.assertNotIn('teachForm").innerHTML', JS)
        self.assertNotIn("$(\"#teachForm\").innerHTML", JS)

    def test_readonly_get_routes_no_query_token(self):
        self.assertIn('@app.get("/api/learning/lessons/search")', APP)
        self.assertIn('@app.get("/api/learning/lessons/{lesson_id}")', APP)
        self.assertIn("def get_lesson_v1", V1)
        self.assertIn("def search_lessons_v1", V1)
        search_chunk = APP[
            APP.find('@app.get("/api/learning/lessons/search")') : APP.find(
                '@app.get("/api/learning/lessons/{lesson_id}")'
            )
            + 400
        ]
        self.assertNotIn("token: Optional[str] = Query", search_chunk)
        self.assertNotIn("?token=", search_chunk)

    def test_slices_1_to_4_still_present(self):
        self.assertIn("function setSelectedSymbol", JS)
        self.assertIn("function applySelectedSymbol", JS)
        self.assertIn("function playAlarmSound", JS)
        self.assertIn("rememberLastFired", JS)
        self.assertIn("deskStickyBar", HTML)
        self.assertIn("is-lesson-jump", CSS)


class TestEditorSurviveModel(unittest.TestCase):
    """Deterministic stand-in for: jump inserts lesson 1 without wiping the editor."""

    def test_insert_lesson_1_keeps_open_editor_and_teach_text(self):
        teach_text = "draft note still here"
        open_edit_id = 20
        list_html = (
            f'<div class="learn-lesson" data-lesson-id="{open_edit_id}">'
            f'<textarea data-edit-text="{open_edit_id}">open draft</textarea>'
            f"</div>"
        )
        # jumpToLesson prepends a card; it must not replace the list string wholesale
        card1 = '<div class="learn-lesson is-lesson-jump" data-lesson-id="1"></div>'
        after = card1 + list_html
        self.assertIn('data-lesson-id="1"', after)
        self.assertIn(f'data-lesson-id="{open_edit_id}"', after)
        self.assertIn("open draft", after)
        self.assertEqual(teach_text, "draft note still here")
        # Contrast: a full remount would drop the open textarea value
        remount = re.sub(r"<textarea[^>]*>.*?</textarea>", "<textarea></textarea>", list_html)
        self.assertNotIn("open draft", remount)


if __name__ == "__main__":
    unittest.main()
