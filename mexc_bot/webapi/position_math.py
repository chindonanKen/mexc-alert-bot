"""Positions / PnL math: order collapse, remaining-cost, book split.

Spot math is qty × price. Futures math never mixes into a spot row.
Layers are one row per user order (never one row per exchange fill).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

_PARTIAL_STATUS = frozenset(
    {
        "NEW",
        "PARTIAL",
        "PARTIALLY_FILLED",
        "PARTIALLYFILLED",
        "PENDING",
        "LIVE",
        "UNFILLED",
        "1",
    }
)
_FILLED_STATUS = frozenset(
    {"FILLED", "CLOSED", "DONE", "2", "COMPLETE", "COMPLETED"}
)

# Fields Kenneth sees on Positions / PnL — never leave these empty.
DISPLAY_MONEY_FIELDS = (
    "bought_usd",
    "sold_usd",
    "remaining_cost_usd",
    "remaining_mark_usd",
    "realized_pnl_usd",
    "upnl_usd_est",
)
DISPLAY_QTY_FIELDS = ("size_remaining", "size_qty", "size_sold")
DISPLAY_AVG_FIELDS = ("entry_avg", "entry_display", "leftover_avg")


def parse_raw(obj: Any) -> Dict[str, Any]:
    if isinstance(obj, dict):
        inner = obj.get("raw")
        if isinstance(inner, dict):
            return inner
        raw_json = obj.get("raw_json")
        if isinstance(raw_json, str) and raw_json.strip():
            try:
                parsed = json.loads(raw_json)
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                return {}
        if inner is None and raw_json is None:
            return obj if _looks_like_exchange_raw(obj) else {}
        return {}
    if isinstance(obj, str) and obj.strip():
        try:
            parsed = json.loads(obj)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _looks_like_exchange_raw(d: dict) -> bool:
    keys = set(d.keys())
    return bool(
        keys
        & {
            "orderId",
            "order_id",
            "origQty",
            "orig_qty",
            "contractSize",
            "contract_size",
            "executedQty",
        }
    )


def _sf(val: Any) -> Optional[float]:
    if val in (None, ""):
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def contract_size_from_raw(raw: Any) -> float:
    """Futures contract multiplier. Missing → 1.0. Never use this on spot."""
    d = raw if isinstance(raw, dict) else parse_raw(raw)
    if not isinstance(d, dict):
        return 1.0
    for key in ("contractSize", "contract_size", "cs", "faceValue", "face_value"):
        val = d.get(key)
        if val in (None, ""):
            continue
        n = _sf(val)
        if n is not None and n > 0:
            return n
    return 1.0


def order_id_from_fill(fill: dict) -> str:
    for key in ("order_id", "orderId", "_order_id"):
        val = fill.get(key)
        if val not in (None, ""):
            return str(val)
    raw = parse_raw(fill)
    for key in ("orderId", "order_id", "ordId"):
        val = raw.get(key)
        if val not in (None, ""):
            return str(val)
    return ""


def fill_notional_usd(fill: dict, *, book: str = "spot", contract_size: float = 1.0) -> float:
    """Layer volume in dollars. Spot: qty×price. Futures: qty×cs×price when quote missing."""
    qq = _sf(fill.get("quote_qty"))
    if qq is not None and qq > 0:
        return qq
    px = _sf(fill.get("price")) or 0.0
    qty = _sf(fill.get("qty")) or 0.0
    if px <= 0 or qty <= 0:
        return 0.0
    cs = contract_size if (book or "spot").lower() == "futures" else 1.0
    if cs <= 0:
        cs = 1.0
    return px * qty * cs


def collapse_fills_to_orders(fills: List[dict]) -> List[dict]:
    """One dict per (order_id, side). Fills with no id stay one-each (legacy)."""
    groups: Dict[tuple, List[dict]] = {}
    singles: List[dict] = []
    for i, fill in enumerate(fills or []):
        if not isinstance(fill, dict):
            continue
        oid = order_id_from_fill(fill)
        side = str(fill.get("side") or "").upper() or "?"
        if not oid:
            row = dict(fill)
            row["_fully_filled"] = True
            row["_order_id"] = f"legacy-{fill.get('id') or fill.get('exchange_trade_id') or i}"
            row["_fill_count"] = 1
            if row.get("quote_qty") in (None, ""):
                px = _sf(row.get("price")) or 0.0
                qty = _sf(row.get("qty")) or 0.0
                row["quote_qty"] = px * qty
            singles.append(row)
            continue
        groups.setdefault((oid, side), []).append(fill)

    out: List[dict] = []
    for (oid, _side), parts in groups.items():
        out.append(_merge_order_fills(oid, parts))
    out.extend(singles)
    out.sort(key=lambda x: float(x.get("ts") or 0))
    return out


def is_order_fully_filled(order: dict) -> bool:
    """True when the order is complete. In-progress partials are False."""
    if order.get("_fully_filled") is True:
        return True
    raw = parse_raw(order)
    status = (
        str(
            order.get("_order_status")
            or order.get("order_status")
            or raw.get("status")
            or raw.get("order_status")
            or raw.get("state")
            or ""
        )
        .upper()
        .replace(" ", "_")
    )
    orig = _sf(
        order.get("_orig_qty")
        or raw.get("origQty")
        or raw.get("orig_qty")
        or raw.get("volume")
    )
    filled = _sf(
        order.get("_filled_qty")
        or order.get("qty")
        or raw.get("executedQty")
        or raw.get("cumExecQty")
        or raw.get("dealVol")
    )
    if status in _FILLED_STATUS:
        return True
    if orig is not None and filled is not None:
        return filled + 1e-9 >= orig
    if status in _PARTIAL_STATUS:
        return False
    return True


def fully_filled_orders(fills: List[dict]) -> List[dict]:
    """Collapse by order_id, then drop in-progress / partial orders."""
    return [o for o in collapse_fills_to_orders(fills) if is_order_fully_filled(o)]


def _merge_order_fills(oid: str, parts: List[dict]) -> dict:
    parts = sorted(parts, key=lambda x: float(x.get("ts") or 0))
    qty = 0.0
    quote = 0.0
    orig: Optional[float] = None
    filled_hint: Optional[float] = None
    status = ""
    raw_acc: Dict[str, Any] = {}
    for p in parts:
        q = float(p.get("qty") or 0)
        px = float(p.get("price") or 0)
        qq = p.get("quote_qty")
        qty += q
        quote += float(qq) if qq not in (None, "") else px * q
        raw = parse_raw(p)
        if raw:
            raw_acc.update(raw)
            o = _sf(raw.get("origQty") or raw.get("orig_qty") or raw.get("volume"))
            if o is not None:
                orig = o
            fv = _sf(
                raw.get("executedQty") or raw.get("cumExecQty") or raw.get("dealVol")
            )
            if fv is not None:
                filled_hint = max(filled_hint or 0.0, fv)
            st = raw.get("status") or raw.get("order_status") or raw.get("state")
            if st not in (None, ""):
                status = str(st)
    vwap = (quote / qty) if qty else 0.0
    first = parts[0]
    raw_acc["orderId"] = oid
    if orig is not None:
        raw_acc["origQty"] = orig
    raw_acc["executedQty"] = filled_hint if filled_hint is not None else qty
    if status:
        raw_acc["status"] = status
    row = dict(first)
    row.update(
        {
            "id": first.get("id"),
            "ts": parts[-1].get("ts") or first.get("ts"),
            "price": vwap,
            "qty": qty,
            "quote_qty": quote,
            "side": first.get("side"),
            "order_id": oid,
            "raw": raw_acc,
            "raw_json": json.dumps(raw_acc) if raw_acc else first.get("raw_json"),
            "_order_id": oid,
            "_fill_count": len(parts),
            "_orig_qty": orig,
            "_filled_qty": filled_hint if filled_hint is not None else qty,
            "_order_status": status,
        }
    )
    return row


def remaining_cost_average(
    bought_usd: Any, sold_usd: Any, remaining_qty: Any
) -> Optional[float]:
    """Open leftover: (bought USD − sold USD) / remaining qty.

    Sell above leftover avg → leftover avg goes down.
    Sell below leftover avg → leftover avg goes up.
    None when remaining qty is not positive (no divide by zero).
    """
    rem = _sf(remaining_qty)
    if rem is None or rem <= 1e-12:
        return None
    bought = _sf(bought_usd) or 0.0
    sold = _sf(sold_usd) or 0.0
    return (bought - sold) / rem


def _layers_notional(orders: Optional[List[dict]], *, book: str = "spot") -> float:
    tot = 0.0
    for o in orders or []:
        tot += fill_notional_usd(o, book=book)
    return tot


def apply_open_remaining_cost_avg(entity: dict) -> dict:
    """Set user-visible open avg / leftover from bought − sold.

    Closed or flat leftover: no-op. No buy/sell dollars and no layers: leave
    existing entry_avg (exchange hold avg or journal). Spot and futures use
    the same leftover formula on their own book — never mixed.
    """
    if not entity:
        return entity
    if not (entity.get("status") == "open" or entity.get("is_open")):
        return entity
    rem = _sf(entity.get("size_remaining"))
    if rem is None or rem <= 1e-12:
        return entity

    book = str(entity.get("book") or entity.get("market") or "spot").lower()
    if book not in ("spot", "futures"):
        book = "spot"
    has_layers = bool(entity.get("buy_orders") or entity.get("sell_orders"))
    has_money = (
        entity.get("bought_usd") is not None or entity.get("sold_usd") is not None
    )
    if not has_money and not has_layers:
        return entity

    bought = _sf(entity.get("bought_usd"))
    sold = _sf(entity.get("sold_usd"))
    if bought is None:
        bought = _layers_notional(entity.get("buy_orders"), book=book)
    if sold is None:
        sold = _layers_notional(entity.get("sell_orders"), book=book)
    bought = bought or 0.0
    sold = sold or 0.0
    if bought == 0.0 and sold == 0.0:
        return entity

    if entity.get("bought_usd") is None:
        entity["bought_usd"] = round(bought, 4)
    if entity.get("sold_usd") is None:
        entity["sold_usd"] = round(sold, 4)
    leftover = bought - sold
    entity["remaining_cost_usd"] = round(leftover, 4)
    avg = remaining_cost_average(bought, sold, rem)
    if avg is not None:
        entity["entry_avg"] = avg
        entity["entry_display"] = avg
        entity["leftover_avg"] = avg
    return entity


def tag_book(d: dict) -> None:
    market = str(d.get("market") or "spot").lower()
    book = "futures" if market == "futures" else "spot"
    d["book"] = book
    d["math"] = book


def _entry_px(d: dict) -> float:
    for key in ("entry_display", "leftover_avg", "avg_entry", "entry_avg", "hold_avg"):
        n = _sf(d.get(key))
        if n is not None and n > 0:
            return n
    return 0.0


def _mark_px(d: dict) -> Optional[float]:
    if d.get("mark_price") is not None:
        return _sf(d.get("mark_price"))
    if d.get("mark") is not None:
        return _sf(d.get("mark"))
    return None


def _is_short(d: dict) -> bool:
    if d.get("position_type") == 2:
        return True
    return str(d.get("position_side") or d.get("side") or "long").lower() == "short"


def apply_open_mark_math(d: dict) -> None:
    """Remaining notional / uPnL. Spot: qty×price. Futures: qty×cs×mark."""
    tag_book(d)
    rem = float(d.get("size_remaining") or d.get("remaining_qty") or 0)
    entry = _entry_px(d)
    mark = _mark_px(d)
    raw = d.get("raw") if isinstance(d.get("raw"), dict) else parse_raw(d)
    short = _is_short(d)
    side = -1.0 if short else 1.0

    if d["book"] == "futures":
        cs = float(d.get("contract_size") or 0) or contract_size_from_raw(raw) or 1.0
        d["contract_size"] = cs
        if rem <= 0:
            d.setdefault("remaining_mark_usd", 0.0)
            d.setdefault("upnl_usd_est", 0.0)
            return
        exch_upnl = d.get("unrealized_pnl")
        if exch_upnl not in (None, ""):
            upnl = float(exch_upnl)
            d["upnl_usd_est"] = upnl
            if mark is None and rem > 0 and cs > 0 and entry > 0:
                derived = entry + side * upnl / (rem * cs)
                d["mark_price"] = derived
                d["mark"] = derived
                if not d.get("mark_source"):
                    d["mark_source"] = "mexc_position"
                mark = derived
            if mark is not None:
                d["remaining_mark_usd"] = rem * cs * float(mark)
            if mark is not None and entry > 0:
                d["upnl_pct"] = round((float(mark) - entry) / entry * 100.0, 3)
            return
        if mark is None or entry <= 0:
            d.setdefault("remaining_mark_usd", 0.0)
            d.setdefault("upnl_usd_est", 0.0)
            return
        mark_f = float(mark)
        d["upnl_usd_est"] = (mark_f - entry) * rem * cs * side
        d["remaining_mark_usd"] = rem * cs * mark_f
        if entry > 0:
            d["upnl_pct"] = round((mark_f - entry) / entry * 100.0, 3)
        return

    d["contract_size"] = 1.0
    if rem <= 0 or mark is None:
        d.setdefault("remaining_mark_usd", 0.0)
        d.setdefault("upnl_usd_est", 0.0)
        return
    mark_f = float(mark)
    d["remaining_mark_usd"] = rem * mark_f
    if entry > 0:
        d["upnl_usd_est"] = (mark_f - entry) * rem
        d["upnl_pct"] = round((mark_f - entry) / entry * 100.0, 3)
    else:
        d["upnl_usd_est"] = 0.0


def ensure_position_display_fields(d: dict) -> dict:
    """P4: every Positions/PnL money · qty · avg cell has a number."""
    tag_book(d)
    is_open = d.get("status") == "open" or d.get("is_open")
    book = d.get("book") or "spot"
    bought = _sf(d.get("bought_usd"))
    sold = _sf(d.get("sold_usd"))
    if bought is None:
        layer_in = _layers_notional(d.get("buy_orders"), book=book)
        if layer_in > 0:
            bought = layer_in
        else:
            qty = _sf(d.get("size_qty")) or _sf(d.get("size_sold")) or 0.0
            px = _sf(d.get("entry_avg")) or _sf(d.get("entry_display")) or 0.0
            cs = 1.0
            if book == "futures":
                cs = float(d.get("contract_size") or 1.0) or 1.0
            bought = px * qty * cs if px > 0 and qty > 0 else 0.0
    if sold is None:
        layer_out = _layers_notional(d.get("sell_orders"), book=book)
        if layer_out > 0:
            sold = layer_out
        else:
            qty = _sf(d.get("size_sold")) or _sf(d.get("size_qty")) or 0.0
            px = _sf(d.get("exit_avg")) or 0.0
            cs = 1.0
            if book == "futures":
                cs = float(d.get("contract_size") or 1.0) or 1.0
            sold = px * qty * cs if px > 0 and qty > 0 else 0.0
    d["bought_usd"] = round(float(bought or 0.0), 4)
    d["sold_usd"] = round(float(sold or 0.0), 4)

    rem = _sf(d.get("size_remaining"))
    if rem is None:
        rem = 0.0
    d["size_remaining"] = rem
    if _sf(d.get("size_qty")) is None:
        d["size_qty"] = rem if is_open else (_sf(d.get("size_sold")) or 0.0)
    if _sf(d.get("size_sold")) is None:
        d["size_sold"] = 0.0 if is_open else float(d.get("size_qty") or 0.0)

    leftover = _sf(d.get("remaining_cost_usd"))
    if leftover is None:
        leftover = (bought - sold) if is_open else 0.0
    d["remaining_cost_usd"] = round(float(leftover), 4)

    avg = _sf(d.get("entry_display"))
    if avg is None:
        avg = _sf(d.get("entry_avg"))
    if avg is None and is_open and rem > 1e-12:
        avg = remaining_cost_average(bought, sold, rem)
    if avg is None:
        avg = 0.0
    d["entry_avg"] = float(avg)
    d["entry_display"] = float(avg)
    if is_open:
        d["leftover_avg"] = float(avg)
    else:
        if _sf(d.get("exit_avg")) is None:
            d["exit_avg"] = 0.0
        d["leftover_avg"] = 0.0

    if _sf(d.get("remaining_mark_usd")) is None:
        d["remaining_mark_usd"] = 0.0
    if _sf(d.get("realized_pnl_usd")) is None:
        if not is_open:
            d["realized_pnl_usd"] = round(
                float(d["sold_usd"]) - float(d["bought_usd"]), 4
            )
        else:
            d["realized_pnl_usd"] = 0.0
    if is_open and _sf(d.get("upnl_usd_est")) is None:
        d["upnl_usd_est"] = 0.0
    if not is_open:
        d["upnl_usd_est"] = 0.0
        d["remaining_mark_usd"] = 0.0
        d["remaining_cost_usd"] = 0.0
    if _sf(d.get("realized_pnl_pct")) is None:
        d["realized_pnl_pct"] = 0.0
    if is_open and _sf(d.get("upnl_pct")) is None:
        d["upnl_pct"] = 0.0
    return d
