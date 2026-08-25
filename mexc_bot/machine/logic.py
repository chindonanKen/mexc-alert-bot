"""Machine gates and layer math. Pure functions — no SQLite writes."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .settings import (
    DEFAULT_LAYER_COUNT,
    DEFAULT_REDS_REQUIRED,
    EQUITY_USD,
    MAX_LIVE_PLAYS,
    MAX_PER_PLAY_USD,
    NEWS_KILL_CLASSES,
    PANIC_BREADTH_MIN,
    bounce_seconds,
    tf_slow_rank,
)

_RUMOR = re.compile(
    r"\b(rumou?r(s)?|allegedly|hearsay|gossip|unconfirmed chatter)\b",
    re.I,
)


def first_candle_sitout(
    reds: Optional[int],
    *,
    heat_breadth: Optional[int] = None,
    panic_board: bool = False,
) -> bool:
    """Sit out the first red of an isolated dump.

    Board-wide panic is the exception. A higher TF still on its first
    candle does not block a different TF that already meets the rules.
    """
    if reds is None:
        return False
    try:
        n = int(reds)
    except (TypeError, ValueError):
        return False
    if n != 1:
        return False
    if panic_board:
        return False
    if heat_breadth is not None and int(heat_breadth) >= PANIC_BREADTH_MIN:
        return False
    return True


def is_rumor(hit: Dict[str, Any]) -> bool:
    blob = " ".join(
        str(hit.get(k) or "")
        for k in ("title", "kind", "class", "severity", "source", "note")
    )
    if _RUMOR.search(blob):
        return True
    kind = str(hit.get("kind") or "").lower()
    if kind in {"rumor", "rumour", "gossip"}:
        return True
    return False


def news_kill(hits: Optional[Iterable[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    """Delist/scam (fatal news) kills even later reds. Rumors are not news."""
    for raw in hits or []:
        if not isinstance(raw, dict):
            continue
        if is_rumor(raw):
            continue
        cls = str(raw.get("class") or raw.get("kind") or "").upper()
        sev = str(raw.get("severity") or "").lower()
        if cls not in NEWS_KILL_CLASSES:
            continue
        if sev in {"unconfirmed", "rumor", "rumour"}:
            continue
        return {"kill": True, "hit": raw, "class": cls}
    return None


def reds_required(habit_reds: Optional[int]) -> int:
    """3+ default until a per-symbol per-TF habit exists."""
    if habit_reds is None:
        return DEFAULT_REDS_REQUIRED
    try:
        n = int(habit_reds)
    except (TypeError, ValueError):
        return DEFAULT_REDS_REQUIRED
    return n if n > 0 else DEFAULT_REDS_REQUIRED


def tf_meets_rules(
    *,
    tf: str,
    reds: Optional[int],
    habit_reds: Optional[int] = None,
    ad_known: bool = False,
    heat_breadth: Optional[int] = None,
    panic_board: bool = False,
    news_hits: Optional[Iterable[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Does this one TF meet play rules (independent of other TFs)."""
    kill = news_kill(news_hits)
    sitout = first_candle_sitout(
        reds, heat_breadth=heat_breadth, panic_board=panic_board
    )
    need = reds_required(habit_reds)
    try:
        n = int(reds) if reds is not None else None
    except (TypeError, ValueError):
        n = None
    reds_ok = n is not None and n >= need
    complete = bool(
        ad_known and reds_ok and not sitout and kill is None
    )
    return {
        "tf": tf,
        "complete": complete,
        "ad_known": bool(ad_known),
        "reds": n,
        "reds_required": need,
        "reds_ok": reds_ok,
        "first_candle_sitout": sitout,
        "news_kill": kill is not None,
        "news": kill,
    }


def pick_working_tf(
    tf_states: Sequence[Dict[str, Any]],
    *,
    respected: Optional[Dict[str, float]] = None,
    locked_tf: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """One complete TF can play even if a higher TF is still first-candle.

    If two (or more) TFs complete: pick the one this range respected.
    Slower if tied. Never average two ADs.
    """
    complete = [dict(s) for s in tf_states if s and s.get("complete")]
    if not complete:
        return None
    if len(complete) == 1:
        chosen = complete[0]
        chosen["pick_reason"] = "one_tf_complete"
        return chosen

    scores: Dict[str, float] = {}
    respected = respected or {}
    for s in complete:
        tf = str(s.get("tf") or "")
        score = float(respected.get(tf) or 0.0)
        if locked_tf and tf == locked_tf:
            score += 10.0
        scores[tf] = score
        s["respected_score"] = score

    best = max(scores.values())
    tied = [s for s in complete if float(s.get("respected_score") or 0) == best]
    if len(tied) == 1:
        chosen = tied[0]
        chosen["pick_reason"] = "range_respected"
        return chosen
    tied.sort(key=lambda s: tf_slow_rank(str(s.get("tf") or "")), reverse=True)
    chosen = tied[0]
    chosen["pick_reason"] = "tie_slower"
    return chosen


def exponential_layers(
    ad_top: Optional[float],
    ad_bottom: Optional[float],
    *,
    count: int = DEFAULT_LAYER_COUNT,
    budget_usd: float = MAX_PER_PLAY_USD,
    zone_prices: Optional[Sequence[float]] = None,
) -> List[Dict[str, Any]]:
    """Layer toward AD bottom. Exponential size (small first, larger deeper)."""
    try:
        top = float(ad_top) if ad_top is not None else None
        bot = float(ad_bottom) if ad_bottom is not None else None
    except (TypeError, ValueError):
        return []
    if top is None or bot is None:
        return []
    if top <= 0 or bot <= 0 or top <= bot:
        return []
    n = max(1, int(count or DEFAULT_LAYER_COUNT))
    budget = max(0.0, min(float(budget_usd), MAX_PER_PLAY_USD))
    if budget <= 0:
        return []

    prices: List[float] = []
    if zone_prices:
        zs = sorted(
            {float(z) for z in zone_prices if z is not None and float(z) > 0},
            reverse=True,
        )
        zs = [z for z in zs if bot - 1e-12 <= z <= top + 1e-12]
        if zs:
            prices = zs[:n]
    if not prices:
        prices = [top - ((i + 1) / n) * (top - bot) for i in range(n)]

    weights = [2**i for i in range(len(prices))]
    tw = float(sum(weights)) or 1.0
    out: List[Dict[str, Any]] = []
    running = 0.0
    for i, px in enumerate(prices):
        if i == len(prices) - 1:
            usd = round(budget - running, 4)
        else:
            usd = round(budget * weights[i] / tw, 4)
            running += usd
        out.append(
            {
                "idx": i + 1,
                "price": round(float(px), 8),
                "usd": max(0.0, usd),
            }
        )
    return out


def play_budget(live_allocated: float, equity: float = EQUITY_USD) -> float:
    """$100 max into one plan, $200 book, leftover stays in the machine book."""
    room = max(0.0, float(equity) - max(0.0, float(live_allocated)))
    return round(min(MAX_PER_PLAY_USD, room), 4)


def can_open_play(live_count: int, live_allocated: float) -> Dict[str, Any]:
    budget = play_budget(live_allocated)
    ok = int(live_count) < MAX_LIVE_PLAYS and budget > 0
    return {
        "ok": ok,
        "live_count": int(live_count),
        "max_live": MAX_LIVE_PLAYS,
        "budget_usd": budget,
        "equity_usd": EQUITY_USD,
        "reason": (
            None
            if ok
            else (
                "max_2_live_plays"
                if int(live_count) >= MAX_LIVE_PLAYS
                else "no_budget"
            )
        ),
    }


def failed_ad(
    *,
    armed_at: Optional[float],
    now: float,
    tf: Optional[str],
    bounced: bool,
) -> bool:
    if bounced or armed_at is None:
        return False
    return (float(now) - float(armed_at)) >= bounce_seconds(tf)


def price_eq(a: Any, b: Any) -> bool:
    """Match official ticks. Tight — do not treat nearby pixels as the bar."""
    try:
        x, y = float(a), float(b)
    except (TypeError, ValueError):
        return False
    if x == y:
        return True
    return abs(x - y) <= max(1e-12, abs(y) * 1e-8)
