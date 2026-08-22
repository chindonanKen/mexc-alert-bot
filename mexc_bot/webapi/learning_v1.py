"""Learning V1 — teach the AD agent from desk data + owner judgment.

No coach product. Teacher = Kenneth. Student = agent.
Surfaces: pending, teach, what I've learned, recent, stats.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from ..learning.money_truth import list_money_reviews
from ..learning.store import EventStore
from . import db


def event_store() -> EventStore:
    return EventStore(db.db_path())


def uid_or_raise() -> int:
    uid = db.default_user_id()
    if not uid:
        raise ValueError("DESK_USER_ID not configured")
    return int(uid)


def what_have_you_learned(
    user_id: Optional[int] = None, *, limit_lessons: int = 20
) -> Dict[str, Any]:
    """Agent recall: lessons + stats + short real cites (no inventions)."""
    uid = int(user_id or uid_or_raise())
    store = event_store()
    lessons_raw = store.list_lessons(uid, approved_only=True, limit=limit_lessons)
    try:
        from ..learning.incident import enrich_lesson_row

        lessons = [enrich_lesson_row(L) for L in lessons_raw]
    except Exception:
        lessons = lessons_raw
    try:
        from ..learning.cases import stamp_lessons_with_cases

        lessons = stamp_lessons_with_cases(store, uid, lessons)
    except Exception:
        pass
    stats = store.learning_stats(uid)
    trades = list_money_reviews(
        uid, closed_only=True, teach_only=True, limit=5, store=store
    )
    lines: List[str] = []
    if lessons:
        lines.append("Lessons I store:")
        for L in lessons[:12]:
            lines.append(f"  · {(L.get('text') or '')[:160]}")
    else:
        lines.append("No durable lessons yet — teach me a rule.")
    try:
        n_cases = len(store.list_setup_cases(uid, limit=5))
        if n_cases:
            lines.append(f"Setup cases frozen (P1): at least {n_cases} recent.")
    except Exception:
        pass
    lines.append("")
    lines.append(
        f"Stats: fires={stats.get('events') or 0} took={stats.get('took') or 0} "
        f"skip={stats.get('skip') or 0}"
    )
    if trades:
        lines.append("Recent teach_ok closed trades:")
        for t in trades[:4]:
            pnl = t.get("pnl_pct")
            usd = t.get("pnl_usd")
            lines.append(
                f"  · {t.get('symbol')} {t.get('market')} "
                f"pnl={pnl}% usd={usd} B{t.get('n_buys')}/S{t.get('n_sells')}"
            )
    return {
        "reply": "\n".join(lines),
        "lessons": lessons,
        "stats": stats,
        "recent_trades": trades,
        "lesson_count": len(lessons),
    }


def overview_learning_strip(user_id: Optional[int] = None) -> Dict[str, Any]:
    """Overview Needs-you + memory strip — not the full Learning home.

    `/api/learning` stays on learning_home_v1 (lessons, trades, cases).
    Overview only paints pending (max 2) + the first ~420 chars of the
    memory strip. Building the 640KB home (list_money_reviews ×2, cases,
    lesson enrich) was the Overview first-paint wait — do not call it here.
    """
    uid = int(user_id or uid_or_raise())
    store = event_store()

    pending_raw = store.list_pending_questions(uid, status="open", limit=10)
    pending_all: List[dict] = []
    for p in pending_raw:
        row = dict(p)
        try:
            from ..learning.trades import enrich_pending_row

            row = enrich_pending_row(store, p)
        except Exception:
            pass
        pending_all.append(row)
    pending = pending_all[:2]

    lessons: List[dict] = []
    try:
        lessons = store.list_lessons(uid, approved_only=True, limit=12)
    except Exception:
        lessons = []

    lines: List[str] = []
    if lessons:
        lines.append("Lessons I store:")
        for L in lessons[:12]:
            lines.append(f"  · {(L.get('text') or '')[:160]}")
    else:
        lines.append("No durable lessons yet — teach me a rule.")
    reply = "\n".join(lines)

    return {
        "needs_you": {
            "pending_questions": pending,
            "count": len(pending_all),
            "has_lessons": bool(lessons),
        },
        "agent_summary": reply,
        "what_learned_reply": reply,
        "stats": {},
    }


def learning_home_v1(user_id: Optional[int] = None) -> Dict[str, Any]:
    """Single payload for Learning view + voice context."""
    uid = int(user_id or uid_or_raise())
    store = event_store()

    pending_raw = store.list_pending_questions(uid, status="open", limit=10)
    pending_all: List[dict] = []
    for p in pending_raw:
        row = dict(p)
        try:
            from ..learning.trades import enrich_pending_row

            row = enrich_pending_row(store, p)
        except Exception:
            pass
        pending_all.append(row)
    pending = pending_all[:2]  # hard cap display 2

    learned = what_have_you_learned(uid)
    # Learning picker: all listable cycles (open + closed), newest first.
    # Includes complete spot fill closes (fill_cycle) — not only futures EXCH.
    trades = list_money_reviews(
        uid, limit=40, listable_only=True, teach_only=False, store=store
    )
    # recent fires + any frozen case (P1)
    fires = []
    try:
        from ..learning.cases import case_public_view

        for e in store.recent_events(uid, limit=25):
            case_row = None
            try:
                case_row = store.get_setup_case(
                    uid, event_id=int(e["id"])
                ) if e.get("id") else None
            except Exception:
                case_row = None
            case_view = case_public_view(case_row) if case_row else None
            fires.append(
                {
                    "id": e.get("id"),
                    "symbol": e.get("symbol"),
                    "market": e.get("market"),
                    "drop_pct": e.get("drop_pct"),
                    "velocity_band": e.get("velocity_band"),
                    "ts": e.get("ts"),
                    "price": e.get("price"),
                    "ref_price": e.get("ref_price"),
                    "heat_breadth": e.get("heat_breadth"),
                    "last_action": e.get("last_action") or e.get("action"),
                    "case": case_view,
                    "has_case": bool(case_view),
                }
            )
    except Exception:
        pass

    cases = []
    try:
        from ..learning.cases import case_public_view

        for row in store.list_setup_cases(uid, limit=30):
            cases.append(case_public_view(row))
    except Exception:
        pass

    stats = dict(learned.get("stats") or {})
    stats["cases"] = len(cases)

    return {
        "user_id": uid,
        "product": "learning_v1",
        "phase": "p1_cases",
        "needs_you": {
            "pending_questions": pending,
            "count": len(pending_all),
            "has_lessons": bool(learned.get("lessons")),
        },
        "pending_questions": pending,
        "lessons": learned.get("lessons") or [],
        "what_learned_reply": learned.get("reply") or "",
        "stats": stats,
        "trades": trades,
        "fires": fires,
        "cases": cases,
        "agent_summary": learned.get("reply") or "",
    }


def agent_ask(question: str, user_id: Optional[int] = None) -> Dict[str, Any]:
    """Lightweight agent Q&A for Learning UI — not a coach product.

    Routes:
    - learned / what have you learned → what_have_you_learned
    - else brief from stats + lessons + last teach_ok trade
    """
    uid = int(user_id or uid_or_raise())
    q = (question or "").strip().lower()
    if any(
        w in q
        for w in (
            "learned",
            "what do you know",
            "what have you",
            "memory",
            "lessons",
        )
    ):
        out = what_have_you_learned(uid)
        return {"reply": out["reply"], "kind": "what_learned", **out}

    home = learning_home_v1(uid)
    lines = [
        "I'm your AD agent (student). You teach; I store.",
        "",
        home.get("agent_summary") or "",
    ]
    if home.get("pending_questions"):
        lines.append("")
        lines.append(
            f"I have {len(home['pending_questions'])} question(s) waiting for you."
        )
    if q and q not in ("brief", "status", "?"):
        lines.append("")
        lines.append(f"(You asked: {question[:200]})")
        lines.append(
            "Use voice or tools: teach, what_have_you_learned, list_teachable_trades, "
            "list_pending_questions."
        )
    return {
        "reply": "\n".join(lines).strip(),
        "kind": "brief",
        "stats": home.get("stats"),
        "lesson_count": len(home.get("lessons") or []),
    }
