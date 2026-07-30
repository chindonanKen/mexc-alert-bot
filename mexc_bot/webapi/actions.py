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
        "Label latest fire as took, skip, or watch",
        {
            "action": {"type": "string", "enum": ["took", "skip", "watch"]},
            "bounce_quality": {"type": "string"},
        },
        ["action"],
    ),
    _tool(
        "list_fires",
        "List recent mover/target fire events from memory",
        {"limit": {"type": "integer"}},
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
            )
        if name == "list_fires":
            return {"fires": list_recent_fires(limit=int(args.get("limit") or 12))}
        if name == "list_intel":
            return {"investigations": list_investigations(), "news": list_news()}
        if name == "get_overview":
            uid = _uid()
            return {
                "alerts": len(list_alerts()),
                "watch": len(list_watchlist()),
                "open_positions": len(list_positions()),
                "movers": get_movers_settings(),
                "recent_fires": list_recent_fires(limit=5),
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
