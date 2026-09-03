"""Machine gates and layer math. Pure functions — no SQLite writes."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .settings import (
    DEFAULT_LAYER_COUNT,
    EQUITY_USD,
    MAX_LIVE_PLAYS,
    MAX_PER_PLAY_USD,
    NEWS_KILL_CLASSES,
    PANIC_BREADTH_MIN,
    tf_slow_rank,
)

def ad_gap_frac(last: Any, ad_bottom: Any) -> Optional[float]:
    """Relative distance of last to AD bottom. Smaller = closer / through.

    gap/last so a $0.01 coin and an $80 stock compare. At or below bottom → 0.
    None = cannot rank (missing last or bottom).
    """
    try:
        last_f = float(last)
        bot = float(ad_bottom)
    except (TypeError, ValueError):
        return None
    if last_f <= 0:
        return None
    gap = last_f - bot
    if gap <= 0:
        return 0.0
    return gap / last_f


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
    """Sit out the first or second red of an isolated dump.

    Most charts we watch are not clickable yet on red 1 or 2.
    Board-wide panic (panic_board or heat_breadth >= PANIC_BREADTH_MIN)
    is the exception. Sit-out is per-TF: another TF that already meets
    the rules can still play.
    """
    if reds is None:
        return False
    try:
        n = int(reds)
    except (TypeError, ValueError):
        return False
    if n not in (1, 2):
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


def reds_required(habit_reds: Optional[int]) -> Optional[int]:
    """Chart habit is weigh / log only. No default 3. Do not hang 15m ≥ 3."""
    if habit_reds is None:
        return None
    try:
        n = int(habit_reds)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def is_faster_tf(tf: Optional[str], play_tf: Optional[str]) -> bool:
    """True when this TF is faster than the chosen play TF."""
    if not tf or not play_tf:
        return False
    a, b = tf_slow_rank(str(tf)), tf_slow_rank(str(play_tf))
    if a <= 0 or b <= 0:
        return False
    return a < b


def tf_meets_rules(
    *,
    tf: str,
    reds: Optional[int],
    habit_reds: Optional[int] = None,
    ad_known: bool = False,
    heat_breadth: Optional[int] = None,
    panic_board: bool = False,
    news_hits: Optional[Iterable[Dict[str, Any]]] = None,
    play_tf: bool = False,
    faster_tf: bool = False,
) -> Dict[str, Any]:
    """Log this TF. Do not mark complete from a hung 3-red law.

    Sit-out is first/second red on the chosen play TF (or a provisional
    single TF). Faster-TF reds are log only — never sit or take as law.
    """
    kill = news_kill(news_hits)
    if faster_tf:
        sitout = False
    else:
        sitout = first_candle_sitout(
            reds, heat_breadth=heat_breadth, panic_board=panic_board
        )
    need = reds_required(habit_reds)
    try:
        n = int(reds) if reds is not None else None
    except (TypeError, ValueError):
        n = None
    return {
        "tf": tf,
        "complete": False,
        "ad_known": bool(ad_known),
        "reds": n,
        "reds_required": need,
        "reds_ok": False,
        "first_candle_sitout": sitout,
        "faster_tf_log_only": bool(faster_tf),
        "play_tf": bool(play_tf),
        "news_kill": kill is not None,
        "news": kill,
    }


def pick_working_tf(
    tf_states: Sequence[Dict[str, Any]],
    *,
    respected: Optional[Dict[str, float]] = None,
    locked_tf: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Kenneth names the play TF. Faster-TF 3+ reds is log only — never a pick.

    ``respected`` is kept for callers; it does not complete a TF.
    """
    del respected
    if not locked_tf:
        return None
    want = str(locked_tf).strip()
    for s in tf_states:
        if s and str(s.get("tf") or "") == want:
            chosen = dict(s)
            chosen["pick_reason"] = "kenneth_play_tf"
            return chosen
    return {
        "tf": want,
        "complete": False,
        "pick_reason": "kenneth_play_tf",
        "faster_tf_log_only": False,
        "play_tf": True,
    }


# Dump-depth AD-side: cluster at the AD, not equal fifths from T.
# Equally spaced from B + 0.065 L down to slightly under B.
# Forbidden: P_i = T − L × i / 5.
AD_SIDE_L_FRACS = tuple(0.065 + (-0.008 - 0.065) * i / 4 for i in range(5))
AD_SIDE_HALF_PCTS = (10.0, 15.0, 20.0, 25.0, 30.0)
PANIC_HALF_PCTS = (20.0, 30.0, 50.0)
PLAY_AD_HALF = 0.50


def equal_spread_prices(ad_top: float, ad_bottom: float, n: int = 5) -> List[float]:
    """Forbidden pack. Tests assert dump-depth is not this."""
    length = float(ad_top) - float(ad_bottom)
    return [float(ad_top) - ((i + 1) / n) * length for i in range(n)]


def dump_depth_layers(
    ad_top: Optional[float],
    ad_bottom: Optional[float],
    *,
    budget_usd: float = MAX_PER_PLAY_USD,
) -> List[Dict[str, Any]]:
    """Default Size pack. D = L/T. Skip any price ≤ 0. Panic Q_i as specified."""
    try:
        top = float(ad_top) if ad_top is not None else None
        bot = float(ad_bottom) if ad_bottom is not None else None
    except (TypeError, ValueError):
        return []
    if top is None or bot is None:
        return []
    if top <= 0 or bot <= 0 or top <= bot:
        return []
    length = top - bot
    depth = length / top
    budget = max(0.0, min(float(budget_usd), MAX_PER_PLAY_USD))
    if budget <= 0:
        return []
    out: List[Dict[str, Any]] = []
    idx = 1
    ad_budget = budget * PLAY_AD_HALF
    for frac, half_pct in zip(AD_SIDE_L_FRACS, AD_SIDE_HALF_PCTS):
        px = bot + length * frac
        if px <= 0:
            continue
        usd = round(ad_budget * (half_pct / 100.0), 4)
        out.append(
            {
                "idx": idx,
                "price": round(float(px), 8),
                "usd": max(0.0, usd),
                "size_pct": round(PLAY_AD_HALF * half_pct, 4),
                "half_pct": half_pct,
                "band": "ad",
                "d": round(depth, 6),
            }
        )
        idx += 1
    for i, half_pct in enumerate(PANIC_HALF_PCTS, start=1):
        px = bot - length * (0.10 + 0.18 * (i - 1) / 2.0)
        if px <= 0:
            continue
        usd = round(budget * PLAY_AD_HALF * (half_pct / 100.0), 4)
        out.append(
            {
                "idx": idx,
                "price": round(float(px), 8),
                "usd": max(0.0, usd),
                "size_pct": round(PLAY_AD_HALF * half_pct, 4),
                "half_pct": half_pct,
                "band": "panic",
                "d": round(depth, 6),
            }
        )
        idx += 1
    if out:
        used = sum(x["usd"] for x in out[:-1])
        out[-1]["usd"] = round(max(0.0, budget - used), 4)
    return out


def at_ad_layer(layers: Sequence[Dict[str, Any]], ad_bottom: Any) -> Optional[Dict[str, Any]]:
    """The AD-side layer at/just above B; else P5 slightly under."""
    try:
        bot = float(ad_bottom)
    except (TypeError, ValueError):
        bot = None
    ad = [L for L in layers if str(L.get("band") or "ad") == "ad"]
    if not ad:
        return None
    if bot is None:
        return dict(ad[-1])
    above = [L for L in ad if float(L.get("price") or 0) >= bot]
    if above:
        return dict(above[-1])
    return dict(ad[-1])


def exponential_layers(
    ad_top: Optional[float],
    ad_bottom: Optional[float],
    *,
    count: int = DEFAULT_LAYER_COUNT,
    budget_usd: float = MAX_PER_PLAY_USD,
    zone_prices: Optional[Sequence[float]] = None,
) -> List[Dict[str, Any]]:
    """Deprecated equal-spread. Recut/seed use dump_depth_layers."""
    del count, zone_prices
    return dump_depth_layers(ad_top, ad_bottom, budget_usd=budget_usd)
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
    """A timer is not a fail. Clock expiry never fails a plan.

    ``armed_at`` / ``now`` / ``tf`` / ``bounced`` stay on the signature so
    callers can pass a bounce-window clock. That clock is risk, not close.
    """
    del armed_at, now, tf, bounced
    return False


def last_under_ad(
    last_price: Any,
    ad_bottom: Any,
    *,
    ad_known: bool = False,
) -> bool:
    """Tape fact: last is at or under the AD. Not a fail. Not a close."""
    if not ad_known:
        return False
    try:
        px = float(last_price)
        bot = float(ad_bottom)
    except (TypeError, ValueError):
        return False
    return px <= bot


def volume_at_ad(volume: Optional[str], volume_n: Any = None) -> bool:
    if volume_n is not None:
        try:
            if float(volume_n) > 0:
                return True
        except (TypeError, ValueError):
            pass
    return str(volume or "").strip().lower() in {"normal", "elevated", "climax"}


def decision_line(
    *,
    kind: str,
    reds: Optional[int] = None,
    tf: Optional[str] = None,
    volume: Optional[str] = None,
    volume_n: Any = None,
) -> Dict[str, str]:
    """One plain why from evaluate gates. Engine-written. No gloss."""
    if kind == "news":
        return {"decision": "News flatten.", "decision_reason": "news"}
    if kind == "fail":
        return {
            "decision": "Reassess. Do not flatten from a clock.",
            "decision_reason": "fail",
        }
    if kind == "kill":
        return {"decision": "Kill.", "decision_reason": "kill"}
    if kind == "bounce":
        return {"decision": "Bounce.", "decision_reason": "bounce"}
    if kind == "sit_out":
        try:
            n = int(reds) if reds is not None else 1
        except (TypeError, ValueError):
            n = 1
        word = "Second" if n == 2 else "First"
        return {
            "decision": f"{word} red at the AD, sit out.",
            "decision_reason": "sit_out",
        }
    if kind == "arm":
        bits: List[str] = []
        try:
            n = int(reds) if reds is not None else None
        except (TypeError, ValueError):
            n = None
        tf_s = str(tf or "").strip()
        if n is not None and tf_s:
            bits.append(f"{n} red {tf_s}")
        elif n is not None:
            bits.append(f"{n} red")
        if volume_at_ad(volume, volume_n):
            bits.append("volume at the AD")
        bits.append("no news")
        return {
            "decision": ", ".join(bits) + ", taking it.",
            "decision_reason": "arm",
        }
    if kind == "cap":
        return {"decision": "Two live, wait.", "decision_reason": "cap"}
    if kind == "watch":
        return {
            "decision": "Watch. Waiting for the line.",
            "decision_reason": "watch",
        }
    return {"decision": "Grind, no volume, wait.", "decision_reason": "wait"}


def price_eq(a: Any, b: Any) -> bool:
    """Match official ticks. Tight — do not treat nearby pixels as the bar."""
    try:
        x, y = float(a), float(b)
    except (TypeError, ValueError):
        return False
    if x == y:
        return True
    return abs(x - y) <= max(1e-12, abs(y) * 1e-8)
