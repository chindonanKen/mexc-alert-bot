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
    lessons = store.list_lessons(uid, approved_only=True, limit=limit_lessons)
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
    trades = list_money_reviews(uid, limit=15, teach_only=True, store=store)
    # recent fires
    fires = []
    try:
        for e in store.recent_events(uid, limit=25):
            fires.append(
                {
                    "id": e.get("id"),
                    "symbol": e.get("symbol"),
                    "market": e.get("market"),
                    "drop_pct": e.get("drop_pct"),
                    "velocity_band": e.get("velocity_band"),
                    "ts": e.get("ts"),
                    "price": e.get("price"),
                    "last_action": e.get("last_action") or e.get("action"),
                }
            )
    except Exception:
        pass

    return {
        "user_id": uid,
        "product": "learning_v1",
        "needs_you": {
            "pending_questions": pending,
            "count": len(pending_all),
            "has_lessons": bool(learned.get("lessons")),
        },
        "pending_questions": pending,
        "lessons": learned.get("lessons") or [],
        "what_learned_reply": learned.get("reply") or "",
        "stats": learned.get("stats") or {},
        "trades": trades,
        "fires": fires,
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
