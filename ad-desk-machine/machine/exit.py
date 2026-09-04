"""Exit live-read. Bounce kinds, defensive under-AD, leftover remaining-cost."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

BOUNCE_KINDS = ("GOOD", "WEAK", "FAIL", "TOO_EARLY")


def _f(raw: Any) -> Optional[float]:
    if raw is None or raw == "":
        return None
    try:
        x = float(raw)
    except (TypeError, ValueError):
        return None
    return x


def leftover_remaining_cost(fills: Sequence[Dict[str, Any]]) -> Optional[float]:
    """Remaining-cost of the unsold bag (FIFO). Not a Positions leftover-avg."""
    lots: List[List[float]] = []  # [qty, px]
    for row in fills or []:
        if not isinstance(row, dict):
            continue
        side = str(row.get("side") or "buy").lower()
        px = _f(row.get("filled_price") if row.get("filled_price") is not None else row.get("price"))
        usd = _f(row.get("usd"))
        qty = _f(row.get("qty"))
        if px is None or px <= 0:
            continue
        if qty is None or qty <= 0:
            if usd is None or usd <= 0:
                continue
            qty = usd / px
        if side == "buy":
            lots.append([qty, px])
            continue
        if side != "sell":
            continue
        left = qty
        for lot in lots:
            if left <= 0:
                break
            take = min(lot[0], left)
            lot[0] -= take
            left -= take
    rem_qty = sum(lot[0] for lot in lots if lot[0] > 0)
    rem_cost = sum(lot[0] * lot[1] for lot in lots if lot[0] > 0)
    if rem_qty <= 0:
        return None
    return round(rem_cost / rem_qty, 8)


def leftover_usd(fills: Sequence[Dict[str, Any]]) -> float:
    """Remaining notional of the unsold bag."""
    bought = 0.0
    sold = 0.0
    for row in fills or []:
        if not isinstance(row, dict):
            continue
        try:
            usd = float(row.get("usd") or 0)
        except (TypeError, ValueError):
            continue
        side = str(row.get("side") or "buy").lower()
        if side == "buy":
            bought += usd
        elif side == "sell":
            sold += usd
    return round(max(0.0, bought - sold), 4)


def bounce_kind(play: Dict[str, Any], tape: Dict[str, Any], leftover: Any) -> Optional[str]:
    """GOOD / WEAK / FAIL / TOO_EARLY. None when not in."""
    if not (tape.get("in_play") or play.get("in_play") or leftover):
        if not tape.get("force_bounce_kind"):
            return None
    last = _f(tape.get("current_price") if tape.get("current_price") is not None else tape.get("last"))
    entry = _f(leftover)
    typical = _f(play.get("typical_bounce") or play.get("bounce_run") or tape.get("typical_bounce"))
    candles_need = play.get("candles_to_bounce")
    candles_since = tape.get("candles_since_arm")
    board_panic = bool(tape.get("board_panic") or tape.get("panic_board"))

    if tape.get("grind_not_this_chart"):
        return "FAIL"
    if board_panic and tape.get("weak_first_bounce"):
        return "TOO_EARLY"
    try:
        if (
            candles_need
            and candles_since is not None
            and int(candles_since) < int(candles_need)
            and last is not None
            and entry is not None
            and last > entry
        ):
            return "TOO_EARLY"
    except (TypeError, ValueError):
        pass
    if last is None or entry is None:
        raw = tape.get("bounce_kind")
        return str(raw).upper() if raw in BOUNCE_KINDS or str(raw).upper() in BOUNCE_KINDS else None
    if typical is not None and last >= entry + typical:
        return "GOOD"
    if last > entry:
        return "WEAK"
    return None


def hung_sell_layers(play: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Hung sell layers only. Empty means empty — do not invent."""
    raw = play.get("sell_layers") or []
    out: List[Dict[str, Any]] = []
    for i, row in enumerate(raw, 1):
        if not isinstance(row, dict):
            continue
        px = _f(row.get("price"))
        if px is None or px <= 0:
            continue
        item = dict(row)
        item.setdefault("idx", i)
        item["price"] = px
        try:
            item["pct"] = float(row.get("pct") or row.get("size_pct") or 0)
        except (TypeError, ValueError):
            item["pct"] = 0.0
        out.append(item)
    return out


def defensive_sell_layers(
    sell_layers: Sequence[Dict[str, Any]],
    *,
    ad_bottom: Any,
    new_bottom: Any,
    board_panic: bool = False,
) -> List[Dict[str, Any]]:
    """Past AD and not board-wide panic: lower sells vs the new bottom.

    Do not put sells at the bottom. Board-wide panic keeps the hung sells.
    """
    hung = [dict(x) for x in sell_layers or []]
    if board_panic or not hung:
        return hung
    bot = _f(ad_bottom)
    low = _f(new_bottom)
    if bot is None or low is None or bot <= 0:
        return hung
    if low >= bot:
        return hung
    scale = low / bot
    out: List[Dict[str, Any]] = []
    for row in hung:
        px = _f(row.get("price"))
        if px is None:
            continue
        item = dict(row)
        new_px = px * scale
        floor = low * 1.02
        if new_px <= low:
            new_px = floor
        item["price"] = round(new_px, 8)
        item["defensive"] = True
        out.append(item)
    return out


def exit_decision(play: Dict[str, Any], tape: Dict[str, Any], fills: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Live-read Exit. No invented sells when the hung list is empty."""
    sells = hung_sell_layers(play)
    leftover = leftover_remaining_cost(fills)
    kind = bounce_kind(play, tape, leftover)
    last = _f(tape.get("current_price") if tape.get("current_price") is not None else tape.get("last"))
    past_b = bool(tape.get("past_b"))
    if last is not None and play.get("ad_bottom") is not None:
        try:
            past_b = past_b or float(last) < float(play["ad_bottom"])
        except (TypeError, ValueError):
            pass
    board_panic = bool(tape.get("board_panic") or tape.get("panic_board"))
    if past_b and not board_panic and sells:
        sells = defensive_sell_layers(
            sells,
            ad_bottom=play.get("ad_bottom"),
            new_bottom=last if last is not None else play.get("ad_bottom"),
            board_panic=False,
        )

    sell_now: List[Dict[str, Any]] = []
    if sells and last is not None and kind in {"GOOD", "WEAK", "FAIL"}:
        for row in sells:
            try:
                if float(last) >= float(row["price"]):
                    sell_now.append(dict(row))
            except (TypeError, ValueError, KeyError):
                continue
        if kind == "FAIL" and leftover_usd(fills) > 0 and not sell_now:
            # Fail flatten uses hung sells only — still do not invent prices.
            sell_now = [dict(sells[0])] if sells else []

    return {
        "bounce_kind": kind,
        "sell_layers": sells,
        "sell_now": sell_now,
        "leftover": leftover,
        "invented": False,
        "empty": not bool(sells),
        "defensive": any(x.get("defensive") for x in sells),
    }
