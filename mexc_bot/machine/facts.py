"""Tape facts for the process-pack interpreter. No play numbers, no policy."""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence

from .hang import official_volume_n, volume_label
from .logic import is_faster_tf, news_kill
from .tape import official_last_price, official_reds


MET_FRAC = 0.05  # last 5% of L above B, through B
THROUGH_FRAC = 0.03  # slightly through B is still the AD area, not panic Q
VOL_SPIKE = 1.2
TF_BAR_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "8h": 28800,
    "12h": 43200,
    "1d": 86400,
    "1w": 604800,
}


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


def bars_ever_met(
    plan: Dict[str, Any],
    snap: Dict[str, Any],
    *,
    last: Any = None,
) -> bool:
    """Sticky: once last or a bar low printed the met band, it stays met."""
    if plan.get("met"):
        return True
    ad_known = (plan.get("ad_status") == "known") and plan.get("ad_top") is not None
    if is_met(
        last=last,
        ad_top=plan.get("ad_top"),
        ad_bottom=plan.get("ad_bottom"),
        ad_known=ad_known,
    ):
        return True
    for seq in (
        snap.get("bars"),
        snap.get("bars_1m"),
        snap.get("faster_bars"),
    ):
        for bar in seq or []:
            if not isinstance(bar, dict):
                continue
            low = bar.get("l") if bar.get("l") is not None else bar.get("low")
            if is_met(
                last=low,
                ad_top=plan.get("ad_top"),
                ad_bottom=plan.get("ad_bottom"),
                ad_known=ad_known,
            ):
                return True
    return False


def is_met(*, last: Any, ad_top: Any, ad_bottom: Any, ad_known: bool) -> bool:
    """Met area: last in the last 5% of L above B, slightly through B. Not a buy."""
    if not ad_known:
        return False
    px, ceil = _f(last), met_ceiling(ad_top, ad_bottom)
    t, b = _f(ad_top), _f(ad_bottom)
    if px is None or ceil is None or b is None or t is None or t <= b:
        return False
    floor = b - THROUGH_FRAC * (t - b)
    return floor <= px <= ceil


def is_stock_symbol(symbol: Any, market: Any = None) -> bool:
    s = str(symbol or "").upper()
    if "STOCK" in s:
        return True
    # Compact MEXC stock perps (TSLAUSDT), not crypto BTC_USDT.
    if str(market or "").lower() == "futures" and "_" not in s and s.endswith("USDT"):
        return True
    return False


_FASTER_TF = {
    "1w": "1d",
    "1d": "15m",
    "12h": "15m",
    "8h": "15m",
    "4h": "15m",
    "1h": "5m",
    "15m": "1m",
    "5m": "1m",
    "1m": "1m",
}


def faster_tf_for(play_tf: Optional[str]) -> str:
    """A faster tape than the play TF. Never the next-slower chart step (12h on 1d)."""
    key = str(play_tf or "").strip()
    return _FASTER_TF.get(key, "1m")


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
        bars=snap.get("bars_1m"),
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
    past_panic = False
    approaching = False
    b = _f(plan.get("ad_bottom"))
    t = _f(plan.get("ad_top"))
    if written and last is not None and b is not None:
        past_b = last < b
        if t is not None and t > b:
            length = t - b
            past_panic = last < b - THROUGH_FRAC * length
            ceil = b + MET_FRAC * length
            approaching = (not met) and last <= b + 0.20 * length and last > ceil

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
    elif not play_tf:
        others = {k: v for k, v in reds_map.items() if str(k) != "1m"}
        if len(others) == 1:
            try:
                play_reds = int(next(iter(others.values())))
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
    if snap.get("trade_dump"):
        fast_dump = True
        fast_dump_volume = True
        quiet = False

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

    fill = _f(plan.get("leftover_avg"))
    typical = _f(play.get("typical_bounce") or play.get("bounce_run"))
    bounce_strong = bool(snap.get("bounce_strong") or snap.get("bounced"))
    panic_up = bool(snap.get("panic_up_volume"))
    into_base = bool(snap.get("into_base"))
    bounce_weak = bool(snap.get("bounce_weak"))
    if (
        not bounce_weak
        and plan.get("live")
        and last is not None
        and fill is not None
        and last > fill
        and not bounce_strong
        and not panic_up
        and not into_base
    ):
        bounce_weak = typical is None or last < fill + typical

    candles_stale = bool(snap.get("candles_stale"))
    need_n = play.get("candles_to_bounce")
    armed = _f(plan.get("armed_at"))
    if (
        not candles_stale
        and need_n
        and armed
        and met
        and plan.get("live")
        and not bounce_strong
        and not panic_up
    ):
        n_bars = 0
        for bar in snap.get("bars") or []:
            if not isinstance(bar, dict):
                continue
            ts = _f(bar.get("ts"))
            if ts is not None and ts >= armed:
                n_bars += 1
        try:
            candles_stale = n_bars >= int(need_n)
        except (TypeError, ValueError):
            candles_stale = False

    should_sell = bool(
        snap.get("bounced")
        or bounce_strong
        or into_base
        or panic_up
        or candles_stale
    )

    facts = {
        "written_plan": written,
        "not_written_plan": not written,
        "at_ad": met,
        "not_at_ad": not met,
        "met": met,
        "past_b": past_b,
        "not_past_b": not past_b,
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
        "into_base": into_base,
        "bounce_strong": bounce_strong,
        "panic_up_volume": panic_up,
        "should_sell": should_sell,
        "not_should_sell": not should_sell,
        "past_panic": past_panic,
        "not_past_panic": not past_panic,
        "approaching_ad": approaching,
        "not_live": not bool(plan.get("live")),
        "not_board_grind": not grind_board,
        "nibble_done": bool(play.get("nibble_done")),
        "not_nibble_done": not bool(play.get("nibble_done")),
        "bounce_weak": bounce_weak and not should_sell,
        "not_bounce_weak": not (bounce_weak and not should_sell),
        "candles_stale": candles_stale,
        "not_fast_dump_volume": not fast_dump_volume,
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
