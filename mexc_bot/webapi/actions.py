"""Desk mutations + voice tool dispatch. Safe defaults: no live exchange orders."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from . import db

logger = logging.getLogger(__name__)


def _uid(explicit: Optional[int] = None) -> int:
    uid = explicit or db.default_user_id()
    if not uid:
        raise ValueError("No DESK_USER_ID / no rows in DB — set DESK_USER_ID in .env")
    return int(uid)


def live_orders_allowed() -> bool:
    return os.getenv("DESK_ALLOW_LIVE_ORDERS", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


# ---- Alerts CRUD ----

def list_alerts(user_id: Optional[int] = None) -> List[dict]:
    uid = _uid(user_id)
    rows = db.fetch_all(
        "SELECT id, user_id, symbol, price, enabled, market FROM alerts WHERE user_id = ? ORDER BY id ASC",
        (uid,),
    )
    for i, r in enumerate(rows, 1):
        r["visual_id"] = i
        r["stable_id"] = r["id"]
    return rows


def add_alert(
    symbol: str,
    price: float,
    market: str = "spot",
    user_id: Optional[int] = None,
) -> dict:
    uid = _uid(user_id)
    mkt = (market or "spot").lower()
    if mkt not in ("spot", "futures"):
        mkt = "spot"
    sym = symbol.upper().strip()
    if mkt == "spot" and not sym.endswith("USDT"):
        sym = sym + "USDT" if not sym.endswith("USDT") else sym
    conn = db.connect()
    try:
        cur = conn.execute(
            "INSERT INTO alerts (user_id, symbol, price, enabled, market) VALUES (?, ?, ?, 1, ?)",
            (uid, sym, float(price), mkt),
        )
        conn.commit()
        sid = int(cur.lastrowid)
        return {"ok": True, "stable_id": sid, "symbol": sym, "price": float(price), "market": mkt}
    finally:
        conn.close()


def update_alert(
    stable_id: int,
    *,
    price: Optional[float] = None,
    enabled: Optional[bool] = None,
    user_id: Optional[int] = None,
) -> dict:
    uid = _uid(user_id)
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM alerts WHERE id = ? AND user_id = ?",
            (stable_id, uid),
        ).fetchone()
        if not row:
            raise ValueError("Alert not found")
        if price is not None:
            conn.execute(
                "UPDATE alerts SET price = ? WHERE id = ? AND user_id = ?",
                (float(price), stable_id, uid),
            )
        if enabled is not None:
            conn.execute(
                "UPDATE alerts SET enabled = ? WHERE id = ? AND user_id = ?",
                (1 if enabled else 0, stable_id, uid),
            )
        conn.commit()
        return {"ok": True, "stable_id": stable_id}
    finally:
        conn.close()


def delete_alert(stable_id: int, user_id: Optional[int] = None) -> dict:
    uid = _uid(user_id)
    conn = db.connect()
    try:
        cur = conn.execute(
            "DELETE FROM alerts WHERE id = ? AND user_id = ?",
            (stable_id, uid),
        )
        conn.commit()
        if cur.rowcount < 1:
            raise ValueError("Alert not found")
        return {"ok": True, "deleted": stable_id}
    finally:
        conn.close()


def delete_alert_visual(visual_id: int, user_id: Optional[int] = None) -> dict:
    alerts = list_alerts(user_id)
    for a in alerts:
        if a["visual_id"] == visual_id:
            return delete_alert(a["stable_id"], user_id)
    raise ValueError(f"No alert at visual #{visual_id}")


# ---- Watchlist ----

def list_watchlist(user_id: Optional[int] = None) -> List[dict]:
    uid = _uid(user_id)
    return db.fetch_all(
        "SELECT symbol, market FROM mover_watchlist WHERE user_id = ? ORDER BY market, symbol",
        (uid,),
    )


def add_watch(symbol: str, market: str = "futures", user_id: Optional[int] = None) -> dict:
    uid = _uid(user_id)
    mkt = (market or "futures").lower()
    if mkt not in ("spot", "futures"):
        mkt = "futures"
    sym = symbol.upper().strip().replace("-", "_")
    if mkt == "futures" and "_" not in sym and not sym.endswith("USDT"):
        sym = f"{sym}_USDT"
    elif mkt == "futures" and sym.endswith("USDT") and "_" not in sym:
        # keep compact form if already TSLAUSDT-like
        pass
    conn = db.connect()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO mover_watchlist (user_id, symbol, market) VALUES (?, ?, ?)",
            (uid, sym, mkt),
        )
        conn.commit()
        return {"ok": True, "symbol": sym, "market": mkt}
    finally:
        conn.close()


def _norm_sym(s: str) -> str:
    return (
        (s or "")
        .upper()
        .replace("_", "")
        .replace("-", "")
        .replace("STOCK", "")
        .strip()
    )


def _base_asset(norm: str) -> str:
    """Strip a single trailing USDT only (BTCUSDT→BTC). No substring matching."""
    n = (norm or "").upper()
    if n.endswith("USDT") and len(n) > 4:
        return n[:-4]
    return n


def watch_symbols_match(query: str, row_symbol: str) -> bool:
    """True if query and row are the same book id under compact/underscore forms.

    BTC == BTCUSDT == BTC_USDT. ETH does NOT match ETHFI_USDT. SOL does NOT match SOLV.
    """
    q = _norm_sym(query)
    r = _norm_sym(row_symbol)
    if not q or not r:
        return False
    if q == r:
        return True
    return _base_asset(q) == _base_asset(r) and bool(_base_asset(q))


def remove_watch(symbol: str, market: Optional[str] = None, user_id: Optional[int] = None) -> dict:
    """Remove watchlist row(s). Matches compact / underscore / STOCK forms only (exact base)."""
    uid = _uid(user_id)
    sym = symbol.upper().strip()
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT symbol, market FROM mover_watchlist WHERE user_id = ?",
            (uid,),
        ).fetchall()
        removed: List[str] = []
        for r in rows:
            rsym = r["symbol"] if hasattr(r, "keys") else r[0]
            rmkt = r["market"] if hasattr(r, "keys") else r[1]
            if market and (rmkt or "").lower() != market.lower():
                continue
            if not watch_symbols_match(sym, str(rsym)):
                continue
            conn.execute(
                "DELETE FROM mover_watchlist WHERE user_id = ? AND symbol = ? AND market = ?",
                (uid, rsym, rmkt),
            )
            removed.append(f"{rsym}:{rmkt}")
        conn.commit()
        if not removed:
            raise ValueError(
                f"No watchlist row matched {sym!r}"
                + (f" market={market}" if market else "")
                + " — check symbol form (e.g. BTC_USDT vs BTCUSDT)"
            )
        return {"ok": True, "removed": removed, "symbol": sym}
    finally:
        conn.close()


def set_movers(
    *,
    enabled: Optional[bool] = None,
    threshold_percent: Optional[float] = None,
    lookback_minutes: Optional[float] = None,
    user_id: Optional[int] = None,
) -> dict:
    uid = _uid(user_id)
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM mover_settings WHERE user_id = ?", (uid,)
        ).fetchone()
        thr = float(threshold_percent) if threshold_percent is not None else (
            float(row["threshold_percent"]) if row else 5.0
        )
        look = int(lookback_minutes * 60) if lookback_minutes is not None else (
            int(row["lookback_seconds"]) if row else 900
        )
        en = (
            (1 if enabled else 0)
            if enabled is not None
            else (int(row["enabled"]) if row else 0)
        )
        if row:
            conn.execute(
                """
                UPDATE mover_settings
                SET enabled = ?, threshold_percent = ?, lookback_seconds = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
                """,
                (en, thr, look, uid),
            )
        else:
            conn.execute(
                """
                INSERT INTO mover_settings (user_id, enabled, threshold_percent, lookback_seconds)
                VALUES (?, ?, ?, ?)
                """,
                (uid, en, thr, look),
            )
        conn.commit()
        return {
            "ok": True,
            "enabled": bool(en),
            "threshold_percent": thr,
            "lookback_seconds": look,
        }
    finally:
        conn.close()


# ---- Journal / positions ----

def list_positions(user_id: Optional[int] = None, include_closed: bool = False) -> List[dict]:
    uid = _uid(user_id)
    if include_closed:
        return db.fetch_all(
            "SELECT * FROM journal_trades WHERE user_id = ? ORDER BY opened_at DESC LIMIT 50",
            (uid,),
        )
    return db.fetch_all(
        "SELECT * FROM journal_trades WHERE user_id = ? AND status = 'open' ORDER BY opened_at DESC",
        (uid,),
    )


def open_position(
    symbol: str,
    market: str = "futures",
    entry_avg: Optional[float] = None,
    notes: Optional[str] = None,
    user_id: Optional[int] = None,
) -> dict:
    uid = _uid(user_id)
    conn = db.connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO journal_trades (
                user_id, symbol, market, status, entry_avg, notes, opened_at
            ) VALUES (?, ?, ?, 'open', ?, ?, ?)
            """,
            (
                uid,
                symbol.upper(),
                (market or "futures").lower(),
                entry_avg,
                notes or "desk",
                time.time(),
            ),
        )
        conn.commit()
        return {"ok": True, "id": int(cur.lastrowid)}
    finally:
        conn.close()


def close_position(
    trade_id: Optional[int] = None,
    symbol: Optional[str] = None,
    exit_avg: Optional[float] = None,
    notes: Optional[str] = None,
    user_id: Optional[int] = None,
) -> dict:
    uid = _uid(user_id)
    conn = db.connect()
    try:
        if trade_id:
            row = conn.execute(
                "SELECT id FROM journal_trades WHERE id = ? AND user_id = ? AND status = 'open'",
                (trade_id, uid),
            ).fetchone()
        elif symbol:
            row = conn.execute(
                """
                SELECT id FROM journal_trades
                WHERE user_id = ? AND status = 'open' AND UPPER(symbol) LIKE ?
                ORDER BY opened_at DESC LIMIT 1
                """,
                (uid, f"%{symbol.upper()}%"),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT id FROM journal_trades
                WHERE user_id = ? AND status = 'open'
                ORDER BY opened_at DESC LIMIT 1
                """,
                (uid,),
            ).fetchone()
        if not row:
            raise ValueError("No open position matched")
        tid = int(row[0] if not hasattr(row, "keys") else row["id"])
        conn.execute(
            """
            UPDATE journal_trades
            SET status = 'closed', exit_avg = ?, notes = COALESCE(notes,'') || ?, closed_at = ?
            WHERE id = ?
            """,
            (exit_avg, f" | {notes}" if notes else "", time.time(), tid),
        )
        conn.commit()
        # Super-agent: train execution edge on close
        try:
            from .learning_api import on_trade_closed

            on_trade_closed(uid, int(tid))
        except Exception:
            pass
        return {"ok": True, "id": tid}
    finally:
        conn.close()


def label_latest(
    action: Optional[str] = None,
    bounce: Optional[str] = None,
    behavior: Optional[str] = None,
    notes: Optional[str] = None,
    user_id: Optional[int] = None,
) -> dict:
    from ..learning.store import EventStore

    uid = _uid(user_id)
    store = EventStore(db.db_path())
    eid = store.label_latest(
        uid,
        action=action,
        bounce_quality=bounce,
        behavior=behavior,
        notes=notes or "desk/voice",
        source="human",
        confidence=1.0,
    )
    if not eid:
        raise ValueError("No learning events to label")
    return {"ok": True, "event_id": eid, "action": action}


def list_recent_fires(user_id: Optional[int] = None, limit: int = 12) -> List[dict]:
    uid = _uid(user_id)
    return db.fetch_all(
        """
        SELECT id, source, symbol, market, drop_pct, velocity_band, mode, ts, price
        FROM learning_events WHERE user_id = ?
        ORDER BY ts DESC LIMIT ?
        """,
        (uid, max(1, min(int(limit), 40))),
    )


def list_investigations(user_id: Optional[int] = None, limit: int = 10) -> List[dict]:
    uid = _uid(user_id)
    return db.fetch_all(
        """
        SELECT id, symbol, market, drop_pct, velocity_band, heat_breadth,
               verdict, confidence, ts
        FROM investigations WHERE user_id = ?
        ORDER BY ts DESC LIMIT ?
        """,
        (uid, max(1, min(int(limit), 30))),
    )


def list_news(limit: int = 12) -> List[dict]:
    return db.fetch_all(
        """
        SELECT id, symbol, class, severity, title, source, ts
        FROM news_events ORDER BY ts DESC LIMIT ?
        """,
        (max(1, min(int(limit), 40)),),
    )


def get_movers_settings(user_id: Optional[int] = None) -> dict:
    uid = _uid(user_id)
    row = db.fetch_one(
        "SELECT enabled, threshold_percent, lookback_seconds FROM mover_settings WHERE user_id = ?",
        (uid,),
    )
    if not row:
        return {"enabled": False, "threshold_percent": 5.0, "lookback_seconds": 900}
    return {
        "enabled": bool(row.get("enabled")),
        "threshold_percent": float(row.get("threshold_percent") or 5),
        "lookback_seconds": int(row.get("lookback_seconds") or 900),
    }


# ---- Voice / agent tool registry (full desk control) ----

def _tool(name: str, description: str, properties: dict, required: Optional[List[str]] = None) -> dict:
    params: Dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        params["required"] = required
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": params},
    }


TOOL_DEFS = [
    _tool(
        "add_alert",
        "Add a one-shot price target alarm/alert on spot or futures",
        {
            "symbol": {"type": "string"},
            "price": {"type": "number"},
            "market": {"type": "string", "enum": ["spot", "futures"]},
        },
        ["symbol", "price"],
    ),
    _tool(
        "update_alert",
        "Update alert price and/or enabled by stable_id or visual list number",
        {
            "stable_id": {"type": "integer"},
            "visual_id": {"type": "integer"},
            "price": {"type": "number"},
            "enabled": {"type": "boolean"},
        },
    ),
    _tool(
        "delete_alert",
        "Delete target alert by stable_id or visual_id",
        {"stable_id": {"type": "integer"}, "visual_id": {"type": "integer"}},
    ),
    _tool("list_alerts", "List all target alarms/alerts", {}),
    _tool("list_watchlist", "List mover watchlist and mover settings", {}),
    _tool(
        "add_watch",
        "Add symbol to downside mover watchlist",
        {
            "symbol": {"type": "string"},
            "market": {"type": "string", "enum": ["spot", "futures"]},
        },
        ["symbol"],
    ),
    _tool(
        "remove_watch",
        "Remove symbol from mover watchlist",
        {"symbol": {"type": "string"}, "market": {"type": "string"}},
        ["symbol"],
    ),
    _tool(
        "set_movers",
        "Enable/disable movers or set threshold % and lookback minutes",
        {
            "enabled": {"type": "boolean"},
            "threshold_percent": {"type": "number"},
            "lookback_minutes": {"type": "number"},
        },
    ),
    _tool(
        "open_position",
        "Open a journal (paper) position — not a live exchange order",
        {
            "symbol": {"type": "string"},
            "market": {"type": "string"},
            "entry_avg": {"type": "number"},
            "notes": {"type": "string"},
        },
        ["symbol"],
    ),
    _tool(
        "close_position",
        "Close journal position by id or symbol",
        {
            "trade_id": {"type": "integer"},
            "symbol": {"type": "string"},
            "exit_avg": {"type": "number"},
            "notes": {"type": "string"},
        },
    ),
    _tool(
        "list_positions",
        "List journal trades/positions",
        {"include_closed": {"type": "boolean"}},
    ),
    _tool(
        "label_fire",
        "Label latest fire as took, skip, watch, partial, or late",
        {
            "action": {
                "type": "string",
                "enum": ["took", "skip", "watch", "partial", "late"],
            },
            "bounce_quality": {"type": "string"},
            "behavior": {"type": "string"},
            "notes": {"type": "string"},
        },
        ["action"],
    ),
    _tool(
        "list_fires",
        "List recent mover/target fire events from memory",
        {"limit": {"type": "integer"}},
    ),
    _tool(
        "list_pending_questions",
        "List open desk learning questions needing owner answer",
        {},
    ),
    _tool(
        "answer_question",
        "Answer or dismiss a pending learning question",
        {
            "question_id": {"type": "integer"},
            "answer_text": {"type": "string"},
            "action": {"type": "string"},
            "behavior": {"type": "string"},
            "dismiss": {"type": "boolean"},
        },
        ["question_id"],
    ),
    _tool(
        "teach",
        "Save a durable trading lesson to desk memory",
        {
            "text": {"type": "string"},
            "needs_approval": {"type": "boolean"},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        ["text"],
    ),
    _tool(
        "approve_draft",
        "Approve or dismiss a coach lesson/behavior draft",
        {
            "lesson_id": {"type": "integer"},
            "dismiss": {"type": "boolean"},
        },
        ["lesson_id"],
    ),
    _tool(
        "learning_stats",
        "Aggregate learning stats from event log (took/skip/bounce)",
        {},
    ),
    _tool(
        "coach_ask",
        "Ask AD Super-Agent (beliefs + judgment + chart)",
        {"question": {"type": "string"}},
        ["question"],
    ),
    _tool(
        "judge_fire",
        "Judge a fire/event with trained setup+ticker edges and chart features",
        {
            "event_id": {"type": "integer"},
            "symbol": {"type": "string"},
        },
    ),
    _tool(
        "belief_setup_top",
        "Best/worst trained setup cells (band+heat+drop edges)",
        {"limit": {"type": "integer"}},
    ),
    _tool(
        "belief_ticker",
        "Trained ticker setup_edge + exec_edge + chart features",
        {
            "symbol": {"type": "string"},
            "market": {"type": "string"},
        },
        ["symbol"],
    ),
    _tool(
        "list_trade_reviews",
        "List trade dossiers (PnL, hold, buy/sell layers, linked fires)",
        {
            "closed_only": {"type": "boolean"},
            "symbol": {"type": "string"},
            "limit": {"type": "integer"},
        },
    ),
    _tool(
        "get_trade_review",
        "One trade dossier by journal trade id",
        {"trade_id": {"type": "integer"}},
        ["trade_id"],
    ),
    _tool(
        "record_process",
        "Record process tags on a trade and update agent exec edge",
        {
            "trade_id": {"type": "integer"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "note": {"type": "string"},
        },
        ["trade_id"],
    ),
    _tool(
        "ticker_stats",
        "Alias of belief_ticker",
        {
            "symbol": {"type": "string"},
            "market": {"type": "string"},
        },
        ["symbol"],
    ),
    _tool(
        "tag_trade",
        "Tag trade process (updates exec beliefs)",
        {
            "trade_id": {"type": "integer"},
            "behavior": {"type": "string"},
            "notes": {"type": "string"},
        },
        ["trade_id"],
    ),
    _tool(
        "list_agent_cases",
        "List open training cases with agent judgments",
        {"limit": {"type": "integer"}},
    ),
    _tool(
        "read_chart",
        "Full discretionary AD chart read (regime, AD zone, volume, RSI div, thesis) for a symbol you follow",
        {
            "symbol": {"type": "string"},
            "market": {"type": "string"},
            "refresh": {"type": "boolean"},
        },
        ["symbol"],
    ),
    _tool(
        "refresh_book_charts",
        "Re-read charts for entire book: targets + movers watchlist + open positions",
        {},
    ),
    _tool(
        "correct_judgment",
        "Owner corrects the agent's call (no_trade|take_scout|take_layers|wait_deeper) with reason — updates case + nudges beliefs",
        {
            "correct_verdict": {"type": "string"},
            "reason": {"type": "string"},
            "event_id": {"type": "integer"},
            "case_id": {"type": "integer"},
            "symbol": {"type": "string"},
        },
        ["correct_verdict", "reason"],
    ),
    _tool(
        "list_intel",
        "List isolated-dump investigations and recent news",
        {},
    ),
    _tool(
        "get_overview",
        "Desk status: counts, movers settings, recent fires",
        {},
    ),
    _tool(
        "propose_trade",
        "Propose AD trade plan (paper). Prefer over live orders.",
        {
            "symbol": {"type": "string"},
            "thesis": {"type": "string"},
            "entry_zone": {"type": "string"},
            "layers": {"type": "integer"},
            "invalidation": {"type": "string"},
        },
        ["symbol", "thesis"],
    ),
]


def run_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    args = args or {}
    try:
        if name == "add_alert":
            return add_alert(args["symbol"], float(args["price"]), args.get("market", "spot"))
        if name == "update_alert":
            sid = args.get("stable_id")
            if not sid and args.get("visual_id"):
                for a in list_alerts():
                    if a["visual_id"] == int(args["visual_id"]):
                        sid = a["stable_id"]
                        break
            if not sid:
                raise ValueError("stable_id or visual_id required")
            return update_alert(
                int(sid),
                price=float(args["price"]) if args.get("price") is not None else None,
                enabled=args.get("enabled"),
            )
        if name == "delete_alert":
            if args.get("stable_id"):
                return delete_alert(int(args["stable_id"]))
            if args.get("visual_id"):
                return delete_alert_visual(int(args["visual_id"]))
            raise ValueError("stable_id or visual_id required")
        if name == "list_alerts":
            return {"alerts": list_alerts()}
        if name == "list_watchlist":
            return {"watchlist": list_watchlist(), "settings": get_movers_settings()}
        if name == "add_watch":
            return add_watch(args["symbol"], args.get("market", "futures"))
        if name == "remove_watch":
            return remove_watch(args["symbol"], args.get("market"))
        if name == "set_movers":
            return set_movers(
                enabled=args.get("enabled"),
                threshold_percent=args.get("threshold_percent"),
                lookback_minutes=args.get("lookback_minutes"),
            )
        if name == "open_position":
            return open_position(
                args["symbol"],
                args.get("market", "futures"),
                args.get("entry_avg"),
                args.get("notes"),
            )
        if name == "close_position":
            return close_position(
                args.get("trade_id"),
                args.get("symbol"),
                args.get("exit_avg"),
                args.get("notes"),
            )
        if name == "list_positions":
            return {
                "positions": list_positions(include_closed=bool(args.get("include_closed")))
            }
        if name == "label_fire":
            return label_latest(
                action=args.get("action"),
                bounce=args.get("bounce_quality"),
                behavior=args.get("behavior"),
                notes=args.get("notes"),
            )
        if name == "list_fires":
            return {"fires": list_recent_fires(limit=int(args.get("limit") or 12))}
        if name == "list_pending_questions":
            from .learning_api import learning_bundle

            b = learning_bundle()
            return {"pending_questions": b.get("pending_questions") or []}
        if name == "answer_question":
            from .learning_api import answer_question

            return answer_question(
                int(args["question_id"]),
                answer_text=args.get("answer_text"),
                action=args.get("action"),
                behavior=args.get("behavior"),
                dismiss=bool(args.get("dismiss")),
            )
        if name == "teach":
            from .learning_api import teach

            return teach(
                str(args.get("text") or ""),
                tags=args.get("tags"),
                needs_approval=bool(args.get("needs_approval")),
            )
        if name == "approve_draft":
            from .learning_api import approve_draft

            return approve_draft(
                int(args["lesson_id"]), dismiss=bool(args.get("dismiss"))
            )
        if name == "learning_stats":
            from .learning_api import learning_bundle

            return {"stats": learning_bundle().get("stats")}
        if name == "coach_ask":
            from .learning_api import coach_ask

            return coach_ask(str(args.get("question") or "brief"))
        if name == "judge_fire":
            from .learning_api import judge_fire

            return judge_fire(
                event_id=args.get("event_id"),
                symbol=args.get("symbol"),
                open_case=True,
            )
        if name == "belief_setup_top":
            from .learning_api import belief_setup_top

            return belief_setup_top(limit=int(args.get("limit") or 15))
        if name == "belief_ticker" or name == "ticker_stats":
            from .learning_api import belief_ticker

            return belief_ticker(
                str(args["symbol"]), market=args.get("market")
            )
        if name == "list_trade_reviews":
            from .learning_api import trades_api

            return trades_api(
                closed_only=bool(args.get("closed_only")),
                symbol=args.get("symbol"),
                limit=int(args.get("limit") or 20),
            )
        if name == "get_trade_review":
            from .learning_api import trade_api

            return trade_api(int(args["trade_id"]))
        if name == "record_process" or name == "tag_trade":
            from .learning_api import record_process, tag_trade

            if name == "tag_trade":
                return tag_trade(
                    int(args["trade_id"]),
                    behavior=args.get("behavior"),
                    notes=args.get("notes"),
                )
            return record_process(
                int(args["trade_id"]),
                tags=args.get("tags") or [],
                note=args.get("note"),
            )
        if name == "list_agent_cases":
            from .learning_api import beliefs as _bel, uid_or_raise

            return {
                "cases": _bel().list_cases(
                    uid_or_raise(), limit=int(args.get("limit") or 15)
                )
            }
        if name == "read_chart":
            from .learning_api import read_symbol_chart

            return read_symbol_chart(
                str(args["symbol"]),
                market=args.get("market"),
                refresh=args.get("refresh", True),
            )
        if name == "refresh_book_charts":
            from .learning_api import refresh_book_charts

            return refresh_book_charts()
        if name == "correct_judgment":
            from .learning_api import correct_judgment

            return correct_judgment(
                correct_verdict=str(args["correct_verdict"]),
                reason=str(args["reason"]),
                event_id=args.get("event_id"),
                case_id=args.get("case_id"),
                symbol=args.get("symbol"),
            )
        if name == "list_intel":
            return {"investigations": list_investigations(), "news": list_news()}
        if name == "get_overview":
            uid = _uid()
            out = {
                "alerts": len(list_alerts()),
                "watch": len(list_watchlist()),
                "open_positions": len(list_positions()),
                "movers": get_movers_settings(),
                "recent_fires": list_recent_fires(limit=5),
                "user_id": uid,
                "live_orders_allowed": live_orders_allowed(),
            }
            try:
                from .learning_api import learning_bundle

                b = learning_bundle(uid)
                out["needs_you"] = b.get("needs_you")
                out["coach_pulse"] = b.get("coach_pulse")
                out["stats"] = b.get("stats")
            except Exception:
                pass
            return out
        if name == "propose_trade":
            plan = {
                "symbol": args["symbol"].upper(),
                "thesis": args.get("thesis"),
                "entry_zone": args.get("entry_zone"),
                "layers": args.get("layers") or 5,
                "invalidation": args.get("invalidation"),
                "mode": "paper_proposal",
                "note": "Not sent to exchange. Use open_position to journal.",
            }
            try:
                open_position(
                    plan["symbol"],
                    "futures",
                    notes=f"PROPOSAL: {(plan.get('thesis') or '')[:200]}",
                )
                plan["journaled_proposal"] = True
            except Exception:
                plan["journaled_proposal"] = False
            return plan
        return {"error": f"Unknown tool {name}"}
    except Exception as e:
        logger.exception("tool %s failed", name)
        return {"error": str(e)}
