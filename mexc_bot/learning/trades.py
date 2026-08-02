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
) -> Dict[str, List[dict]]:
    """Split fills into buy/sell layers overlapping the trade window."""
    o = _f(opened_at) or 0.0
    c = _f(closed_at) or (time.time() + 1)
    # slack before open for late-synced fills
    win_start = o - 120
    win_end = c + 120
    buys: List[dict] = []
    sells: List[dict] = []
    for f in fills:
        if not symbols_match(symbol, f.get("symbol") or ""):
            continue
        fm = (f.get("market") or "").lower()
        if market and fm and fm != market.lower():
            # still allow if market blank
            if fm not in ("", market.lower()):
                continue
        ts = _f(f.get("ts")) or 0.0
        if ts < win_start or ts > win_end:
            continue
        side = (f.get("side") or "").lower()
        layer = {
            "price": _f(f.get("price")),
            "qty": _f(f.get("qty")),
            "ts": ts,
            "side": side,
            "exchange_trade_id": f.get("exchange_trade_id"),
        }
        if side in ("buy", "long", "bid"):
            buys.append(layer)
        elif side in ("sell", "short", "ask"):
            sells.append(layer)
    buys.sort(key=lambda x: x.get("ts") or 0)
    sells.sort(key=lambda x: x.get("ts") or 0)
    return {"buys": buys, "sells": sells}


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
