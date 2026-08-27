"""Smart PnL aggregates for AD Desk — remaining-cost money facts.

P5: full closed history available via All. Window/from/to filter the
displayed closed list only (query filter — never deletes).
P6: same leftover math as Positions. This module does not recompute
remaining_avg / remaining_cost_usd.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from .position_math import ensure_position_display_fields
from .positions_enrich import list_position_entities

try:
    from zoneinfo import ZoneInfo

    MANILA = ZoneInfo("Asia/Manila")
except Exception:  # pragma: no cover
    MANILA = timezone(timedelta(hours=8))


def _f(x: Any) -> Optional[float]:
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _n(x: Any) -> float:
    v = _f(x)
    return 0.0 if v is None else v


def _window_cutoff(window: str) -> Optional[float]:
    """Rolling-seconds fallback when no Manila from/to dates are given."""
    w = (window or "all").lower()
    if w in ("all", "forever", "teach", "custom", ""):
        return None
    import time

    now = time.time()
    if w == "7d":
        return now - 7 * 86400
    if w == "30d":
        return now - 30 * 86400
    if w == "90d":
        return now - 90 * 86400
    return None


def manila_today() -> date:
    return datetime.now(MANILA).date()


def parse_manila_date(raw: Optional[str]) -> Optional[date]:
    if not raw:
        return None
    s = str(raw).strip()[:10]
    if len(s) < 10:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def manila_day_start_ts(d: date) -> float:
    return datetime(d.year, d.month, d.day, tzinfo=MANILA).timestamp()


def manila_day_end_ts(d: date) -> float:
    nxt = d + timedelta(days=1)
    return datetime(nxt.year, nxt.month, nxt.day, tzinfo=MANILA).timestamp()


def chip_date_span(window: str, today: Optional[date] = None) -> Tuple[Optional[date], Optional[date]]:
    """Calendar dates a 7d/30d chip writes. All → (None, None)."""
    w = (window or "all").lower()
    today = today or manila_today()
    if w == "7d":
        return today - timedelta(days=6), today
    if w == "30d":
        return today - timedelta(days=29), today
    if w == "90d":
        return today - timedelta(days=89), today
    return None, None


def display_name(symbol: Any) -> str:
    s = str(symbol or "").upper()
    if not s:
        return ""
    s = s.replace("STOCK", "")
    if s.endswith("_USDT"):
        s = s[: -len("_USDT")]
    elif s.endswith("USDT"):
        s = s[: -len("USDT")]
    return s.replace("_", "") or str(symbol or "")


def book_label(market: Any) -> str:
    m = str(market or "spot").lower()
    return "FUT" if m == "futures" else "SPOT"


def hold_days(opened_at: Any, closed_at: Any, hold_hours: Any = None) -> float:
    o = _f(opened_at)
    c = _f(closed_at)
    if o and c and c >= o:
        return round((c - o) / 86400.0, 2)
    h = _f(hold_hours)
    if h is not None:
        return round(h / 24.0, 2)
    return 0.0


def _manila_dt(ts: Any) -> Optional[datetime]:
    v = _f(ts)
    if v is None or v <= 0:
        return None
    return datetime.fromtimestamp(v, tz=MANILA)


def _week_bounds(d: date) -> Tuple[date, date]:
    monday = d - timedelta(days=d.weekday())
    return monday, monday + timedelta(days=6)


def _month_label(d: date) -> str:
    return d.strftime("%b %Y").upper()


def _week_label(d: date) -> str:
    monday, sunday = _week_bounds(d)
    if monday.month == sunday.month:
        return f"{monday.strftime('%b').upper()} {monday.day}–{sunday.day}"
    return (
        f"{monday.strftime('%b').upper()} {monday.day}–"
        f"{sunday.strftime('%b').upper()} {sunday.day}"
    )


def resolve_group_by(
    window: str,
    from_d: Optional[date],
    to_d: Optional[date],
) -> str:
    """Week for 7d/30d; month for All or a span longer than 30 days."""
    w = (window or "all").lower()
    if w in ("7d", "30d"):
        return "week"
    if from_d and to_d and (to_d - from_d).days > 30:
        return "month"
    if w in ("all", "forever", "teach", ""):
        return "month"
    if from_d and to_d:
        return "week" if (to_d - from_d).days <= 30 else "month"
    return "month"


def _closed_ts(e: dict) -> float:
    return _f(e.get("closed_at") or e.get("opened_at")) or 0.0


def in_closed_window(
    e: dict,
    *,
    from_d: Optional[date],
    to_d: Optional[date],
    cutoff: Optional[float],
) -> bool:
    ts = _closed_ts(e)
    if from_d is not None and ts < manila_day_start_ts(from_d):
        return False
    if to_d is not None and ts >= manila_day_end_ts(to_d):
        return False
    if from_d is None and to_d is None and cutoff is not None and ts < cutoff:
        return False
    return True


def _layer_brief(o: dict) -> Dict[str, Any]:
    return {
        "side": o.get("side"),
        "price": _n(o.get("price")),
        "qty": _n(o.get("qty")),
        "quote_qty": _n(o.get("quote_qty")),
        "ts": o.get("ts"),
    }


def _closed_row(e: dict) -> Dict[str, Any]:
    ensure_position_display_fields(e)
    buys = [_layer_brief(o) for o in (e.get("buy_orders") or [])]
    sells = [_layer_brief(o) for o in (e.get("sell_orders") or [])]
    return {
        "symbol": e.get("symbol") or "",
        "name": display_name(e.get("symbol")),
        "market": (e.get("market") or e.get("book") or "spot"),
        "book": e.get("book") or e.get("market") or "spot",
        "book_label": book_label(e.get("book") or e.get("market")),
        "bought_usd": _n(e.get("bought_usd")),
        "sold_usd": _n(e.get("sold_usd")),
        "realized_pnl_usd": _n(e.get("realized_pnl_usd")),
        "realized_pnl_pct": _n(e.get("realized_pnl_pct")),
        "remaining_cost_usd": _n(e.get("remaining_cost_usd")),
        "leftover_avg": _n(e.get("leftover_avg")),
        "remaining_avg": _n(e.get("remaining_avg") or e.get("leftover_avg")),
        "entry_avg": _n(e.get("entry_avg") or e.get("entry_display")),
        "exit_avg": _n(e.get("exit_avg")),
        "size_qty": _n(e.get("size_qty")),
        "size_remaining": _n(e.get("size_remaining")),
        "closed_at": e.get("closed_at"),
        "opened_at": e.get("opened_at"),
        "hold_days": hold_days(e.get("opened_at"), e.get("closed_at"), e.get("hold_hours")),
        "entity_key": e.get("entity_key"),
        "outcome": e.get("outcome"),
        "contract_size": _f(e.get("contract_size")),
        "buy_orders": buys,
        "sell_orders": sells,
    }


def _open_row(e: dict) -> Dict[str, Any]:
    ensure_position_display_fields(e)
    is_hold = bool(e.get("is_hold") or e.get("position_book") == "hold")
    return {
        "symbol": e.get("symbol") or "",
        "name": display_name(e.get("symbol")),
        "market": (e.get("market") or e.get("book") or "spot"),
        "book": e.get("book") or e.get("market") or "spot",
        "book_label": book_label(e.get("book") or e.get("market")),
        "bought_usd": _n(e.get("bought_usd")),
        "sold_usd": _n(e.get("sold_usd")),
        "realized_pnl_usd": _n(e.get("realized_pnl_usd")),
        "remaining_mark_usd": _n(e.get("remaining_mark_usd")),
        "remaining_cost_usd": _n(e.get("remaining_cost_usd")),
        "leftover_avg": _n(e.get("leftover_avg") or e.get("entry_display")),
        "remaining_avg": _n(
            e.get("remaining_avg") or e.get("leftover_avg") or e.get("entry_display")
        ),
        "entry_avg": _n(e.get("entry_avg") or e.get("entry_display")),
        "size_remaining": _n(e.get("size_remaining")),
        "upnl_usd_est": _n(e.get("upnl_usd_est")),
        "free_coins": bool(e.get("free_coins")),
        "free_coins_status": e.get("free_coins_status") or "",
        "principal_recovered": bool(e.get("principal_recovered")),
        "is_hold": is_hold,
        "position_book": e.get("position_book") or ("hold" if is_hold else "ad"),
        "hold_hours": _n(e.get("hold_hours")),
        "entity_key": e.get("entity_key"),
        "money_truth": e.get("money_truth"),
    }


def _open_sort_key(e: dict) -> Tuple[int, float, str]:
    """AD leftover first, then free, then hold. Bigger leftover $ first."""
    if e.get("is_hold") or e.get("position_book") == "hold":
        bucket = 2
    elif e.get("free_coins"):
        bucket = 1
    else:
        bucket = 0
    return (bucket, -_n(e.get("remaining_cost_usd")), str(e.get("symbol") or ""))


def group_closed_rows(rows: List[dict], group_by: str) -> List[Dict[str, Any]]:
    buckets: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for row in rows:
        dt = _manila_dt(row.get("closed_at") or row.get("opened_at"))
        if dt is None:
            key, label = "unknown", "UNKNOWN"
        elif group_by == "week":
            monday, _sunday = _week_bounds(dt.date())
            key = monday.isoformat()
            label = _week_label(dt.date())
        else:
            key = f"{dt.year:04d}-{dt.month:02d}"
            label = _month_label(dt.date())
        if key not in buckets:
            buckets[key] = {
                "key": key,
                "label": label,
                "closed_n": 0,
                "realized_usd": 0.0,
                "in_usd": 0.0,
                "out_usd": 0.0,
                "rows": [],
            }
            order.append(key)
        g = buckets[key]
        g["closed_n"] += 1
        g["realized_usd"] += _n(row.get("realized_pnl_usd"))
        g["in_usd"] += _n(row.get("bought_usd"))
        g["out_usd"] += _n(row.get("sold_usd"))
        g["rows"].append(row)
    order.sort(reverse=True)
    out = []
    for key in order:
        g = buckets[key]
        g["realized_usd"] = round(g["realized_usd"], 2)
        g["in_usd"] = round(g["in_usd"], 2)
        g["out_usd"] = round(g["out_usd"], 2)
        out.append(g)
    return out


def _sum_closed(rows: List[dict]) -> Tuple[float, float, float]:
    real = in_usd = out_usd = 0.0
    for e in rows:
        real += _n(e.get("realized_pnl_usd"))
        in_usd += _n(e.get("bought_usd"))
        out_usd += _n(e.get("sold_usd"))
    return real, in_usd, out_usd


def build_pnl_summary(
    user_id: int,
    *,
    window: str = "all",
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> Dict[str, Any]:
    entities = list_position_entities(user_id, include_closed=True, closed_limit=0)
    from_d = parse_manila_date(from_date)
    to_d = parse_manila_date(to_date)
    cutoff = None
    if from_d is None and to_d is None:
        cutoff = _window_cutoff(window)

    opens = [e for e in entities if e.get("status") == "open"]
    closed = [e for e in entities if e.get("status") == "closed"]
    closed.sort(key=_closed_ts, reverse=True)
    opens.sort(key=_open_sort_key)

    closed_w = [
        e
        for e in closed
        if in_closed_window(e, from_d=from_d, to_d=to_d, cutoff=cutoff)
    ]

    open_mark = sum(_n(e.get("remaining_mark_usd")) for e in opens)
    open_cost = sum(_n(e.get("remaining_cost_usd")) for e in opens)
    open_upnl = sum(_n(e.get("upnl_usd_est")) for e in opens)
    free_bags = [e for e in opens if e.get("free_coins") and not e.get("is_hold")]
    hold_bags = [e for e in opens if e.get("is_hold") or e.get("position_book") == "hold"]
    free_mark = sum(_n(e.get("remaining_mark_usd")) for e in free_bags)
    hold_mark = sum(_n(e.get("remaining_mark_usd")) for e in hold_bags)
    at_risk_mark = max(0.0, open_mark - free_mark - hold_mark)

    realized, in_usd, out_usd = _sum_closed(closed_w)
    win_n = miss_n = flat_n = 0
    win_usd = miss_usd = 0.0
    best = worst = None
    for e in closed_w:
        r = _n(e.get("realized_pnl_usd"))
        if r > 0.5:
            win_n += 1
            win_usd += r
        elif r < -0.5:
            miss_n += 1
            miss_usd += r
        else:
            flat_n += 1
        if best is None or r > best["realized_pnl_usd"]:
            best = {
                "symbol": e.get("symbol"),
                "market": e.get("market"),
                "realized_pnl_usd": r,
            }
        if worst is None or r < worst["realized_pnl_usd"]:
            worst = {
                "symbol": e.get("symbol"),
                "market": e.get("market"),
                "realized_pnl_usd": r,
            }

    spot_r = sum(
        _n(e.get("realized_pnl_usd"))
        for e in closed_w
        if (e.get("market") or "").lower() == "spot"
    )
    fut_r = sum(
        _n(e.get("realized_pnl_usd"))
        for e in closed_w
        if (e.get("market") or "").lower() == "futures"
    )

    closed_rows = [_closed_row(e) for e in closed_w]
    group_by = resolve_group_by(window, from_d, to_d)
    groups = group_closed_rows(closed_rows, group_by)

    eff_from, eff_to = from_d, to_d
    if eff_from is None and eff_to is None:
        chip_from, chip_to = chip_date_span(window)
        if chip_from and chip_to:
            eff_from, eff_to = chip_from, chip_to

    return {
        "window": window or "all",
        "from_date": eff_from.isoformat() if eff_from else None,
        "to_date": eff_to.isoformat() if eff_to else None,
        "timezone": "Asia/Manila",
        "group_by": group_by,
        "cutoff_days": None if cutoff is None else None,
        "history_cutoff": None,
        "bankroll": {
            "open_mark_usd": round(open_mark, 2),
            "at_risk_mark_usd": round(at_risk_mark, 2),
            "open_cost_usd": round(open_cost, 2),
            "open_upnl_usd": round(open_upnl, 2),
            "free_bags_n": len(free_bags),
            "free_mark_usd": round(free_mark, 2),
            "hold_bags_n": len(hold_bags),
            "hold_mark_usd": round(hold_mark, 2),
            "open_n": len(opens),
        },
        "realized": {
            "pnl_usd": round(realized, 2),
            "in_usd": round(in_usd, 2),
            "out_usd": round(out_usd, 2),
            "win_n": win_n,
            "miss_n": miss_n,
            "flat_n": flat_n,
            "win_usd": round(win_usd, 2),
            "miss_usd": round(miss_usd, 2),
            "best": best,
            "worst": worst,
            "closed_n": len(closed_w),
            "closed_all_n": len(closed),
        },
        "by_book": {
            "spot_realized_usd": round(spot_r, 2),
            "futures_realized_usd": round(fut_r, 2),
        },
        "open_book": [_open_row(e) for e in opens],
        "closed_history": closed_rows,
        "closed_groups": groups,
        "free_bags": [
            {
                "symbol": e.get("symbol") or "",
                "remaining_mark_usd": _n(e.get("remaining_mark_usd")),
                "bought_usd": _n(e.get("bought_usd")),
                "sold_usd": _n(e.get("sold_usd")),
                "realized_pnl_usd": _n(e.get("realized_pnl_usd")),
                "remaining_cost_usd": _n(e.get("remaining_cost_usd")),
                "leftover_avg": _n(e.get("leftover_avg") or e.get("entry_display")),
                "remaining_avg": _n(
                    e.get("remaining_avg")
                    or e.get("leftover_avg")
                    or e.get("entry_display")
                ),
                "entity_key": e.get("entity_key"),
            }
            for e in free_bags
        ],
    }
