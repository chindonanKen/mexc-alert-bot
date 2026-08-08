"""Single money-truth path for teaching: exchange-backed position entities.

Positions desk UI already uses ``list_position_entities``. Coach, voice tools,
and belief training must use the same shape — not journal dossiers alone.

**Teaching window:** only trades in the AD Desk era (registered fills / since
``LEARNING_TEACH_SINCE``) are ``teach_ok``. Older exchange history can still
be listed for display but is not used to train the agent.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .engagement import symbols_match
from .store import EventStore

logger = logging.getLogger(__name__)

# Default: desk / private-read era (owner's registered history). Override via env.
_DEFAULT_TEACH_SINCE = "2026-07-01"


def teach_since_ts(store: Optional[EventStore] = None, user_id: int = 0) -> float:
    """Unix ts: only trades at/after this are allowed for agent training.

    1) LEARNING_TEACH_SINCE=YYYY-MM-DD (or unix seconds)
    2) Else earliest journal_fill for user (what we have registered)
    3) Else default 2026-07-01
    """
    raw = (os.getenv("LEARNING_TEACH_SINCE") or "").strip()
    if raw:
        try:
            if raw.isdigit() or (raw.replace(".", "", 1).isdigit() and raw.count(".") < 2):
                return float(raw)
            # YYYY-MM-DD
            dt = datetime.strptime(raw[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except Exception:
            logger.warning("Invalid LEARNING_TEACH_SINCE=%r — using default", raw)
    if store is not None and user_id:
        try:
            with store._lock:
                row = store._get_conn().execute(
                    "SELECT MIN(ts) AS m FROM journal_fills WHERE user_id=?",
                    (int(user_id),),
                ).fetchone()
            if row and row["m"]:
                return float(row["m"])
        except Exception:
            pass
    try:
        dt = datetime.strptime(_DEFAULT_TEACH_SINCE, "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        )
        return dt.timestamp()
    except Exception:
        return 0.0


def in_teach_window(
    entity: dict, *, since: float
) -> bool:
    """Closed: closed_at (or opened_at) >= since. Open: always if since in past."""
    if since <= 0:
        return True
    is_open = entity.get("status") == "open" or entity.get("is_open")
    if is_open:
        # Open bags we hold now are teachable (layers from registered fills)
        o = float(entity.get("opened_at") or 0)
        if o <= 0:
            return True
        return o >= since - 86400  # 1d pad
    c = float(entity.get("closed_at") or entity.get("opened_at") or 0)
    if c <= 0:
        return False
    return c >= since


def money_truth_label(entity: dict) -> str:
    """exchange | fill_cycle | fill_recon_unverified | journal_manual.

    fill_cycle = completed flat from fill walk (spot closed with buys+sells).
    That is the normal path for newly closed spot — still teachable process + fill $.
    """
    is_open = entity.get("status") == "open" or entity.get("is_open")
    n_sells = int(entity.get("n_sells") or 0)
    rem = float(entity.get("size_remaining") or 0)
    # Complete closed fill cycle (newest spot closes land here, not history_positions)
    if (
        not is_open
        and (entity.get("recon_from_fills") or entity.get("money_truth") in (
            "fill_recon_unverified",
            "fill_cycle",
            None,
            "",
        ))
        and n_sells > 0
        and rem <= 1e-8
        and not entity.get("exchange_history")
    ):
        return "fill_cycle"
    if entity.get("money_truth") in (
        "exchange",
        "fill_cycle",
        "fill_recon_unverified",
        "journal_manual",
    ):
        # Don't let stale fill_recon_unverified win over complete cycle detection above
        mt = str(entity["money_truth"])
        if mt == "fill_recon_unverified" and not is_open and n_sells > 0 and rem <= 1e-8:
            return "fill_cycle"
        return mt
    if entity.get("exchange_history") or entity.get("exchange_hold"):
        return "exchange"
    if entity.get("recon_from_fills"):
        if not is_open and n_sells > 0 and rem <= 1e-8:
            return "fill_cycle"
        return "fill_recon_unverified"
    if entity.get("journal_id") or entity.get("notes"):
        return "journal_manual"
    return "unknown"


def entity_to_review(
    entity: dict,
    *,
    events: Optional[List[dict]] = None,
    teach_since: float = 0.0,
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
    in_window = in_teach_window(entity, since=teach_since)
    # Long-term invest (hold book): never AD teach / bulk learn
    is_hold = (
        (entity.get("position_book") or "").lower() == "hold"
        or entity.get("is_hold") is True
        or entity.get("ad_learning") is False
    )
    # $ + process training: exchange-backed OR complete fill cycles in desk era
    # (spot closes never appear in futures history_positions)
    teach_ok = (not is_hold) and in_window and mt in ("exchange", "fill_cycle")
    # Always listable for Learning picker when we have a real AD position cycle
    listable = (not is_hold) and in_window and mt in (
        "exchange",
        "fill_cycle",
        "fill_recon_unverified",
    )
    if is_open and mt == "exchange" and not is_hold:
        listable = True
        teach_ok = teach_ok or in_window
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
        "bought_usd": entity.get("bought_usd"),
        "sold_usd": entity.get("sold_usd"),
        "remaining_mark_usd": entity.get("remaining_mark_usd"),
        "remaining_cost_usd": entity.get("remaining_cost_usd"),
        "principal_recovered": entity.get("principal_recovered"),
        "free_coins": entity.get("free_coins"),
        "free_coins_status": entity.get("free_coins_status"),
        "position_book": "hold" if is_hold else (entity.get("position_book") or "ad"),
        "is_hold": is_hold,
        "ad_learning": not is_hold,
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
        "in_teach_window": in_window,
        "teach_since": teach_since if teach_since > 0 else None,
        "teach_ok": teach_ok,
        "listable_for_teach": listable or teach_ok,
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
    listable_only: bool = False,
    store: Optional[EventStore] = None,
) -> List[dict]:
    """Reviews from position entities (same source as Positions desk).

    teach_only: exchange + complete fill_cycle (agent $ training).
    listable_only: Learning picker — includes fill_recon opens/partials too.
    """
    from ..webapi.positions_enrich import list_position_entities

    include_closed = not open_only
    entities = list_position_entities(
        user_id, include_closed=include_closed, closed_limit=max(limit, 60)
    )
    events: List[dict] = []
    if store is not None:
        try:
            events = store.recent_events(user_id, limit=100)
        except Exception:
            events = []

    since = teach_since_ts(store, user_id)
    reviews = []
    for e in entities:
        st = e.get("status")
        if closed_only and st != "closed":
            continue
        if open_only and st != "open" and not e.get("is_open"):
            continue
        if symbol and not symbols_match(symbol, e.get("symbol") or ""):
            continue
        r = entity_to_review(e, events=events, teach_since=since)
        if teach_only and not r.get("teach_ok"):
            continue
        if listable_only and not (
            r.get("listable_for_teach") or r.get("teach_ok")
        ):
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
