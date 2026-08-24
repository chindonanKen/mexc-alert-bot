"""Trade dossiers + ticker profiles for AD Desk learning.

Built from journal_trades + journal_fills + learning_events. Soft-fail klines.
Never touches alerts table.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Sequence

from .engagement import symbols_match
from .store import EventStore

logger = logging.getLogger(__name__)


def _f(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _pnl_pct(entry: Optional[float], exit_: Optional[float]) -> Optional[float]:
    e, x = _f(entry), _f(exit_)
    if e is None or x is None or e == 0:
        return None
    return (x - e) / e * 100.0


def _hold_seconds(opened: Any, closed: Any) -> Optional[float]:
    o, c = _f(opened), _f(closed)
    if o is None:
        return None
    end = c if c is not None else time.time()
    return max(0.0, end - o)


def fills_for_trade(
    fills: Sequence[dict],
    *,
    symbol: str,
    market: str,
    opened_at: Optional[float],
    closed_at: Optional[float],
    all_history: bool = False,
    strict_market: bool = False,
) -> Dict[str, List[dict]]:
    """Split fills into buy/sell layers.

    Default: window around journal open/close.
    ``all_history=True``: every fill for the symbol (for correct size/avg).
    ``strict_market=True``: never mix spot and futures books (position entities).
    """
    o = _f(opened_at) or 0.0
    c = _f(closed_at) or (time.time() + 1)
    win_start = o - 120
    win_end = c + 120
    buys: List[dict] = []
    sells: List[dict] = []
    mwant = (market or "").lower()
    for f in fills:
        if not symbols_match(symbol, f.get("symbol") or ""):
            continue
        fm = (f.get("market") or "").lower()
        if mwant and fm and fm not in ("", mwant):
            if strict_market:
                continue
            # dossier convenience: futures journal may still want spot fills
            if mwant == "futures" and fm == "spot":
                pass
            else:
                continue
        ts = _f(f.get("ts")) or 0.0
        if not all_history and (ts < win_start or ts > win_end):
            continue
        side = (f.get("side") or "").lower()
        layer = {
            "price": _f(f.get("price")),
            "qty": _f(f.get("qty")),
            "ts": ts,
            "side": side,
            "exchange_trade_id": f.get("exchange_trade_id"),
            "quote_qty": _f(f.get("quote_qty")),
            "market": fm or mwant or "spot",
        }
        if side in ("buy", "long", "bid"):
            buys.append(layer)
        elif side in ("sell", "short", "ask"):
            sells.append(layer)
    buys.sort(key=lambda x: x.get("ts") or 0)
    sells.sort(key=lambda x: x.get("ts") or 0)
    return {"buys": buys, "sells": sells}


def _all_fills_chronological(
    fills: Sequence[dict], *, symbol: str, market: str
) -> List[dict]:
    layers = fills_for_trade(
        fills,
        symbol=symbol,
        market=market,
        opened_at=0,
        closed_at=time.time() + 1,
        all_history=True,
        strict_market=True,
    )
    all_fills: List[dict] = []
    for b in layers["buys"]:
        all_fills.append({**b, "side": "buy"})
    for s in layers["sells"]:
        all_fills.append({**s, "side": "sell"})
    all_fills.sort(key=lambda x: x.get("ts") or 0)
    return all_fills


def remaining_cost_average(
    bought_usd: Any,
    sold_usd: Any,
    remaining_qty: Any,
) -> Optional[float]:
    """Open-position average: (bought USD − sold USD) / remaining qty.

    Sell above leftover avg → leftover avg goes down.
    Sell below leftover avg → leftover avg goes up.
    None when remaining qty is not positive (closed / dust — no divide by zero).
    """
    rem = _f(remaining_qty)
    if rem is None or rem <= 1e-12:
        return None
    bought = _f(bought_usd) or 0.0
    sold = _f(sold_usd) or 0.0
    return (bought - sold) / rem


def _layers_notional(orders: Optional[Sequence[dict]]) -> float:
    tot = 0.0
    for o in orders or []:
        q = _f(o.get("quote_qty"))
        if q is not None and q > 0:
            tot += q
            continue
        p, qty = _f(o.get("price")), _f(o.get("qty"))
        if p is not None and qty is not None and qty > 0 and p > 0:
            tot += p * qty
    return tot


def apply_open_remaining_cost_avg(entity: dict) -> dict:
    """Set user-visible open avg / remaining cost from bought − sold.

    Closed or flat leftover: no-op. No buy/sell dollars and no layers: leave
    existing entry_avg (exchange hold avg or journal).
    """
    if not entity:
        return entity
    if not (entity.get("status") == "open" or entity.get("is_open")):
        return entity
    rem = _f(entity.get("size_remaining"))
    if rem is None or rem <= 1e-12:
        return entity

    has_layers = bool(entity.get("buy_orders") or entity.get("sell_orders"))
    has_money = (
        entity.get("bought_usd") is not None or entity.get("sold_usd") is not None
    )
    if not has_money and not has_layers:
        return entity

    bought = _f(entity.get("bought_usd"))
    sold = _f(entity.get("sold_usd"))
    if bought is None:
        bought = _layers_notional(entity.get("buy_orders"))
    if sold is None:
        sold = _layers_notional(entity.get("sell_orders"))
    bought = bought or 0.0
    sold = sold or 0.0
    if bought == 0.0 and sold == 0.0 and not has_layers:
        return entity

    if entity.get("bought_usd") is None:
        entity["bought_usd"] = round(bought, 4)
    if entity.get("sold_usd") is None:
        entity["sold_usd"] = round(sold, 4)
    entity["remaining_cost_usd"] = round(bought - sold, 4)
    avg = remaining_cost_average(bought, sold, rem)
    if avg is not None:
        entity["entry_avg"] = avg
        entity["entry_display"] = avg
    return entity


def _inventory_is_flat(qty: float, bought_qty_cycle: float = 0.0) -> bool:
    """True when remaining qty is economically zero (float dust after sells).

    Prod fills often leave ~1e-12..1e-10 after a full exit; a tight absolute
    threshold (1e-12) failed to close real SYN/ASTEROID cycles.
    """
    if qty <= 0:
        return True
    # absolute dust — tiny for any reasonable spot size
    if qty <= 1e-8:
        return True
    # relative dust vs this cycle's bought size
    if bought_qty_cycle > 0 and (qty / bought_qty_cycle) <= 1e-9:
        return True
    return False


def segment_positions_from_fills(
    fills: Sequence[dict],
    *,
    symbol: str,
    market: str = "spot",
) -> List[Dict[str, Any]]:
    """Split fill history into discrete position entities.

    When inventory hits zero after sells, that cycle CLOSES as its own trade
    (success/miss on realized PnL). Next buy starts a new position.
    Returns newest-first.
    """
    all_fills = _all_fills_chronological(fills, symbol=symbol, market=market)
    positions: List[Dict[str, Any]] = []
    qty = 0.0
    cost = 0.0
    cycle_buys: List[dict] = []
    cycle_sells: List[dict] = []
    opened_at: Optional[float] = None
    realized_quote = 0.0  # sell proceeds this cycle
    sold_qty_cycle = 0.0
    bought_qty_cycle = 0.0
    bought_cost_cycle = 0.0  # total buy notional this cycle (for full-cycle avg)

    def _close_cycle(closed_at: float) -> None:
        nonlocal qty, cost, cycle_buys, cycle_sells, opened_at
        nonlocal realized_quote, sold_qty_cycle, bought_qty_cycle, bought_cost_cycle
        if not cycle_buys and not cycle_sells:
            return
        entry_avg = (
            (bought_cost_cycle / bought_qty_cycle) if bought_qty_cycle > 1e-12 else None
        )
        exit_avg = (
            (realized_quote / sold_qty_cycle) if sold_qty_cycle > 1e-12 else None
        )
        pnl_pct = None
        pnl_usd = None
        if entry_avg and exit_avg and entry_avg > 0 and sold_qty_cycle > 0:
            # realized on closed portion: sell proceeds - cost basis of sold qty
            # cost basis of sold = entry_avg * sold (avg-cost assumption for full close)
            cost_basis = entry_avg * sold_qty_cycle
            pnl_usd = realized_quote - cost_basis
            pnl_pct = (exit_avg - entry_avg) / entry_avg * 100.0
        hold_s = None
        if opened_at is not None:
            hold_s = max(0.0, closed_at - opened_at)
        outcome = "flat"
        if pnl_pct is not None:
            if pnl_pct > 0.5:
                outcome = "success"
            elif pnl_pct < -0.5:
                outcome = "miss"
        bought_usd = round(bought_cost_cycle, 4) if bought_cost_cycle else 0.0
        sold_usd = round(realized_quote, 4) if realized_quote else 0.0
        positions.append(
            {
                "symbol": symbol,
                "market": market,
                "status": "closed",
                "opened_at": opened_at,
                "closed_at": closed_at,
                "hold_seconds": hold_s,
                "hold_hours": round(hold_s / 3600.0, 2) if hold_s is not None else None,
                "entry_avg": entry_avg,
                "exit_avg": exit_avg,
                "entry_display": entry_avg,
                "size_remaining": 0.0,
                "size_qty": bought_qty_cycle,
                "size_sold": sold_qty_cycle,
                "bought_usd": bought_usd,
                "sold_usd": sold_usd,
                "remaining_cost_usd": 0.0,
                "remaining_mark_usd": 0.0,
                "principal_recovered": bool(
                    bought_usd > 0 and sold_usd + 1e-6 >= bought_usd
                ),
                "buy_orders": list(cycle_buys),
                "sell_orders": list(cycle_sells),
                "n_buys": len(cycle_buys),
                "n_sells": len(cycle_sells),
                "realized_pnl_pct": round(pnl_pct, 3) if pnl_pct is not None else None,
                "realized_pnl_usd": round(pnl_usd, 4) if pnl_usd is not None else None,
                "outcome": outcome,
                "is_open": False,
                "recon_from_fills": True,
                "entity_key": f"{symbol}:{int(opened_at or 0)}-{int(closed_at)}",
            }
        )
        cycle_buys = []
        cycle_sells = []
        opened_at = None
        realized_quote = 0.0
        sold_qty_cycle = 0.0
        bought_qty_cycle = 0.0
        bought_cost_cycle = 0.0
        qty = 0.0
        cost = 0.0

    for f in all_fills:
        q = _f(f.get("qty")) or 0.0
        p = _f(f.get("price")) or 0.0
        ts = _f(f.get("ts")) or 0.0
        if q <= 0 or p <= 0:
            continue
        side = (f.get("side") or "").lower()
        if side == "buy":
            if _inventory_is_flat(qty, bought_qty_cycle):
                # new cycle (also snaps residual dust to zero)
                qty = 0.0
                cost = 0.0
                opened_at = ts
                cycle_buys = []
                cycle_sells = []
                realized_quote = 0.0
                sold_qty_cycle = 0.0
                bought_qty_cycle = 0.0
                bought_cost_cycle = 0.0
            cost += p * q
            qty += q
            bought_qty_cycle += q
            bought_cost_cycle += p * q
            cycle_buys.append(f)
        elif side == "sell":
            if _inventory_is_flat(qty, bought_qty_cycle):
                # sell with no inventory — orphan (incomplete history)
                continue
            sell_q = min(qty, q)
            avg = cost / qty if qty > 0 else p
            cost -= avg * sell_q
            qty -= sell_q
            # record full sell fill on this cycle (display)
            cycle_sells.append(f)
            realized_quote += p * sell_q
            sold_qty_cycle += sell_q
            if _inventory_is_flat(qty, bought_qty_cycle):
                _close_cycle(ts)

    # open remainder (ignore dust)
    if not _inventory_is_flat(qty, bought_qty_cycle) and cycle_buys:
        # Remaining-cost avg: (bought USD − sold USD) / leftover qty.
        # Frozen inventory avg (sell-at-then-avg) is NOT the user-visible entry.
        remaining_cost_raw = bought_cost_cycle - realized_quote
        entry_avg = remaining_cost_average(bought_cost_cycle, realized_quote, qty)
        entry_avg_full = (
            (bought_cost_cycle / bought_qty_cycle) if bought_qty_cycle > 1e-12 else None
        )
        bought_usd = round(bought_cost_cycle, 4) if bought_cost_cycle else 0.0
        sold_usd = round(realized_quote, 4) if realized_quote else 0.0
        remaining_cost = round(remaining_cost_raw, 4)
        # Partial realized on sells so far (avg-cost of sold vs sell proceeds)
        pnl_usd = None
        pnl_pct = None
        if entry_avg_full and entry_avg_full > 0 and sold_qty_cycle > 0:
            cost_basis_sold = entry_avg_full * sold_qty_cycle
            pnl_usd = sold_usd - cost_basis_sold
            exit_avg = sold_usd / sold_qty_cycle if sold_qty_cycle > 0 else None
            if exit_avg is not None:
                pnl_pct = (exit_avg - entry_avg_full) / entry_avg_full * 100.0
        principal_recovered = bool(
            bought_usd > 0 and sold_usd + max(1.0, 0.005 * bought_usd) >= bought_usd
        )
        positions.append(
            {
                "symbol": symbol,
                "market": market,
                "status": "open",
                "opened_at": opened_at,
                "closed_at": None,
                "hold_seconds": (time.time() - opened_at) if opened_at else None,
                "hold_hours": (
                    round((time.time() - opened_at) / 3600.0, 2) if opened_at else None
                ),
                "entry_avg": entry_avg,
                "exit_avg": None,
                "entry_display": entry_avg,
                "size_remaining": qty,
                "size_qty": bought_qty_cycle,
                "size_sold": sold_qty_cycle,
                "bought_usd": bought_usd,
                "sold_usd": sold_usd,
                "remaining_cost_usd": remaining_cost,
                "remaining_mark_usd": None,  # filled when mark available
                "principal_recovered": principal_recovered,
                "buy_orders": list(cycle_buys),
                "sell_orders": list(cycle_sells),
                "n_buys": len(cycle_buys),
                "n_sells": len(cycle_sells),
                "realized_pnl_pct": round(pnl_pct, 3) if pnl_pct is not None else None,
                "realized_pnl_usd": round(pnl_usd, 4) if pnl_usd is not None else None,
                "outcome": "open",
                "is_open": True,
                "recon_from_fills": True,
                "entity_key": f"{symbol}:open:{int(opened_at or 0)}",
            }
        )
    elif cycle_buys and _inventory_is_flat(qty, bought_qty_cycle):
        # dust left after last sell — treat as closed at last sell time
        last_ts = 0.0
        for s in cycle_sells:
            last_ts = max(last_ts, _f(s.get("ts")) or 0.0)
        if last_ts > 0:
            _close_cycle(last_ts)

    # newest first
    positions.sort(
        key=lambda x: float(x.get("closed_at") or x.get("opened_at") or 0),
        reverse=True,
    )
    return positions


def reconstruct_open_from_fills(
    fills: Sequence[dict],
    *,
    symbol: str,
    market: str = "spot",
) -> Dict[str, Any]:
    """Current open inventory only (latest cycle). Prefer segment_positions_from_fills."""
    segs = segment_positions_from_fills(fills, symbol=symbol, market=market)
    for s in segs:
        if s.get("is_open"):
            return {
                "symbol": symbol,
                "market": market,
                "size_remaining": s.get("size_remaining") or 0.0,
                "entry_avg": s.get("entry_avg"),
                "buy_orders": s.get("buy_orders") or [],
                "sell_orders": s.get("sell_orders") or [],
                "n_buys": s.get("n_buys") or 0,
                "n_sells": s.get("n_sells") or 0,
                "first_open_ts": s.get("opened_at"),
                "last_fill_ts": (s.get("buy_orders") or s.get("sell_orders") or [{}])[
                    -1
                ].get("ts")
                if (s.get("buy_orders") or s.get("sell_orders"))
                else None,
                "total_bought_qty": s.get("size_qty"),
                "total_sold_qty": s.get("size_sold"),
                "is_open": True,
            }
    return {
        "symbol": symbol,
        "market": market,
        "size_remaining": 0.0,
        "entry_avg": None,
        "buy_orders": [],
        "sell_orders": [],
        "n_buys": 0,
        "n_sells": 0,
        "first_open_ts": None,
        "last_fill_ts": None,
        "total_bought_qty": 0.0,
        "total_sold_qty": 0.0,
        "is_open": False,
    }


def link_events(
    events: Sequence[dict],
    *,
    symbol: str,
    market: str,
    opened_at: Optional[float],
    window_before: float = 3600,
    window_after: float = 1800,
) -> List[dict]:
    """Fires on same book near trade open."""
    o = _f(opened_at)
    if o is None:
        return []
    out: List[dict] = []
    for e in events:
        if not symbols_match(symbol, e.get("symbol") or ""):
            continue
        em = (e.get("market") or "").lower()
        if market and em and em != market.lower():
            continue
        ets = _f(e.get("ts"))
        if ets is None:
            continue
        if o - window_before <= ets <= o + window_after:
            out.append(
                {
                    "id": e.get("id"),
                    "ts": ets,
                    "price": e.get("price"),
                    "drop_pct": e.get("drop_pct"),
                    "velocity_band": e.get("velocity_band"),
                    "mode": e.get("mode"),
                    "source": e.get("source"),
                    "last_action": e.get("last_action"),
                }
            )
    out.sort(key=lambda x: abs((x.get("ts") or 0) - o))
    return out[:5]


def build_trade_dossier(
    trade: dict,
    *,
    fills: Sequence[dict],
    events: Sequence[dict],
) -> dict:
    """One open or closed trade → learning dossier."""
    opened = trade.get("opened_at")
    closed = trade.get("closed_at")
    hold = _hold_seconds(opened, closed)
    entry = _f(trade.get("entry_avg"))
    exit_ = _f(trade.get("exit_avg"))
    pnl = _pnl_pct(entry, exit_) if trade.get("status") == "closed" else None
    layers = fills_for_trade(
        fills,
        symbol=str(trade.get("symbol") or ""),
        market=str(trade.get("market") or ""),
        opened_at=_f(opened),
        closed_at=_f(closed),
    )
    # If no fills, synthesize single layer from journal avgs
    if not layers["buys"] and entry is not None:
        layers["buys"] = [
            {
                "price": entry,
                "qty": None,
                "ts": _f(opened),
                "side": "buy",
                "synthetic": True,
            }
        ]
    if trade.get("status") == "closed" and not layers["sells"] and exit_ is not None:
        layers["sells"] = [
            {
                "price": exit_,
                "qty": None,
                "ts": _f(closed),
                "side": "sell",
                "synthetic": True,
            }
        ]
    linked = link_events(
        events,
        symbol=str(trade.get("symbol") or ""),
        market=str(trade.get("market") or ""),
        opened_at=_f(opened),
    )
    hold_h = round(hold / 3600.0, 2) if hold is not None else None
    # Realized $ when we have fill qtys (long: sells - buys quote)
    buy_notional = 0.0
    buy_qty = 0.0
    sell_notional = 0.0
    sell_qty = 0.0
    for b in layers["buys"]:
        p, q = _f(b.get("price")), _f(b.get("qty"))
        if p is not None and q is not None:
            buy_notional += p * q
            buy_qty += q
    for s in layers["sells"]:
        p, q = _f(s.get("price")), _f(s.get("qty"))
        if p is not None and q is not None:
            sell_notional += p * q
            sell_qty += q
    pnl_usd = None
    if trade.get("status") == "closed" and buy_notional > 0 and sell_notional > 0:
        pnl_usd = round(sell_notional - buy_notional, 4)
    return {
        "id": trade.get("id"),
        "symbol": trade.get("symbol"),
        "market": trade.get("market"),
        "status": trade.get("status"),
        "entry_avg": entry,
        "exit_avg": exit_,
        "opened_at": _f(opened),
        "closed_at": _f(closed),
        "hold_seconds": hold,
        "hold_hours": hold_h,
        "pnl_pct": round(pnl, 3) if pnl is not None else None,
        "pnl_usd": pnl_usd,
        "buy_qty": buy_qty or None,
        "sell_qty": sell_qty or None,
        "notes": trade.get("notes"),
        "buy_layers": layers["buys"],
        "sell_layers": layers["sells"],
        "n_buys": len(layers["buys"]),
        "n_sells": len(layers["sells"]),
        "linked_events": linked,
        "primary_event_id": linked[0]["id"] if linked else None,
    }


def list_trade_dossiers(
    store: EventStore,
    user_id: int,
    *,
    closed_only: bool = False,
    open_only: bool = False,
    symbol: Optional[str] = None,
    limit: int = 40,
) -> List[dict]:
    if open_only:
        trades = store.journal_list(user_id, open_only=True)
    else:
        trades = store.journal_list(user_id, open_only=False)
    if closed_only:
        trades = [t for t in trades if t.get("status") == "closed"]
    if symbol:
        trades = [t for t in trades if symbols_match(symbol, t.get("symbol") or "")]
    trades = trades[: max(1, min(int(limit), 100))]
    fills = store.recent_fills(user_id, limit=200)
    events = store.recent_events(user_id, limit=80)
    return [
        build_trade_dossier(t, fills=fills, events=events) for t in trades
    ]


def get_trade_dossier(
    store: EventStore, user_id: int, trade_id: int
) -> Optional[dict]:
    all_t = store.journal_list(user_id, open_only=False)
    trade = next((t for t in all_t if int(t["id"]) == int(trade_id)), None)
    if not trade:
        return None
    fills = store.recent_fills(user_id, limit=200)
    events = store.recent_events(user_id, limit=100)
    return build_trade_dossier(trade, fills=fills, events=events)


def ticker_profile(
    store: EventStore,
    user_id: int,
    symbol: str,
    market: Optional[str] = None,
) -> dict:
    """Per-chart learning summary."""
    events = store.recent_events(user_id, limit=100)
    ev = [
        e
        for e in events
        if symbols_match(symbol, e.get("symbol") or "")
        and (
            not market
            or not e.get("market")
            or (e.get("market") or "").lower() == market.lower()
        )
    ]
    stats = store.stats_for_symbol(user_id, symbol, market) if hasattr(store, "stats_for_symbol") else {}
    # Prefer full learning_stats filtered
    full = store.learning_stats(user_id)
    dossiers = list_trade_dossiers(
        store, user_id, symbol=symbol, limit=30
    )
    if market:
        dossiers = [
            d
            for d in dossiers
            if not d.get("market") or d["market"].lower() == market.lower()
        ]
    closed = [d for d in dossiers if d.get("status") == "closed"]
    pnls = [d["pnl_pct"] for d in closed if d.get("pnl_pct") is not None]
    holds = [d["hold_hours"] for d in closed if d.get("hold_hours") is not None]
    wins = sum(1 for p in pnls if p > 0)
    lessons = [
        les
        for les in store.list_lessons(user_id, approved_only=True, limit=40)
        if symbol.upper().replace("_", "")
        in (les.get("text") or "").upper().replace("_", "")
        or symbol.upper() in (les.get("tags_json") or "").upper()
    ]
    took = sum(1 for e in ev if e.get("last_action") == "took")
    skip = sum(1 for e in ev if e.get("last_action") == "skip")
    late = sum(1 for e in ev if e.get("last_action") == "late")
    panic = sum(1 for e in ev if e.get("velocity_band") == "PANIC")
    bounces = [e.get("outcome_bounce") for e in ev if e.get("outcome_bounce") is not None]
    try:
        bounces_f = sorted(float(b) for b in bounces)
        med_b = bounces_f[len(bounces_f) // 2] if bounces_f else None
    except (TypeError, ValueError):
        med_b = None
    return {
        "symbol": symbol,
        "market": market,
        "fires": len(ev),
        "took": took,
        "skip": skip,
        "late": late,
        "panic_band": panic,
        "median_outcome_bounce_pct": med_b,
        "trades": len(dossiers),
        "closed_trades": len(closed),
        "win_rate": round(wins / len(pnls), 3) if pnls else None,
        "avg_pnl_pct": round(sum(pnls) / len(pnls), 3) if pnls else None,
        "avg_hold_hours": round(sum(holds) / len(holds), 2) if holds else None,
        "recent_trades": closed[:8],
        "recent_fires": ev[:12],
        "lessons": lessons[:8],
        "global_stats_note": {
            "user_events": full.get("events"),
            "user_took": full.get("took"),
        },
        "stats_for_symbol": stats,
    }


def list_active_tickers(store: EventStore, user_id: int, limit: int = 30) -> List[dict]:
    """Symbols with fires or trades, ranked by recency."""
    events = store.recent_events(user_id, limit=80)
    trades = store.journal_list(user_id, open_only=False)
    score: Dict[str, dict] = {}
    for e in events:
        sym = e.get("symbol") or ""
        key = EventStore._norm_symbol(sym)
        if not key:
            continue
        row = score.setdefault(
            key,
            {
                "symbol": sym,
                "market": e.get("market"),
                "fires": 0,
                "trades": 0,
                "last_ts": 0.0,
            },
        )
        row["fires"] += 1
        row["last_ts"] = max(row["last_ts"], float(e.get("ts") or 0))
        row["market"] = row.get("market") or e.get("market")
    for t in trades:
        sym = t.get("symbol") or ""
        key = EventStore._norm_symbol(sym)
        if not key:
            continue
        row = score.setdefault(
            key,
            {
                "symbol": sym,
                "market": t.get("market"),
                "fires": 0,
                "trades": 0,
                "last_ts": 0.0,
            },
        )
        row["trades"] += 1
        ts = float(t.get("closed_at") or t.get("opened_at") or 0)
        row["last_ts"] = max(row["last_ts"], ts)
        row["symbol"] = sym or row["symbol"]
    ranked = sorted(score.values(), key=lambda r: -r["last_ts"])
    return ranked[: max(1, min(int(limit), 50))]


def enrich_pending_row(store: EventStore, row: dict) -> dict:
    """Attach fire context for UI cards."""
    out = dict(row)
    eid = row.get("event_id")
    payload = {}
    if row.get("payload_json"):
        try:
            import json

            payload = json.loads(row["payload_json"]) or {}
        except Exception:
            payload = {}
    out["payload"] = payload
    if eid:
        with store._lock:
            er = store._get_conn().execute(
                """
                SELECT id, symbol, market, ts, price, ref_price, drop_pct,
                       velocity_band, mode, source, heat_breadth
                FROM learning_events WHERE id = ?
                """,
                (int(eid),),
            ).fetchone()
        if er:
            ev = dict(er)
            out["event"] = ev
            out["symbol"] = out.get("symbol") or ev.get("symbol")
            out["market"] = ev.get("market")
            out["fire_ts"] = ev.get("ts")
            out["fire_price"] = ev.get("price")
            out["ref_price"] = ev.get("ref_price")
            out["drop_pct"] = ev.get("drop_pct")
            out["velocity_band"] = ev.get("velocity_band")
            out["mode"] = ev.get("mode")
            out["source"] = ev.get("source")
    inf = payload.get("inference") or {}
    out["inferred_action"] = inf.get("action")
    out["inferred_confidence"] = inf.get("confidence")
    out["inferred_reason"] = inf.get("reason") or payload.get("reason")
    out["inferred_source"] = inf.get("source")
    return out


def candle_features_soft(
    market: str,
    symbol: str,
    *,
    around_ts: Optional[float] = None,
) -> Dict[str, Any]:
    """Best-effort public kline features. Never raises."""
    try:
        from ..movers.klines import KlineClient

        client = KlineClient()
        reds = client.consecutive_reds(market or "futures", symbol, ["5m", "15m", "1h"])
        client.close()
        return {
            "ok": True,
            "consecutive_reds": reds,
            "around_ts": around_ts,
            "note": "soft features — not a full OHLC model",
        }
    except Exception as e:
        logger.debug("candle_features_soft: %s", e)
        return {"ok": False, "error": str(e)[:120]}
