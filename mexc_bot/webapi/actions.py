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


def remove_watch(symbol: str, market: Optional[str] = None, user_id: Optional[int] = None) -> dict:
    uid = _uid(user_id)
    sym = symbol.upper().strip()
    conn = db.connect()
    try:
        if market:
            conn.execute(
                "DELETE FROM mover_watchlist WHERE user_id = ? AND symbol = ? AND market = ?",
                (uid, sym, market.lower()),
            )
        else:
            conn.execute(
                "DELETE FROM mover_watchlist WHERE user_id = ? AND (symbol = ? OR symbol LIKE ?)",
                (uid, sym, f"%{sym.replace('_', '')}%"),
            )
        conn.commit()
        return {"ok": True, "removed": sym}
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
        return {"ok": True, "id": tid}
    finally:
        conn.close()


def label_latest(
    action: Optional[str] = None,
    bounce: Optional[str] = None,
    behavior: Optional[str] = None,
    user_id: Optional[int] = None,
) -> dict:
    uid = _uid(user_id)
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT id FROM learning_events WHERE user_id = ? ORDER BY ts DESC LIMIT 1",
            (uid,),
        ).fetchone()
        if not row:
            raise ValueError("No learning events to label")
        eid = int(row[0] if not hasattr(row, "keys") else row["id"])
        conn.execute(
            """
            INSERT INTO learning_labels (
                event_id, user_id, action, bounce_quality, behavior, notes, ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (eid, uid, action, bounce, behavior, "desk/voice", time.time()),
        )
        conn.commit()
        return {"ok": True, "event_id": eid, "action": action}
    finally:
        conn.close()


# ---- Voice / agent tool registry ----

TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "add_alert",
            "description": "Add a one-shot price target alert on spot or futures",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "price": {"type": "number"},
                    "market": {"type": "string", "enum": ["spot", "futures"]},
                },
                "required": ["symbol", "price"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_alert",
            "description": "Delete target alert by stable_id or visual_id",
            "parameters": {
                "type": "object",
                "properties": {
                    "stable_id": {"type": "integer"},
                    "visual_id": {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_alerts",
            "description": "List current target alerts",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_watch",
            "description": "Add symbol to downside mover watchlist",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "market": {"type": "string", "enum": ["spot", "futures"]},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_watch",
            "description": "Remove symbol from mover watchlist",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "market": {"type": "string"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_movers",
            "description": "Enable/disable movers or set threshold % and lookback minutes",
            "parameters": {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean"},
                    "threshold_percent": {"type": "number"},
                    "lookback_minutes": {"type": "number"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_position",
            "description": "Record an open trade in the journal (paper desk log — not exchange order unless live enabled)",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "market": {"type": "string"},
                    "entry_avg": {"type": "number"},
                    "notes": {"type": "string"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "close_position",
            "description": "Close journal position by id or symbol",
            "parameters": {
                "type": "object",
                "properties": {
                    "trade_id": {"type": "integer"},
                    "symbol": {"type": "string"},
                    "exit_avg": {"type": "number"},
                    "notes": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_positions",
            "description": "List open journal trades / positions",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "label_fire",
            "description": "Label latest mover/target fire as took, skip, or watch",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["took", "skip", "watch"]},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_overview",
            "description": "Get desk status summary counts",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_trade",
            "description": (
                "Propose a trade plan (AD layers) without placing an exchange order. "
                "Always prefer this over live orders unless user explicitly insists on live."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "thesis": {"type": "string"},
                    "entry_zone": {"type": "string"},
                    "layers": {"type": "integer"},
                    "invalidation": {"type": "string"},
                },
                "required": ["symbol", "thesis"],
            },
        },
    },
]


def run_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    args = args or {}
    try:
        if name == "add_alert":
            return add_alert(args["symbol"], float(args["price"]), args.get("market", "spot"))
        if name == "delete_alert":
            if args.get("stable_id"):
                return delete_alert(int(args["stable_id"]))
            if args.get("visual_id"):
                return delete_alert_visual(int(args["visual_id"]))
            raise ValueError("stable_id or visual_id required")
        if name == "list_alerts":
            return {"alerts": list_alerts()}
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
            return {"positions": list_positions()}
        if name == "label_fire":
            return label_latest(action=args.get("action"))
        if name == "get_overview":
            uid = _uid()
            return {
                "alerts": len(list_alerts()),
                "watch": len(list_watchlist()),
                "open_positions": len(list_positions()),
                "user_id": uid,
                "live_orders_allowed": live_orders_allowed(),
            }
        if name == "propose_trade":
            plan = {
                "symbol": args["symbol"].upper(),
                "thesis": args.get("thesis"),
                "entry_zone": args.get("entry_zone"),
                "layers": args.get("layers") or 5,
                "invalidation": args.get("invalidation"),
                "mode": "paper_proposal",
                "note": "Not sent to exchange. Use open_position to journal; live orders require DESK_ALLOW_LIVE_ORDERS.",
            }
            # persist as learning note if possible
            try:
                open_position(
                    plan["symbol"],
                    "futures",
                    notes=f"PROPOSAL: {plan['thesis'][:200]}",
                )
                plan["journaled_proposal"] = True
            except Exception:
                plan["journaled_proposal"] = False
            return plan
        return {"error": f"Unknown tool {name}"}
    except Exception as e:
        logger.exception("tool %s failed", name)
        return {"error": str(e)}
