"""Single money-truth path for teaching: exchange-backed position entities.

Positions desk UI already uses ``list_position_entities``. Coach, voice tools,
and belief training must use the same shape — not journal dossiers alone.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from .engagement import symbols_match
from .store import EventStore

logger = logging.getLogger(__name__)


def money_truth_label(entity: dict) -> str:
    """exchange | fill_recon_unverified | journal_manual."""
    if entity.get("exchange_history") or entity.get("exchange_hold"):
        return "exchange"
    if entity.get("recon_from_fills"):
        # Spot (and any residual without exchange open/history authority)
        return "fill_recon_unverified"
    if entity.get("journal_id") or entity.get("notes"):
        return "journal_manual"
    return "unknown"


def entity_to_review(
    entity: dict,
    *,
    events: Optional[List[dict]] = None,
) -> dict:
    """Map a position entity → dossier-like review for coach/tools."""
    is_open = entity.get("status") == "open" or entity.get("is_open")
    mt = money_truth_label(entity)
    entry = entity.get("entry_display")
    if entry is None:
        entry = entity.get("entry_avg")
    pnl_pct = (
        entity.get("upnl_pct") if is_open else entity.get("realized_pnl_pct")
    )
    pnl_usd = (
        entity.get("upnl_usd_est") if is_open else entity.get("realized_pnl_usd")
    )
    eid = entity.get("entity_key") or entity.get("id")
    review = {
        "id": eid,
        "entity_key": entity.get("entity_key") or str(eid),
        "symbol": entity.get("symbol"),
        "market": entity.get("market") or "spot",
        "status": "open" if is_open else "closed",
        "entry_avg": entry,
        "hold_avg": entity.get("hold_avg"),
        "entry_live": entity.get("entry_live") or entry,
        "exit_avg": entity.get("exit_avg"),
        "opened_at": entity.get("opened_at"),
        "closed_at": entity.get("closed_at"),
        "hold_seconds": entity.get("hold_seconds"),
        "hold_hours": entity.get("hold_hours"),
        "pnl_pct": round(float(pnl_pct), 3) if pnl_pct is not None else None,
        "pnl_usd": round(float(pnl_usd), 4) if pnl_usd is not None else None,
        "outcome": entity.get("outcome"),
        "size_remaining": entity.get("size_remaining"),
        "size_qty": entity.get("size_qty"),
        "size_sold": entity.get("size_sold"),
        "buy_qty": entity.get("size_qty"),
        "sell_qty": entity.get("size_sold"),
        "n_buys": entity.get("n_buys") or 0,
        "n_sells": entity.get("n_sells") or 0,
        "buy_layers": entity.get("buy_orders") or [],
        "sell_layers": entity.get("sell_orders") or [],
        "notes": entity.get("notes"),
        "leverage": entity.get("leverage"),
        "hold_fee": entity.get("hold_fee"),
        "position_side": entity.get("position_side"),
        "money_truth": mt,
        "exchange_history": bool(entity.get("exchange_history")),
        "exchange_hold": bool(entity.get("exchange_hold")),
        "verified": mt == "exchange",
        "teach_ok": mt == "exchange",  # coach may only claim $ facts when True
        "source": (
            "mexc_history_positions"
            if entity.get("exchange_history")
            else (
                "mexc_open_positions"
                if entity.get("exchange_hold")
                else "fill_recon"
            )
        ),
        "linked_events": [],
        "primary_event_id": None,
    }
    if events:
        linked = _link_events(
            events,
            symbol=str(review["symbol"] or ""),
            market=str(review["market"] or ""),
            opened_at=float(review["opened_at"] or 0) or None,
        )
        review["linked_events"] = linked
        review["primary_event_id"] = linked[0]["id"] if linked else None
    return review


def _link_events(
    events: List[dict],
    *,
    symbol: str,
    market: str,
    opened_at: Optional[float],
    window_s: float = 6 * 3600,
) -> List[dict]:
    out = []
    o = float(opened_at or 0)
    for e in events:
        if not symbols_match(symbol, e.get("symbol") or ""):
            continue
        em = (e.get("market") or "").lower()
        if market and em and em != market.lower() and market.lower() != "futures":
            # allow futures events near futures trades
            if not (market.lower() == "futures" and em == "futures"):
                if market.lower() == "spot" and em == "spot":
                    pass
                elif em and em != market.lower():
                    continue
        ts = float(e.get("ts") or 0)
        if o > 0 and abs(ts - o) > window_s and ts > o + window_s:
            # prefer fires near open
            if ts < o - window_s or ts > o + window_s:
                continue
        out.append(
            {
                "id": e.get("id"),
                "symbol": e.get("symbol"),
                "price": e.get("price"),
                "ts": ts,
                "velocity_band": e.get("velocity_band"),
                "drop_pct": e.get("drop_pct"),
            }
        )
    out.sort(key=lambda x: abs((x.get("ts") or 0) - o) if o else 0)
    return out[:8]


def list_money_reviews(
    user_id: int,
    *,
    closed_only: bool = False,
    open_only: bool = False,
    symbol: Optional[str] = None,
    limit: int = 40,
    teach_only: bool = False,
    store: Optional[EventStore] = None,
) -> List[dict]:
    """Reviews from exchange-backed position entities (same as Positions desk)."""
    from ..webapi.positions_enrich import list_position_entities

    include_closed = not open_only
    entities = list_position_entities(
        user_id, include_closed=include_closed, closed_limit=max(limit, 40)
    )
    events: List[dict] = []
    if store is not None:
        try:
            events = store.recent_events(user_id, limit=100)
        except Exception:
            events = []

    reviews = []
    for e in entities:
        st = e.get("status")
        if closed_only and st != "closed":
            continue
        if open_only and st != "open" and not e.get("is_open"):
            continue
        if symbol and not symbols_match(symbol, e.get("symbol") or ""):
            continue
        r = entity_to_review(e, events=events)
        if teach_only and not r.get("teach_ok"):
            continue
        reviews.append(r)

    # newest first
    # Optional journal manuals not already represented (desk Open form / tests)
    if store is not None and not teach_only:
        try:
            from .trades import list_trade_dossiers

            seen = {
                str(r.get("symbol") or "").upper().replace("_", "")
                + ":"
                + str(r.get("status"))
                for r in reviews
                if r.get("money_truth") == "exchange"
            }
            for d in list_trade_dossiers(
                store,
                user_id,
                closed_only=closed_only,
                open_only=open_only,
                symbol=symbol,
                limit=limit,
            ):
                sk = (
                    str(d.get("symbol") or "").upper().replace("_", "")
                    + ":"
                    + str(d.get("status"))
                )
                if sk in seen:
                    continue
                d = dict(d)
                d["money_truth"] = "journal_manual"
                d["teach_ok"] = False
                d["verified"] = False
                d["buy_layers"] = d.get("buy_layers") or []
                d["sell_layers"] = d.get("sell_layers") or []
                d["entity_key"] = d.get("entity_key") or f"journal:{d.get('id')}"
                reviews.append(d)
        except Exception:
            pass

    reviews.sort(
        key=lambda x: float(x.get("closed_at") or x.get("opened_at") or 0),
        reverse=True,
    )
    return reviews[: max(1, min(int(limit), 100))]


def get_money_review(
    user_id: int,
    review_id: Any,
    *,
    store: Optional[EventStore] = None,
) -> Optional[dict]:
    """Lookup by entity_key or synthetic id."""
    rid = str(review_id)
    for r in list_money_reviews(user_id, limit=100, store=store):
        if str(r.get("id")) == rid or str(r.get("entity_key")) == rid:
            return r
    return None


def coach_last_closed_line(user_id: int, store: Optional[EventStore] = None) -> str:
    """One safe line for coach pulse — exchange-verified closed only."""
    closed = list_money_reviews(
        user_id, closed_only=True, limit=1, teach_only=True, store=store
    )
    if not closed:
        # fall back to any closed with label
        closed = list_money_reviews(
            user_id, closed_only=True, limit=1, teach_only=False, store=store
        )
        if not closed:
            return "No closed exchange trades yet for coach cite."
        t = closed[0]
        return (
            f"Last closed {t.get('symbol')} ({t.get('money_truth')}): "
            f"do not treat $ as exchange-verified"
        )
    t = closed[0]
    pnl = t.get("pnl_pct")
    usd = t.get("pnl_usd")
    parts = [f"Last closed {t.get('symbol')} [exchange]"]
    if pnl is not None:
        parts.append(f"{'+' if pnl >= 0 else ''}{pnl}%")
    if usd is not None:
        parts.append(f"${usd}")
    if t.get("hold_hours") is not None:
        parts.append(f"hold={t.get('hold_hours')}h")
    return " ".join(parts)
