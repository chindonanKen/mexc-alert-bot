"""Desk learning API — EventStore + trade dossiers + ticker profiles."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from ..coach.engine import (
    format_brief,
    format_coach_pulse,
    format_coach_reply,
    propose_behavior_draft,
)
from ..learning.store import EventStore
from ..learning.trades import (
    candle_features_soft,
    enrich_pending_row,
    get_trade_dossier,
    list_active_tickers,
    list_trade_dossiers,
    ticker_profile,
)
from . import db


def event_store() -> EventStore:
    return EventStore(db.db_path())


def uid_or_raise() -> int:
    uid = db.default_user_id()
    if not uid:
        env = os.getenv("DESK_USER_ID") or "8630949601"
        if str(env).strip().isdigit():
            return int(env)
        raise ValueError("No DESK_USER_ID")
    return int(uid)


def learning_bundle(user_id: Optional[int] = None) -> Dict[str, Any]:
    uid = int(user_id or uid_or_raise())
    store = event_store()
    stats = store.learning_stats(uid)
    raw_pending = store.list_pending_questions(uid, status="open", limit=5)
    pending = [enrich_pending_row(store, p) for p in raw_pending]
    drafts = store.list_lessons(uid, pending_only=True, limit=10)
    lessons = store.list_lessons(uid, approved_only=True, limit=12)
    fires = store.recent_events(uid, limit=25)
    trades = list_trade_dossiers(store, uid, limit=25)
    closed = [t for t in trades if t.get("status") == "closed"]
    tickers = list_active_tickers(store, uid, limit=20)
    pulse = format_coach_pulse(
        stats=stats,
        lessons=lessons,
        pending_n=len(pending),
        drafts_n=len(drafts),
    )
    # Append trade snapshot to pulse
    if closed:
        last = closed[0]
        pnl = last.get("pnl_pct")
        pnl_s = f"{pnl:+.1f}%" if pnl is not None else "n/a"
        pulse += (
            f"\nLast closed: {last.get('symbol')} {pnl_s} in "
            f"{last.get('hold_hours')}h · {last.get('n_buys')} buys / "
            f"{last.get('n_sells')} sells."
        )
    return {
        "user_id": uid,
        "stats": stats,
        "pending_questions": pending,
        "drafts": drafts,
        "lessons": lessons,
        "fires": fires,
        "trades": trades,
        "closed_trades": closed[:15],
        "tickers": tickers,
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
    pending = [
        enrich_pending_row(store, p)
        for p in store.list_pending_questions(uid, limit=5)
    ]
    opens = store.journal_list(uid, open_only=True)
    closed = list_trade_dossiers(store, uid, closed_only=True, limit=8)
    q = (question or "").strip()

    # Ticker-scoped if question mentions a known symbol
    ticker_ctx = None
    q_up = q.upper().replace(" ", "")
    for t in list_active_tickers(store, uid, limit=40):
        sym = (t.get("symbol") or "").upper()
        base = sym.replace("_", "").replace("USDT", "")
        if base and len(base) >= 2 and base in q_up.replace("_", ""):
            ticker_ctx = ticker_profile(store, uid, t["symbol"], t.get("market"))
            break

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

    if closed:
        text += "\n\nRecent closed trades (from your journal):"
        for d in closed[:5]:
            pnl = d.get("pnl_pct")
            pnl_s = f"{pnl:+.1f}%" if pnl is not None else "?"
            text += (
                f"\n  #{d.get('id')} {d.get('symbol')} {pnl_s} "
                f"hold={d.get('hold_hours')}h "
                f"layers={d.get('n_buys')}/{d.get('n_sells')}"
            )
    if ticker_ctx:
        text += (
            f"\n\nTicker {ticker_ctx.get('symbol')}: fires={ticker_ctx.get('fires')} "
            f"took={ticker_ctx.get('took')} skip={ticker_ctx.get('skip')} "
            f"closed_trades={ticker_ctx.get('closed_trades')} "
            f"avg_pnl={ticker_ctx.get('avg_pnl_pct')} "
            f"avg_hold_h={ticker_ctx.get('avg_hold_hours')}"
        )
        soft = candle_features_soft(
            ticker_ctx.get("market") or "futures",
            str(ticker_ctx.get("symbol") or ""),
        )
        if soft.get("ok") and soft.get("consecutive_reds"):
            text += f"\n  Candle reds (soft): {soft['consecutive_reds']}"

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
        "ticker": ticker_ctx,
        "closed_trades": closed[:5],
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


def trades_api(
    user_id: Optional[int] = None,
    *,
    closed_only: bool = False,
    symbol: Optional[str] = None,
    limit: int = 30,
) -> Dict[str, Any]:
    uid = int(user_id or uid_or_raise())
    store = event_store()
    rows = list_trade_dossiers(
        store,
        uid,
        closed_only=closed_only,
        symbol=symbol,
        limit=limit,
    )
    return {"user_id": uid, "trades": rows}


def trade_api(trade_id: int, user_id: Optional[int] = None) -> Dict[str, Any]:
    uid = int(user_id or uid_or_raise())
    d = get_trade_dossier(event_store(), uid, int(trade_id))
    if not d:
        raise ValueError("Trade not found")
    # soft candle context on close
    feat = candle_features_soft(
        d.get("market") or "futures",
        str(d.get("symbol") or ""),
        around_ts=d.get("closed_at") or d.get("opened_at"),
    )
    d["candle_features"] = feat
    return {"trade": d}


def ticker_api(
    symbol: str,
    market: Optional[str] = None,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    uid = int(user_id or uid_or_raise())
    store = event_store()
    prof = ticker_profile(store, uid, symbol, market)
    prof["candle_features"] = candle_features_soft(
        market or prof.get("market") or "futures", symbol
    )
    return {"ticker": prof}


def tag_trade(
    trade_id: int,
    *,
    behavior: Optional[str] = None,
    notes: Optional[str] = None,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Attach behavior to linked event and/or trade notes."""
    uid = int(user_id or uid_or_raise())
    store = event_store()
    d = get_trade_dossier(store, uid, int(trade_id))
    if not d:
        raise ValueError("Trade not found")
    eid = d.get("primary_event_id")
    if eid and behavior:
        store.label_event(
            int(eid),
            uid,
            behavior=behavior,
            notes=notes,
            source="human",
            confidence=1.0,
        )
    if notes:
        # append journal notes via SQL
        with store._lock:
            conn = store._get_conn()
            row = conn.execute(
                "SELECT notes FROM journal_trades WHERE id = ? AND user_id = ?",
                (int(trade_id), uid),
            ).fetchone()
            if row:
                old = row["notes"] or ""
                merged = (old + " | " + notes).strip(" |") if old else notes
                if behavior:
                    merged = f"[{behavior}] {merged}"
                conn.execute(
                    "UPDATE journal_trades SET notes = ? WHERE id = ?",
                    (merged, int(trade_id)),
                )
    return {"ok": True, "trade_id": trade_id, "behavior": behavior}
