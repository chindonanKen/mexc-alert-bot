"""AD Super-Agent API — judgments, beliefs, cases, chart features, voice.

Replaces tag-farm learning surface with training cockpit endpoints.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from ..learning.beliefs import BeliefEngine
from ..learning.chart_features import compute_fire_features
from ..learning.store import EventStore
from ..learning.trades import (
    enrich_pending_row,
    get_trade_dossier,
    list_trade_dossiers,
)
from . import db


def event_store() -> EventStore:
    return EventStore(db.db_path())


def beliefs() -> BeliefEngine:
    return BeliefEngine(event_store())


def uid_or_raise() -> int:
    uid = db.default_user_id()
    if not uid:
        env = os.getenv("DESK_USER_ID") or "8630949601"
        if str(env).strip().isdigit():
            return int(env)
        raise ValueError("No DESK_USER_ID")
    return int(uid)


def _event_row(store: EventStore, user_id: int, event_id: int) -> Optional[dict]:
    with store._lock:
        r = store._get_conn().execute(
            "SELECT * FROM learning_events WHERE id=? AND user_id=?",
            (int(event_id), int(user_id)),
        ).fetchone()
    return dict(r) if r else None


def _attach_chart(event: dict) -> dict:
    feats = compute_fire_features(
        market=str(event.get("market") or "futures"),
        symbol=str(event.get("symbol") or ""),
        fire_px=float(event.get("price") or 0),
        fire_ts=float(event.get("ts") or time.time()),
        peak_px=float(event["ref_price"]) if event.get("ref_price") else None,
        heat_breadth=event.get("heat_breadth"),
        velocity_band=event.get("velocity_band"),
    )
    # persist into payload_json
    try:
        store = event_store()
        with store._lock:
            conn = store._get_conn()
            raw = event.get("payload_json")
            payload = json.loads(raw) if raw else {}
            payload["chart_features"] = feats
            conn.execute(
                "UPDATE learning_events SET payload_json=? WHERE id=?",
                (json.dumps(payload), int(event["id"])),
            )
    except Exception:
        pass
    return feats


def judge_fire(
    *,
    event_id: Optional[int] = None,
    symbol: Optional[str] = None,
    user_id: Optional[int] = None,
    open_case: bool = True,
) -> Dict[str, Any]:
    uid = int(user_id or uid_or_raise())
    store = event_store()
    eng = beliefs()
    event = None
    if event_id:
        event = _event_row(store, uid, int(event_id))
    elif symbol:
        recent = store.recent_events(uid, limit=40)
        for e in recent:
            if symbol.upper().replace("_", "") in (e.get("symbol") or "").upper().replace(
                "_", ""
            ):
                event = e
                break
    if not event:
        raise ValueError("No event to judge")

    chart = {}
    try:
        payload = json.loads(event.get("payload_json") or "{}")
        chart = payload.get("chart_features") or {}
    except Exception:
        chart = {}
    if not chart.get("ok"):
        chart = _attach_chart(event)

    judgment = eng.judge_fire(uid, event, chart_features=chart)
    case_id = None
    if open_case and event.get("id"):
        # avoid duplicate open cases for same event
        existing = [
            c
            for c in eng.list_cases(uid, limit=30)
            if c.get("event_id") == event.get("id") and c.get("status") == "open"
        ]
        if existing:
            case_id = existing[0]["id"]
        else:
            case_id = eng.open_case(uid, event, judgment)
    return {"judgment": judgment, "case_id": case_id, "chart": chart}


def agent_bundle(user_id: Optional[int] = None) -> Dict[str, Any]:
    """Training cockpit payload — agent brain first."""
    uid = int(user_id or uid_or_raise())
    store = event_store()
    eng = beliefs()
    cases = eng.list_cases(uid, limit=15)
    open_cases = [c for c in cases if c.get("status") in ("open", "scored")]
    active = open_cases[0] if open_cases else (cases[0] if cases else None)

    # Auto-judge latest unlabeled/recent fire if no active case
    if not active:
        recent = store.recent_events(uid, limit=5)
        if recent:
            try:
                j = judge_fire(event_id=int(recent[0]["id"]), user_id=uid, open_case=True)
                cases = eng.list_cases(uid, limit=15)
                open_cases = [c for c in cases if c.get("status") in ("open", "scored")]
                active = open_cases[0] if open_cases else None
                if active and not active.get("judgment"):
                    active["judgment"] = j.get("judgment")
            except Exception:
                pass

    pending = [
        enrich_pending_row(store, p)
        for p in store.list_pending_questions(uid, status="open", limit=5)
    ]
    setups = eng.list_setup_beliefs(uid, limit=12)
    tickers = eng.list_ticker_beliefs(uid, limit=15)
    trades = list_trade_dossiers(store, uid, limit=12)
    closed = [t for t in trades if t.get("status") == "closed"]
    stats = store.learning_stats(uid)
    lessons = store.list_lessons(uid, approved_only=True, limit=8)

    # Coach pulse with weight deltas
    pulse_lines = []
    if active and active.get("judgment"):
        j = active["judgment"] if isinstance(active["judgment"], dict) else {}
        s = j.get("setup") or {}
        pulse_lines.append(
            f"Active: {active.get('symbol')} → {s.get('verdict')} "
            f"(conf {j.get('confidence')}) size={j.get('size_hint')}"
        )
        for c in (j.get("cite") or [])[:2]:
            pulse_lines.append(c)
    if setups:
        best = setups[0]
        pulse_lines.append(
            f"Best setup cell {best.get('velocity_band')}+{best.get('heat_bin')}+"
            f"{best.get('drop_bin')}: edge={float(best.get('edge') or 0):+.2f} n={best.get('n')}"
        )
    if closed:
        t = closed[0]
        pnl = t.get("pnl_pct")
        pulse_lines.append(
            f"Last trade {t.get('symbol')}: "
            f"{('+' if pnl and pnl>=0 else '')}{pnl}% hold={t.get('hold_hours')}h"
            if pnl is not None
            else f"Last trade {t.get('symbol')} open/closed"
        )
    if not pulse_lines:
        pulse_lines.append(
            "AD Super-Agent online. Fires train setup edges; closes train exec edges."
        )

    return {
        "user_id": uid,
        "agent": "AD-SuperAgent-v1",
        "active_case": active,
        "cases": cases[:10],
        "needs_you": {
            "pending_questions": pending,
            "count": len(pending),
        },
        "beliefs": {
            "setups": setups,
            "tickers": tickers,
        },
        "trades": trades,
        "closed_trades": closed[:10],
        "stats": stats,
        "lessons": lessons,
        "coach_pulse": "\n".join(pulse_lines),
        "fires": store.recent_events(uid, limit=15),
    }


def belief_setup_top(user_id: Optional[int] = None, limit: int = 15) -> Dict[str, Any]:
    uid = int(user_id or uid_or_raise())
    rows = beliefs().list_setup_beliefs(uid, limit=limit)
    worst = sorted(rows, key=lambda r: float(r.get("edge") or 0))[:5]
    return {"best": rows[:8], "worst": worst}


def belief_ticker(
    symbol: str, market: Optional[str] = None, user_id: Optional[int] = None
) -> Dict[str, Any]:
    uid = int(user_id or uid_or_raise())
    eng = beliefs()
    mkt = market or "futures"
    b = eng.get_ticker_belief(uid, symbol, mkt)
    # also try spot if empty
    if b.get("thin") and not market:
        b2 = eng.get_ticker_belief(uid, symbol, "spot")
        if not b2.get("thin"):
            b = b2
    chart = compute_fire_features(
        market=str(b.get("market") or mkt),
        symbol=symbol,
        fire_px=1.0,  # relative features still need price — soft
        fire_ts=time.time(),
    )
    # If fire_px=1 is bad for AD, only attach when we have a recent event price
    store = event_store()
    recent = store.recent_events(uid, limit=30)
    for e in recent:
        if symbol.upper().replace("_", "") in (e.get("symbol") or "").upper().replace(
            "_", ""
        ):
            chart = compute_fire_features(
                market=str(e.get("market") or mkt),
                symbol=str(e.get("symbol")),
                fire_px=float(e.get("price") or 1),
                fire_ts=float(e.get("ts") or time.time()),
                peak_px=float(e["ref_price"]) if e.get("ref_price") else None,
                heat_breadth=e.get("heat_breadth"),
                velocity_band=e.get("velocity_band"),
            )
            break
    return {"ticker": b, "chart": chart}


def coach_ask(question: str, user_id: Optional[int] = None) -> Dict[str, Any]:
    """Agent-native coach: always load beliefs + optional judge."""
    uid = int(user_id or uid_or_raise())
    store = event_store()
    eng = beliefs()
    q = (question or "").strip()
    lines = [
        "AD SUPER-AGENT (not financial advice)",
        "Judgment from setup/ticker edges + AD rules + chart features.",
        "",
    ]
    # Try judge latest or mentioned symbol
    judgment = None
    try:
        parts = q.upper().replace("/", " ").split()
        recent = store.recent_events(uid, limit=20)
        for e in recent:
            base = (e.get("symbol") or "").upper().replace("_", "").replace("USDT", "")
            if base and any(base in p or p in base for p in parts if len(p) >= 2):
                judgment = judge_fire(
                    event_id=int(e["id"]), user_id=uid, open_case=False
                )["judgment"]
                break
        if judgment is None and recent:
            if any(w in q.lower() for w in ("judge", "fire", "dump", "panic", "now", "latest")):
                judgment = judge_fire(
                    event_id=int(recent[0]["id"]), user_id=uid, open_case=False
                )["judgment"]
    except Exception:
        judgment = None

    if judgment:
        lines.append(
            f"JUDGMENT {judgment.get('symbol')}: {judgment['setup'].get('verdict')} "
            f"size={judgment.get('size_hint')} conf={judgment.get('confidence')}"
        )
        for c in judgment.get("cite") or []:
            lines.append(f"  · {c}")
        lines.append("")

    setups = eng.list_setup_beliefs(uid, limit=5)
    if setups:
        lines.append("Setup beliefs (trained):")
        for s in setups:
            lines.append(
                f"  {s.get('velocity_band')}+{s.get('heat_bin')}+{s.get('drop_bin')}: "
                f"edge={float(s.get('edge') or 0):+.2f} n={s.get('n')} "
                f"g/b={s.get('n_good')}/{s.get('n_bad')}"
            )
    else:
        lines.append("Setup beliefs: none yet — need outcomes after fires (15m/1h).")

    tickers = eng.list_ticker_beliefs(uid, limit=5)
    if tickers:
        lines.append("Ticker beliefs:")
        for t in tickers:
            lines.append(
                f"  {t.get('symbol')}: setup={float(t.get('setup_edge') or 0):+.2f} "
                f"exec={float(t.get('exec_edge') or 0):+.2f} n={t.get('n_fires')}"
            )

    trades = list_trade_dossiers(store, uid, closed_only=True, limit=4)
    if trades:
        lines.append("Recent closed trades:")
        for d in trades:
            pnl = d.get("pnl_pct")
            lines.append(
                f"  #{d.get('id')} {d.get('symbol')} "
                f"pnl={pnl}% hold={d.get('hold_hours')}h "
                f"layers={d.get('n_buys')}/{d.get('n_sells')}"
            )

    ql = q.lower()
    if "grind" in ql:
        lines.append(
            "Rule: GRIND → no_trade / micro only unless ticker setup_edge high with n≥10."
        )
    elif "panic" in ql:
        lines.append(
            "Rule: PANIC + broad heat + positive setup edge → take_layers; thin data → scout."
        )
    elif any(w in ql for w in ("rsi", "divergence", "volume", "candle", "chart")):
        lines.append(
            "Chart engine: sharpness, AD depth zone, volume expand/dry, RSI + bullish div proxy."
        )

    lines.append("")
    lines.append("Voice: judge_fire · belief_setup_top · belief_ticker · trade_review · record_process")
    return {
        "reply": "\n".join(lines),
        "judgment": judgment,
        "setups": setups,
        "tickers": tickers,
    }


def record_process(
    trade_id: int,
    *,
    tags: Optional[List[str]] = None,
    note: Optional[str] = None,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    uid = int(user_id or uid_or_raise())
    store = event_store()
    d = get_trade_dossier(store, uid, int(trade_id))
    if not d:
        raise ValueError("Trade not found")
    tags = tags or []
    # persist tags on journal
    for t in tags:
        note_part = f"[{t}]"
        with store._lock:
            conn = store._get_conn()
            row = conn.execute(
                "SELECT notes FROM journal_trades WHERE id=? AND user_id=?",
                (int(trade_id), uid),
            ).fetchone()
            old = (row["notes"] if row else "") or ""
            if note_part not in old:
                merged = (old + " | " + note_part).strip(" |")
                if note:
                    merged = (merged + " | " + note).strip(" |")
                conn.execute(
                    "UPDATE journal_trades SET notes=? WHERE id=?",
                    (merged, int(trade_id)),
                )
    d2 = get_trade_dossier(store, uid, int(trade_id))
    upd = beliefs().update_from_trade_close(
        uid, int(trade_id), dossier=d2 or d, process_tags=tags
    )
    return {"ok": True, "trade_id": trade_id, "belief_update": upd}


def teach(
    text: str,
    *,
    evidence_ids: Optional[List[int]] = None,
    tags: Optional[List[str]] = None,
    needs_approval: bool = False,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    uid = int(user_id or uid_or_raise())
    lid = event_store().teach_lesson(
        uid,
        text,
        tags=tags,
        needs_approval=bool(needs_approval),
        source="owner" if not needs_approval else "coach",
        evidence_event_ids=evidence_ids or [],
    )
    return {"ok": bool(lid), "lesson_id": lid}


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


def on_trade_closed(user_id: int, trade_id: int) -> Dict[str, Any]:
    """Call after journal close — trains exec edge."""
    store = event_store()
    d = get_trade_dossier(store, user_id, int(trade_id))
    if not d:
        return {"ok": False}
    return beliefs().update_from_trade_close(user_id, int(trade_id), dossier=d)


# Back-compat aliases used by older routes
def learning_bundle(user_id: Optional[int] = None) -> Dict[str, Any]:
    return agent_bundle(user_id)


def trades_api(**kwargs):
    uid = int(kwargs.get("user_id") or uid_or_raise())
    return {
        "trades": list_trade_dossiers(
            event_store(),
            uid,
            closed_only=bool(kwargs.get("closed_only")),
            symbol=kwargs.get("symbol"),
            limit=int(kwargs.get("limit") or 30),
        )
    }


def trade_api(trade_id: int, user_id: Optional[int] = None):
    uid = int(user_id or uid_or_raise())
    d = get_trade_dossier(event_store(), uid, int(trade_id))
    if not d:
        raise ValueError("Trade not found")
    return {"trade": d}


def tag_trade(trade_id: int, **kwargs):
    tags = []
    beh = kwargs.get("behavior")
    notes = kwargs.get("notes")
    if beh:
        tags.append(beh)
    if not tags and not (notes or "").strip():
        raise ValueError("Provide behavior and/or notes to tag")
    return record_process(
        trade_id,
        tags=tags,
        note=notes,
        user_id=kwargs.get("user_id"),
    )


def ticker_api(symbol: str, market: Optional[str] = None, user_id: Optional[int] = None):
    return belief_ticker(symbol, market=market, user_id=user_id)


def approve_draft(lesson_id: int, **kwargs):
    uid = int(kwargs.get("user_id") or uid_or_raise())
    ok = event_store().approve_lesson(
        uid, int(lesson_id), dismiss=bool(kwargs.get("dismiss"))
    )
    return {"ok": ok}
