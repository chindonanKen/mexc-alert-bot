"""Tape facts for the process-pack interpreter. No play numbers, no policy."""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence

from .hang import official_volume_n, volume_label
from .logic import is_faster_tf, news_kill
from .tape import official_last_price, official_reds


MET_FRAC = 0.05  # last 5% of L above B, through B
VOL_SPIKE = 1.2


def _f(raw: Any) -> Optional[float]:
    if raw is None or raw == "":
        return None
    try:
        x = float(raw)
    except (TypeError, ValueError):
        return None
    return x


def dollar_volume(bars: Optional[Sequence[Dict[str, Any]]]) -> Optional[float]:
    """Last bar quote $ if present, else coin volume × close. Never invent."""
    seq = [b for b in (bars or []) if isinstance(b, dict)]
    for bar in reversed(seq):
        for key in ("q", "quote_volume", "quoteVolume"):
            v = _f(bar.get(key))
            if v is not None and v > 0:
                return v
        coin = _f(bar.get("v") if bar.get("v") is not None else bar.get("volume"))
        close = _f(bar.get("c") if bar.get("c") is not None else bar.get("close"))
        if coin is not None and coin > 0 and close is not None and close > 0:
            return coin * close
    n = official_volume_n(bars)
    return n if n is not None and n > 0 else None


def typical_dollar_volume(bars: Optional[Sequence[Dict[str, Any]]]) -> Optional[float]:
    seq = [b for b in (bars or []) if isinstance(b, dict)]
    if len(seq) < 4:
        return None
    vals = []
    for bar in seq[:-1]:
        q = None
        for key in ("q", "quote_volume", "quoteVolume"):
            q = _f(bar.get(key))
            if q is not None and q > 0:
                break
        if q is None:
            coin = _f(bar.get("v") if bar.get("v") is not None else bar.get("volume"))
            close = _f(bar.get("c") if bar.get("c") is not None else bar.get("close"))
            if coin and close:
                q = coin * close
        if q is not None and q > 0:
            vals.append(q)
    if not vals:
        return None
    vals.sort()
    return vals[len(vals) // 2]


def met_ceiling(ad_top: Any, ad_bottom: Any) -> Optional[float]:
    t, b = _f(ad_top), _f(ad_bottom)
    if t is None or b is None or t <= b or b <= 0:
        return None
    length = t - b
    return b + MET_FRAC * length


def is_met(*, last: Any, ad_top: Any, ad_bottom: Any, ad_known: bool) -> bool:
    """Met area: last in the last 5% of L above B, through B. Not a buy."""
    if not ad_known:
        return False
    px, ceil = _f(last), met_ceiling(ad_top, ad_bottom)
    b = _f(ad_bottom)
    if px is None or ceil is None or b is None:
        return False
    return px <= ceil


def is_stock_symbol(symbol: Any, market: Any = None) -> bool:
    s = str(symbol or "").upper()
    return "STOCK" in s


def faster_tf_for(play_tf: Optional[str]) -> str:
    from .settings import TF_SLOW_RANK, tf_slow_rank

    rank = tf_slow_rank(play_tf)
    if rank <= 0:
        return "1m"
    slower = sorted(
        ((v, k) for k, v in TF_SLOW_RANK.items() if v < rank),
        reverse=True,
    )
    return slower[0][1] if slower else "1m"


def play_from_row(plan: Dict[str, Any]) -> Dict[str, Any]:
    raw = plan.get("play") or plan.get("play_json")
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        import json

        try:
            blob = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return blob if isinstance(blob, dict) else {}
    return {}


def facts_from(
    plan: Dict[str, Any],
    snap: Dict[str, Any],
    *,
    board: Optional[Dict[str, Any]] = None,
) -> Dict[str, bool]:
    """Boolean fact map the pack `when` list matches. Policy stays in the pack."""
    board = board or {}
    play = play_from_row(plan)
    ad_known = (plan.get("ad_status") == "known") and plan.get("ad_top") is not None
    written = bool(ad_known and _f(plan.get("ad_top")) and _f(plan.get("ad_bottom")))
    last = official_last_price(
        ticker=snap.get("last_price")
        if snap.get("last_price") is not None
        else snap.get("ticker"),
        bars=snap.get("bars") or snap.get("bars_1m"),
    )
    if last is None:
        last = _f(plan.get("last_price"))
    met = is_met(
        last=last,
        ad_top=plan.get("ad_top"),
        ad_bottom=plan.get("ad_bottom"),
        ad_known=written,
    )
    past_b = False
    b = _f(plan.get("ad_bottom"))
    if written and last is not None and b is not None:
        past_b = last < b

    play_tf = str(plan.get("tf") or play.get("tf") or "").strip() or None
    reds_map = snap.get("reds") if isinstance(snap.get("reds"), dict) else {}
    if not reds_map and snap.get("reds") is not None and play_tf:
        reds_map = {play_tf: snap.get("reds")}
    if snap.get("bars"):
        n = official_reds(snap.get("bars"))
        if n is not None:
            reds_map = dict(reds_map)
            reds_map[str(play_tf or "15m")] = n
    play_reds = None
    if play_tf and play_tf in reds_map:
        try:
            play_reds = int(reds_map[play_tf])
        except (TypeError, ValueError):
            play_reds = None
    elif len(reds_map) == 1:
        try:
            play_reds = int(next(iter(reds_map.values())))
        except (TypeError, ValueError):
            play_reds = None

    first_or_second = play_reds in (1, 2)
    faster_reds = None
    for tf, n in reds_map.items():
        if is_faster_tf(str(tf), play_tf):
            try:
                faster_reds = int(n)
            except (TypeError, ValueError):
                continue
            break

    vol_fast = _f(snap.get("vol_usd_fast"))
    if vol_fast is None:
        vol_fast = dollar_volume(snap.get("bars_1m") or snap.get("fast_bars"))
    vol_play = _f(snap.get("vol_usd_play"))
    if vol_play is None:
        vol_play = dollar_volume(snap.get("bars"))
    habit = _f(play.get("volume_habit_usd") or snap.get("vol_habit_usd"))
    if habit is None:
        habit = typical_dollar_volume(snap.get("bars_1m") or snap.get("bars"))

    spike = bool(snap.get("vol_spike"))
    if not spike and vol_fast is not None and habit is not None and habit > 0:
        spike = vol_fast >= VOL_SPIKE * habit
    if not spike:
        lab = str(snap.get("volume") or "").lower()
        if lab in {"elevated", "climax"} and snap.get("bars_1m"):
            spike = True
        elif lab in {"elevated", "climax"} and not snap.get("bars"):
            # Label without a fast tape is not a 1m spike.
            spike = False

    quiet = bool(snap.get("quiet_grind"))
    fast_dump = bool(snap.get("fast_dump") or snap.get("fast_dump_volume"))
    if snap.get("quiet_grind") is None and snap.get("fast_dump") is None:
        if spike and (first_or_second or (play_reds or 0) >= 1 or past_b or met):
            fast_dump = True
        if not spike and not met:
            quiet = True
    fast_dump_volume = bool(fast_dump and spike) or bool(snap.get("fast_dump_volume"))
    if snap.get("fast_dump_volume") is False:
        fast_dump_volume = False

    kill = news_kill(snap.get("news") or [])
    status = str(plan.get("status") or "")
    sold = bool(play.get("sold_bounce") or snap.get("sold_bounce"))
    panic = bool(snap.get("panic_board") or board.get("panic"))
    heat = snap.get("heat_breadth")
    if heat is not None:
        try:
            from .settings import PANIC_BREADTH_MIN

            if int(heat) >= PANIC_BREADTH_MIN:
                panic = True
        except (TypeError, ValueError):
            pass
    grind_board = bool(board.get("grind")) and not panic

    facts = {
        "written_plan": written,
        "not_written_plan": not written,
        "at_ad": met,
        "not_at_ad": not met,
        "met": met,
        "past_b": past_b,
        "first_or_second_red": first_or_second,
        "not_first_or_second_red": not first_or_second,
        "board_panic": panic,
        "not_board_panic": not panic,
        "board_grind": grind_board,
        "quiet_grind": quiet and not met,
        "fast_dump": fast_dump,
        "fast_dump_volume": fast_dump_volume,
        "vol_spike": spike,
        "news_kill": kill is not None,
        "not_news_kill": kill is None,
        "sold_bounce": sold,
        "not_sold_bounce": not sold,
        "killed": status in ("killed", "blocked"),
        "not_killed": status not in ("killed", "blocked"),
        "live": bool(plan.get("live")),
        "no_repeat": bool(play.get("no_repeat")),
        "into_base": bool(snap.get("into_base")),
        "bounce_strong": bool(snap.get("bounce_strong")),
        "panic_up_volume": bool(snap.get("panic_up_volume")),
        "should_sell": bool(
            snap.get("bounced")
            or snap.get("bounce_strong")
            or snap.get("into_base")
            or snap.get("panic_up_volume")
        ),
        "not_should_sell": not bool(
            snap.get("bounced")
            or snap.get("bounce_strong")
            or snap.get("into_base")
            or snap.get("panic_up_volume")
        ),
    }
    facts["_last"] = last
    facts["_play_reds"] = play_reds
    facts["_faster_reds"] = faster_reds
    facts["_vol_usd_fast"] = vol_fast
    facts["_vol_usd_play"] = vol_play
    facts["_vol_habit_usd"] = habit
    facts["_play_tf"] = play_tf
    facts["_reds_map"] = reds_map
    facts["_kill"] = kill
    facts["_volume_label"] = snap.get("volume") or volume_label(snap.get("bars"))
    return facts
