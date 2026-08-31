"""Positions / PnL math: order collapse, remaining-cost, book split.

Spot math is qty × price. Futures math never mixes into a spot row.
Layers are one row per user order (never one row per exchange fill).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .contract_size import resolve_futures_contract_size

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
DISPLAY_AVG_FIELDS = ("entry_avg", "entry_display", "leftover_avg", "remaining_avg")


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


def fill_px_qty_notional(fill: dict) -> float:
    """price × qty (contract notional units). Not leftover-cost cash on futures."""
    px = _sf(fill.get("price")) or 0.0
    qty = _sf(fill.get("qty")) or 0.0
    if px > 0 and qty > 0:
        return px * qty
    qq = _sf(fill.get("quote_qty"))
    return qq if qq is not None and qq > 0 else 0.0


def fill_cash_usd(
    fill: dict, *, book: str = "spot", contract_size: Optional[float] = None
) -> Optional[float]:
    """Leftover-cost dollars. Spot: qty×price. Futures: qty×price×contractSize.

    Futures deal quote_qty in this repo is price×vol (notional). Do not paint
    that as In/Out. Unknown contractSize → None (caller stores 0).
    """
    px = _sf(fill.get("price")) or 0.0
    qty = _sf(fill.get("qty")) or 0.0
    notional = px * qty if px > 0 and qty > 0 else 0.0
    qq = _sf(fill.get("quote_qty"))
    if (book or "spot").lower() != "futures":
        if qq is not None and qq > 0:
            return qq
        return notional

    cs = contract_size if contract_size is not None and contract_size > 0 else None
    if cs is None:
        cs = resolve_futures_contract_size(fill.get("symbol"), fill)
    if cs is None or cs <= 0:
        return None
    if notional > 0:
        if qq is not None and qq > 0:
            # Already cash if quote ≈ notional × cs; else quote is stored notional.
            if abs(qq - notional * cs) <= max(1e-6, 0.02 * notional * cs):
                return qq
            if abs(qq - notional) <= max(1e-6, 0.02 * notional):
                return notional * cs
        return notional * cs
    if qq is not None and qq > 0:
        return qq * cs
    return 0.0


def fill_notional_usd(fill: dict, *, book: str = "spot", contract_size: float = 1.0) -> float:
    """Back-compat: cash when provable, else 0 on futures without size."""
    cash = fill_cash_usd(fill, book=book, contract_size=contract_size or None)
    if cash is not None:
        return cash
    if (book or "spot").lower() == "futures":
        return 0.0
    return fill_px_qty_notional(fill)


def price_key(price: Any) -> Optional[float]:
    """Stable price bucket: one user order at one price."""
    n = _sf(price)
    if n is None or n <= 0:
        return None
    return round(n, 10)


def collapse_fills_to_orders(fills: List[dict]) -> List[dict]:
    """One row per user order at one price (side + price).

    Live fills are often keyed by deal/fill id with no shared orderId.
    Grouping by fill id paints 50 lines for 16 prices. Price+side is the
    user-visible order. Same price, several pieces → one VWAP row.
    """
    groups: Dict[tuple, List[dict]] = {}
    leftovers: List[dict] = []
    for i, fill in enumerate(fills or []):
        if not isinstance(fill, dict):
            continue
        side = str(fill.get("side") or "").upper() or "?"
        pk = price_key(fill.get("price"))
        if pk is None:
            row = dict(fill)
            row["_fully_filled"] = True
            row["_order_id"] = (
                f"legacy-{fill.get('id') or fill.get('exchange_trade_id') or i}"
            )
            row["_fill_count"] = 1
            leftovers.append(row)
            continue
        groups.setdefault((side, pk), []).append(fill)

    out: List[dict] = []
    for (side, pk), parts in groups.items():
        oid = order_id_from_fill(parts[0]) if len(parts) == 1 else ""
        if not oid or any(order_id_from_fill(p) != oid for p in parts):
            oid = f"px:{side}:{pk}"
        out.append(_merge_order_fills(oid, parts))
    out.extend(leftovers)
    out.sort(key=lambda x: float(x.get("ts") or 0))
    return out


def collapse_entity_layers(ent: dict) -> dict:
    """Rewrite buy/sell layers to one row per price. Mutates ``ent``."""
    if not ent:
        return ent
    buys = collapse_fills_to_orders(list(ent.get("buy_orders") or []))
    sells = collapse_fills_to_orders(list(ent.get("sell_orders") or []))
    ent["buy_orders"] = buys
    ent["sell_orders"] = sells
    ent["n_buys"] = len(buys)
    ent["n_sells"] = len(sells)
    return ent


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
    px_qty = 0.0
    for p in parts:
        q = float(p.get("qty") or 0)
        raw = parse_raw(p)
        px = float(
            (raw.get("price") if isinstance(raw, dict) else None)
            or p.get("price")
            or 0
        )
        qq = p.get("quote_qty")
        qty += q
        px_qty += px * q
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
    # VWAP from fill *price*, never cash/qty (that *cs and made AEHR leftover 0.82 vs mark 83).
    vwap = (px_qty / qty) if qty else 0.0
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


def _layers_px_qty(orders: Optional[List[dict]]) -> float:
    tot = 0.0
    for o in orders or []:
        tot += fill_px_qty_notional(o)
    return tot


def _layers_cash(
    orders: Optional[List[dict]], *, book: str = "spot", contract_size: Optional[float] = None
) -> Optional[float]:
    if (book or "spot").lower() != "futures":
        return _layers_px_qty(orders)
    if contract_size is None or contract_size <= 0:
        return None
    tot = 0.0
    for o in orders or []:
        cash = fill_cash_usd(o, book=book, contract_size=contract_size)
        tot += 0.0 if cash is None else cash
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

    n_in = _layers_px_qty(entity.get("buy_orders"))
    n_out = _layers_px_qty(entity.get("sell_orders"))
    if n_in <= 0 and n_out <= 0:
        bought = _sf(entity.get("bought_usd"))
        sold = _sf(entity.get("sold_usd"))
        if bought is None and sold is None:
            return entity
        n_in = bought or 0.0
        n_out = sold or 0.0
    if n_in <= 0 and n_out <= 0:
        return entity

    # Leftover avg is a PRICE: (bought notional − sold notional) / rem qty.
    avg = remaining_cost_average(n_in, n_out, rem)
    if book == "futures":
        hold = _sf(entity.get("hold_avg")) or _sf(entity.get("entry_live"))
        cs_hint = resolve_futures_contract_size(
            entity.get("symbol"), entity, entity.get("contract_size")
        )
        # leftover must be a PRICE in the same units as mark (AEHR ~82, not 0.82).
        if hold and hold > 0 and avg is not None and cs_hint and 0 < cs_hint < 1:
            if abs(avg - hold * cs_hint) <= max(1e-6, 0.05 * abs(hold * cs_hint)):
                avg = hold
    if avg is not None:
        entity["entry_avg"] = avg
        entity["entry_display"] = avg
        entity["leftover_avg"] = avg
        entity["remaining_avg"] = avg

    if book == "futures":
        cs = resolve_futures_contract_size(
            entity.get("symbol"), entity, entity.get("contract_size")
        )
        if cs is None or cs <= 0:
            entity["contract_size_unknown"] = True
            if entity.get("bought_usd") is None:
                entity["bought_usd"] = 0.0
            if entity.get("sold_usd") is None:
                entity["sold_usd"] = 0.0
            entity["remaining_cost_usd"] = 0.0
            return entity
        entity["contract_size_unknown"] = False
        entity["contract_size"] = cs
        cash_in = n_in * cs
        cash_out = n_out * cs
        entity["bought_usd"] = round(cash_in, 4)
        entity["sold_usd"] = round(cash_out, 4)
        entity["remaining_cost_usd"] = round(cash_in - cash_out, 4)
        return entity

    entity["bought_usd"] = round(n_in, 4)
    entity["sold_usd"] = round(n_out, 4)
    entity["remaining_cost_usd"] = round(n_in - n_out, 4)
    return entity


def tag_book(d: dict) -> None:
    market = str(d.get("market") or "spot").lower()
    book = "futures" if market == "futures" else "spot"
    d["book"] = book
    d["math"] = book


def _entry_px(d: dict) -> Optional[float]:
    """Leftover remaining-cost first, including negative leftover (SYN).

    Do not skip n < 0 — that zeroed uPnL $ when leftover avg went below 0.
    Prefer leftover over exchange hold_avg so mark-vs-leftover stays consistent.
    Explicit 0 leftover is a real basis; missing fields stay None.
    """
    for key in (
        "remaining_avg",
        "leftover_avg",
        "entry_display",
        "entry_avg",
        "avg_entry",
        "hold_avg",
    ):
        n = _sf(d.get(key))
        if n is not None and n != 0:
            return n
    for key in ("remaining_avg", "leftover_avg", "entry_display", "entry_avg"):
        n = _sf(d.get(key))
        if n is not None:
            return n
    return None


def _upnl_pct(mark: float, entry: Optional[float]) -> Optional[float]:
    if entry is None or entry == 0:
        return None
    return round((float(mark) - float(entry)) / abs(float(entry)) * 100.0, 3)


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
    """Remaining notional / uPnL. Spot: qty×price. Futures: qty×cs×mark.

    uPnL $ is mark vs leftover avg, including negative leftover (SYN).
    Leftover remaining_avg formula is unchanged: (bought − sold) / rem.
    """
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
        if mark is None and exch_upnl not in (None, "") and rem > 0 and cs > 0 and entry is not None:
            derived = entry + side * float(exch_upnl) / (rem * cs)
            d["mark_price"] = derived
            d["mark"] = derived
            if not d.get("mark_source"):
                d["mark_source"] = "mexc_position"
            mark = derived
        if mark is not None and entry is not None:
            mark_f = float(mark)
            d["upnl_usd_est"] = (mark_f - entry) * rem * cs * side
            d["remaining_mark_usd"] = rem * cs * mark_f
            pct = _upnl_pct(mark_f, entry)
            if pct is not None:
                d["upnl_pct"] = pct
            return
        if mark is not None:
            d["remaining_mark_usd"] = rem * cs * float(mark)
        if exch_upnl not in (None, ""):
            d["upnl_usd_est"] = float(exch_upnl)
            return
        d.setdefault("remaining_mark_usd", 0.0)
        d.setdefault("upnl_usd_est", 0.0)
        return

    d["contract_size"] = 1.0
    if rem <= 0 or mark is None:
        d.setdefault("remaining_mark_usd", 0.0)
        d.setdefault("upnl_usd_est", 0.0)
        return
    mark_f = float(mark)
    d["remaining_mark_usd"] = rem * mark_f
    if entry is not None:
        d["upnl_usd_est"] = (mark_f - entry) * rem
        pct = _upnl_pct(mark_f, entry)
        if pct is not None:
            d["upnl_pct"] = pct
    else:
        d["upnl_usd_est"] = 0.0


def _closed_px_qty(d: dict, *, side: str) -> float:
    qty = _sf(d.get("size_sold")) or _sf(d.get("size_qty")) or 0.0
    if side == "buy":
        px = _sf(d.get("entry_avg")) or _sf(d.get("entry_display")) or 0.0
    else:
        px = _sf(d.get("exit_avg")) or 0.0
    if px > 0 and qty > 0:
        return px * qty
    return 0.0


def ensure_position_display_fields(d: dict) -> dict:
    """P4: every Positions/PnL money · qty · avg cell has a number.

    Futures In/Out are leftover-cost cash (price×qty×contractSize). Raw
    contract×price is notional — never paint it as dollars.
    Leftover avg is a PRICE: (bought notional − sold notional) / rem qty.
    """
    tag_book(d)
    collapse_entity_layers(d)
    is_open = d.get("status") == "open" or d.get("is_open")
    book = d.get("book") or "spot"
    n_in = _layers_px_qty(d.get("buy_orders"))
    n_out = _layers_px_qty(d.get("sell_orders"))
    if n_in <= 0 and not is_open:
        n_in = _closed_px_qty(d, side="buy")
    if n_out <= 0 and not is_open:
        n_out = _closed_px_qty(d, side="sell")
    if n_in <= 0 and book != "futures":
        n_in = _sf(d.get("bought_usd")) or 0.0
    if n_out <= 0 and book != "futures":
        n_out = _sf(d.get("sold_usd")) or 0.0

    cs = None
    if book == "futures":
        cs = resolve_futures_contract_size(d.get("symbol"), d, d.get("contract_size"))
        if cs is not None and cs > 0:
            d["contract_size"] = cs
        if cs is None or cs <= 0:
            d["bought_usd"] = 0.0
            d["sold_usd"] = 0.0
        else:
            d["bought_usd"] = round(n_in * cs, 4)
            d["sold_usd"] = round(n_out * cs, 4)
    else:
        d["bought_usd"] = round(float(n_in or 0.0), 4)
        d["sold_usd"] = round(float(n_out or 0.0), 4)

    rem = _sf(d.get("size_remaining"))
    if rem is None:
        rem = 0.0
    d["size_remaining"] = rem
    if _sf(d.get("size_qty")) is None:
        d["size_qty"] = rem if is_open else (_sf(d.get("size_sold")) or 0.0)
    if _sf(d.get("size_sold")) is None:
        d["size_sold"] = 0.0 if is_open else float(d.get("size_qty") or 0.0)

    has_notional = n_in > 0 or n_out > 0
    leftover_price = remaining_cost_average(n_in, n_out, rem) if is_open else None
    if is_open:
        d["remaining_cost_usd"] = round(float(d["bought_usd"]) - float(d["sold_usd"]), 4)
        if leftover_price is not None:
            d["entry_avg"] = float(leftover_price)
            d["entry_display"] = float(leftover_price)
            d["leftover_avg"] = float(leftover_price)
            d["remaining_avg"] = float(leftover_price)
        else:
            avg = (
                _sf(d.get("remaining_avg"))
                or _sf(d.get("leftover_avg"))
                or _sf(d.get("entry_display"))
                or _sf(d.get("entry_avg"))
                or 0.0
            )
            d["entry_avg"] = float(avg)
            d["entry_display"] = float(avg)
            d["leftover_avg"] = float(avg)
            d["remaining_avg"] = float(avg)
            if not has_notional:
                d["remaining_cost_usd"] = 0.0
    else:
        if _sf(d.get("exit_avg")) is None:
            d["exit_avg"] = 0.0
        d["leftover_avg"] = 0.0
        d["remaining_avg"] = 0.0
        d["remaining_cost_usd"] = 0.0
        if _sf(d.get("entry_avg")) is None:
            d["entry_avg"] = _sf(d.get("entry_display")) or 0.0
        if _sf(d.get("entry_display")) is None:
            d["entry_display"] = float(d.get("entry_avg") or 0.0)
        if _sf(d.get("mark_price")) is None:
            d["mark_price"] = float(d.get("exit_avg") or 0.0)
        d["upnl_usd_est"] = 0.0
        d["upnl_pct"] = 0.0
        d["remaining_mark_usd"] = 0.0

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
    if _sf(d.get("realized_pnl_pct")) is None:
        d["realized_pnl_pct"] = 0.0
    if is_open and _sf(d.get("upnl_pct")) is None:
        d["upnl_pct"] = 0.0
    if _sf(d.get("mark_price")) is None:
        d["mark_price"] = 0.0
    return d
