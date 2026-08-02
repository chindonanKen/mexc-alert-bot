"""Desk learning helpers — EventStore backed (no Telegram)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from ..coach.engine import (
    format_brief,
    format_coach_pulse,
    format_coach_reply,
    propose_behavior_draft,
)
from ..learning.store import EventStore
from . import db


def event_store() -> EventStore:
    return EventStore(db.db_path())


def uid_or_raise() -> int:
    uid = db.default_user_id()
    if not uid:
        # Owner default when empty DB
        import os

        env = os.getenv("DESK_USER_ID") or "8630949601"
        if str(env).strip().isdigit():
            return int(env)
        raise ValueError("No DESK_USER_ID")
    return int(uid)


def learning_bundle(user_id: Optional[int] = None) -> Dict[str, Any]:
    uid = int(user_id or uid_or_raise())
    store = event_store()
    stats = store.learning_stats(uid)
    pending = store.list_pending_questions(uid, status="open", limit=5)
    drafts = store.list_lessons(uid, pending_only=True, limit=10)
    lessons = store.list_lessons(uid, approved_only=True, limit=10)
    fires = store.recent_events(uid, limit=20)
    pulse = format_coach_pulse(
        stats=stats,
        lessons=lessons,
        pending_n=len(pending),
        drafts_n=len(drafts),
    )
    return {
        "user_id": uid,
        "stats": stats,
        "pending_questions": pending,
        "drafts": drafts,
        "lessons": lessons,
        "fires": fires,
        "coach_pulse": pulse,
        "needs_you": {
            "pending_questions": pending,
            "drafts": drafts,
            "count": len(pending) + len(drafts),
        },
    }


def coach_ask(question: str, user_id: Optional[int] = None) -> Dict[str, Any]:
    uid = int(user_id or uid_or_raise())
    store = event_store()
    stats = store.learning_stats(uid)
    recent = store.recent_events(uid, limit=15)
    lessons = store.list_lessons(uid, approved_only=True, limit=8)
    pending = store.list_pending_questions(uid, limit=5)
    opens = store.journal_list(uid, open_only=True)
    q = (question or "").strip()
    if not q or q.lower() in ("brief", "summary", "desk", "overview"):
        text = format_brief(
            recent_events=recent,
            open_trades=opens,
            learning_on=True,
            stats=stats,
            lessons=lessons,
            pending=pending,
        )
    else:
        text = format_coach_reply(
            q,
            recent_events=recent,
            stats=stats,
            lessons=lessons,
            pending=pending,
        )
    # Optional draft from latest event (deduped — same event/text won't flood Needs you)
    draft_id = None
    if recent:
        prop = propose_behavior_draft(event=recent[0], stats=stats)
        if prop:
            draft_id = store.teach_lesson(
                uid,
                prop["text"],
                tags=prop.get("tags"),
                needs_approval=True,
                source="coach",
                kind=prop.get("kind") or "behavior_draft",
                evidence_event_ids=prop.get("evidence_event_ids"),
                dedupe=True,
            )
    return {
        "reply": text,
        "stats": stats,
        "draft_id": draft_id or None,
        "pulse": format_coach_pulse(
            stats=stats,
            lessons=lessons,
            pending_n=len(pending),
            drafts_n=stats.get("pending_drafts") or 0,
        ),
    }


def teach(
    text: str,
    *,
    tags: Optional[List[str]] = None,
    needs_approval: bool = False,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    uid = int(user_id or uid_or_raise())
    lid = event_store().teach_lesson(
        uid,
        text,
        tags=tags,
        needs_approval=needs_approval,
        source="owner" if not needs_approval else "coach",
    )
    return {"ok": bool(lid), "lesson_id": lid}


def approve_draft(
    lesson_id: int, *, dismiss: bool = False, user_id: Optional[int] = None
) -> Dict[str, Any]:
    uid = int(user_id or uid_or_raise())
    ok = event_store().approve_lesson(uid, int(lesson_id), dismiss=dismiss)
    return {"ok": ok, "lesson_id": lesson_id, "dismissed": dismiss}


def answer_question(
    question_id: int,
    *,
    answer_text: Optional[str] = None,
    action: Optional[str] = None,
    behavior: Optional[str] = None,
    dismiss: bool = False,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    uid = int(user_id or uid_or_raise())
    ok = event_store().answer_pending_question(
        uid,
        int(question_id),
        answer_text=answer_text,
        action=action,
        behavior=behavior,
        dismiss=dismiss,
    )
    return {"ok": ok, "question_id": question_id}
