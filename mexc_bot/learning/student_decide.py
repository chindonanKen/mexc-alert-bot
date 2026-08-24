"""Week-1 AD student DECIDE — walk this chart's official tape.

Staff-only. No orders. No pixel prices. No invented line without a walk.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from .red_streak import consecutive_red_streak, streak_label

MANILA = ZoneInfo("Asia/Manila")
TZ_NAME = "Asia/Manila"
DEFAULT_TF = "15m"
MIN_DROP_PCT = 4.0
MIN_BOUNCE_FRAC = 0.40
MIN_REPEAT = 2
TAG_THROUGH_EPS = 0.002
TAG_TOUCH_EPS = 0.003
VOL_EXPAND = 1.4
VOL_FLAT = 0.9

Bar = Dict[str, Any]
FetchBars = Callable[[str, str, str, int], List[Bar]]


def fmt_px(value: Optional[float]) -> str:
    if value is None:
        return ""
    x = float(value)
    ax = abs(x)
    if ax >= 1000:
        s = f"{x:.2f}"
    elif ax >= 1:
        s = f"{x:.6f}"
    else:
        s = f"{x:.8f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def manila_label(ts: Any) -> str:
    """Official bar open → Asia/Manila wall clock."""
    try:
        t = float(ts)
    except (TypeError, ValueError):
        return ""
    if t > 1e12:
        t /= 1000.0
    if t <= 0:
        return ""
    dt = datetime.fromtimestamp(t, tz=MANILA)
    return dt.strftime("%Y-%m-%d %H:%M PHT")


def name_bar(bar: Optional[Bar]) -> Dict[str, Any]:
    if not bar:
        return {}
    try:
        ts = float(bar.get("ts") or 0)
    except (TypeError, ValueError):
        ts = 0.0
    return {
        "ts": ts,
        "label": manila_label(ts),
        "o": _f(bar.get("o")),
        "h": _f(bar.get("h")),
        "l": _f(bar.get("l")),
        "c": _f(bar.get("c")),
        "v": _f(bar.get("v")),
    }


def _f(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _vol_flag(dump_vol: Optional[float], base_vol: Optional[float]) -> str:
    if not dump_vol or not base_vol or base_vol <= 0:
        return "unknown"
    ratio = dump_vol / base_vol
    if ratio >= VOL_EXPAND:
        return "expand"
    if ratio >= VOL_FLAT:
        return "flat"
    return "dry"


def _dump_reds(bars: Sequence[Bar], low_i: int) -> int:
    if low_i < 0:
        return 0
    return consecutive_red_streak(list(bars)[: low_i + 1], include_forming=False)


def _median(vals: Sequence[float]) -> Optional[float]:
    if not vals:
        return None
    s = sorted(float(v) for v in vals)
    return s[len(s) // 2]


def walk_dump_bounces(
    bars: Sequence[Bar],
    *,
    min_drop_pct: float = MIN_DROP_PCT,
    min_bounce_frac: float = MIN_BOUNCE_FRAC,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Walk closed bars oldest→newest. A cycle is a finished dump that then pumped."""
    seq = [b for b in bars if b]
    n = len(seq)
    cycles: List[Dict[str, Any]] = []
    live = {
        "state": "empty",
        "pump_high": None,
        "pump_high_i": None,
        "dump_low": None,
        "dump_low_i": None,
    }
    if not seq:
        return cycles, live

    rh = float(seq[0].get("h") or 0)
    rhi = 0
    dlow = float(seq[0].get("l") or 0)
    dli = 0
    in_dump = False

    for i, b in enumerate(seq):
        h = float(b.get("h") or 0)
        lo = float(b.get("l") or 0)
        if not in_dump:
            if h > rh:
                rh, rhi = h, i
            if rh > 0 and (lo - rh) / rh * 100.0 <= -abs(min_drop_pct):
                in_dump = True
                dlow, dli = lo, i
            continue
        if lo < dlow:
            dlow, dli = lo, i
            continue
        # Pump only after the dump low is in — not the high of a lower-low bar.
        if i <= dli:
            continue
        span = rh - dlow
        if span > 0 and (h - dlow) / span >= min_bounce_frac:
            reds = _dump_reds(seq, dli)
            base = seq[max(0, rhi - 20) : rhi] or seq[: max(1, rhi)]
            dump_bars = seq[rhi : dli + 1] or [seq[dli]]
            base_vol = _median([float(x.get("v") or 0) for x in base])
            dump_vol = _median([float(x.get("v") or 0) for x in dump_bars[-3:]])
            cycles.append(
                {
                    "high": rh,
                    "low": dlow,
                    "high_i": rhi,
                    "low_i": dli,
                    "high_bar": name_bar(seq[rhi]),
                    "low_bar": name_bar(seq[dli]),
                    "reds": reds,
                    "vol": _vol_flag(dump_vol, base_vol),
                    "drop_pct": (dlow - rh) / rh * 100.0 if rh else None,
                }
            )
            in_dump = False
            rh, rhi = h, i
            dlow, dli = lo, i

    live = {
        "state": "dump" if in_dump else "seek",
        "pump_high": rh,
        "pump_high_i": rhi,
        "dump_low": dlow if in_dump else None,
        "dump_low_i": dli if in_dump else None,
        "n_bars": n,
    }
    return cycles, live


def path_habit(cycles: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """How THIS chart's dumps finished — not a crypto-wide rule."""
    if not cycles:
        return {"reds": None, "vol": "unknown", "n": 0}
    reds = [int(c.get("reds") or 0) for c in cycles]
    vols = [str(c.get("vol") or "unknown") for c in cycles]
    typical_reds = _median([r for r in reds if r > 0]) or _median(reds)
    scored = {"expand": 0, "flat": 0, "dry": 0, "unknown": 0}
    for v in vols:
        scored[v if v in scored else "unknown"] += 1
    vol = max((k for k in ("expand", "flat", "dry") if scored[k]), key=lambda k: scored[k], default="unknown")
    if scored.get(vol, 0) == 0:
        vol = "unknown"
    return {
        "reds": int(typical_reds) if typical_reds is not None else None,
        "vol": vol,
        "n": len(cycles),
        "reds_samples": reds,
        "vol_samples": vols,
    }


def live_copy_text(top: float, bottom: float) -> str:
    return f"top {fmt_px(top)} → bottom {fmt_px(bottom)}"


def tag_state(bars: Sequence[Bar], bottom: float) -> str:
    if not bars or bottom is None:
        return "wait"
    last = bars[-1]
    try:
        close = float(last.get("c") or 0)
        recent_low = min(float(b.get("l") or close) for b in bars[-3:])
    except (TypeError, ValueError):
        return "wait"
    if close < bottom * (1.0 - TAG_THROUGH_EPS):
        return "through"
    if recent_low <= bottom * (1.0 + TAG_TOUCH_EPS):
        return "tagged"
    return "wait"


def _vs_habit(live_reds: int, habit_reds: Optional[int]) -> str:
    if habit_reds is None:
        return "unknown"
    if live_reds < habit_reds:
        return "short"
    if live_reds > habit_reds:
        return "long"
    return "at"


def live_vol_pack(bars: Sequence[Bar], live_reds: int) -> Dict[str, Any]:
    """Volume on the live dump vs this tape's recent base — not a market-wide rule."""
    seq = list(bars or [])
    n_dump = max(int(live_reds or 0), 3)
    if len(seq) < n_dump + 4:
        return {"flag": "unknown", "ratio": None}
    dump = seq[-n_dump:]
    base = seq[-(n_dump + 20) : -n_dump] or seq[:-n_dump]
    dump_vol = _median([float(b.get("v") or 0) for b in dump])
    base_vol = _median([float(b.get("v") or 0) for b in base])
    flag = _vol_flag(dump_vol, base_vol)
    ratio = None
    if dump_vol and base_vol and base_vol > 0:
        ratio = dump_vol / base_vol
    return {"flag": flag, "ratio": ratio}


def should_paper_fill(decide: Dict[str, Any]) -> bool:
    """Paper only: line + copy tagged + THIS chart's path habit. No global 3–5."""
    if not isinstance(decide, dict):
        return False
    if decide.get("action") != "line":
        return False
    if decide.get("live_orders"):
        return False
    if decide.get("tag") not in ("tagged", "through"):
        return False
    if not decide.get("live_copy"):
        return False
    habit = decide.get("path_habit") or {}
    streak = decide.get("live_streak") or {}
    if habit.get("reds") is None:
        return False
    if streak.get("vs_habit") not in ("at", "long"):
        return False
    hvol = habit.get("vol")
    live_vol = (decide.get("live_vol") or {}).get("flag")
    if hvol == "expand" and live_vol == "dry":
        return False
    return True


def decide_from_bars(
    bars: Sequence[Bar],
    *,
    symbol: str,
    market: str,
    tf: str = DEFAULT_TF,
    min_drop_pct: float = MIN_DROP_PCT,
    min_bounce_frac: float = MIN_BOUNCE_FRAC,
    min_repeat: int = MIN_REPEAT,
) -> Dict[str, Any]:
    """Structured week-1 decide from already-fetched official bars."""
    mkt = (market or "futures").strip().lower() or "futures"
    sym = (symbol or "").strip()
    tf_s = (tf or DEFAULT_TF).strip() or DEFAULT_TF
    base = {
        "ok": True,
        "action": "skip",
        "reason": "no_tape",
        "symbol": sym,
        "market": mkt,
        "tf": tf_s,
        "tz": TZ_NAME,
        "live_orders": False,
        "initial_drop": None,
        "live_copy": None,
        "path_habit": None,
        "live_streak": None,
        "live_vol": None,
        "tag": None,
        "cycles": 0,
    }
    seq = list(bars or [])
    if not seq:
        return base

    cycles, live = walk_dump_bounces(
        seq, min_drop_pct=min_drop_pct, min_bounce_frac=min_bounce_frac
    )
    base["cycles"] = len(cycles)
    if len(cycles) < int(min_repeat):
        base["reason"] = "no_repeat"
        return base

    first = cycles[0]
    high = float(first["high"])
    low = float(first["low"])
    drop_len = high - low
    top = float(live.get("pump_high") or high)
    bottom = top - drop_len
    habit = path_habit(cycles)
    live_reds = consecutive_red_streak(seq, include_forming=False)
    copy = {
        "top": top,
        "bottom": bottom,
        "text": live_copy_text(top, bottom),
        "pump_high_bar": name_bar(seq[int(live["pump_high_i"])])
        if live.get("pump_high_i") is not None
        else name_bar(first.get("high_bar")),
    }
    base.update(
        {
            "action": "line",
            "reason": "walked",
            "initial_drop": {
                "high": high,
                "low": low,
                "high_bar": first.get("high_bar"),
                "low_bar": first.get("low_bar"),
                "text": (
                    f"{(first.get('high_bar') or {}).get('label') or ''} high {fmt_px(high)} → "
                    f"{(first.get('low_bar') or {}).get('label') or ''} low {fmt_px(low)}"
                ).strip(),
                "drop_len": drop_len,
            },
            "live_copy": copy,
            "path_habit": habit,
            "live_streak": {
                "reds": live_reds,
                "label": streak_label(live_reds),
                "vs_habit": _vs_habit(live_reds, habit.get("reds")),
            },
            "live_vol": live_vol_pack(seq, live_reds),
            "tag": tag_state(seq, bottom),
        }
    )
    return base


def _default_fetch(market: str, symbol: str, tf: str, limit: int) -> List[Bar]:
    from ..movers.klines import KlineClient

    client = KlineClient()
    try:
        return client.get_ohlcv(market, symbol, tf, limit=limit) or []
    finally:
        client.close()


def decide_symbol(
    symbol: str,
    market: str = "futures",
    *,
    tf: str = DEFAULT_TF,
    bars: Optional[Sequence[Bar]] = None,
    fetch_bars: Optional[FetchBars] = None,
    limit: int = 240,
) -> Dict[str, Any]:
    """Walk official MEXC klines unless bars are injected (tests)."""
    mkt = (market or "futures").strip().lower() or "futures"
    sym = (symbol or "").strip()
    if not sym:
        return decide_from_bars([], symbol="", market=mkt, tf=tf)
    if bars is None:
        getter = fetch_bars or _default_fetch
        try:
            bars = getter(mkt, sym, tf, int(limit))
        except Exception:
            bars = []
    return decide_from_bars(bars, symbol=sym, market=mkt, tf=tf)


def collect_book_names(user_id: Optional[int] = None) -> List[Dict[str, str]]:
    """Book / hunt names: targets + watchlist + open positions. No tape walk."""
    from ..webapi import actions

    uid = user_id
    book: List[tuple] = []
    try:
        for a in actions.list_alerts(uid):
            book.append((a.get("market") or "spot", a.get("symbol")))
        for w in actions.list_watchlist(uid):
            book.append((w.get("market") or "futures", w.get("symbol")))
        for p in actions.list_positions(uid):
            book.append((p.get("market") or "futures", p.get("symbol")))
    except Exception:
        pass
    seen = set()
    out: List[Dict[str, str]] = []
    for m, s in book:
        if not s:
            continue
        key = ((m or "futures").lower(), str(s).upper())
        if key in seen:
            continue
        seen.add(key)
        out.append({"symbol": str(s), "market": key[0]})
    return out


def decide_book(
    names: Optional[Sequence[Dict[str, str]]] = None,
    *,
    tf: str = DEFAULT_TF,
    fetch_bars: Optional[FetchBars] = None,
    user_id: Optional[int] = None,
    walk: bool = True,
    max_names: int = 30,
) -> Dict[str, Any]:
    """Staff: list book names, optionally walk each. No walk → no line."""
    rows = list(names) if names is not None else collect_book_names(user_id)
    slim = []
    for row in rows[: max(0, int(max_names))]:
        slim.append(
            {
                "symbol": str(row.get("symbol") or ""),
                "market": str(row.get("market") or "futures").lower(),
            }
        )
    if not walk:
        return {
            "ok": True,
            "live_orders": False,
            "tf": tf,
            "tz": TZ_NAME,
            "n": len(slim),
            "names": slim,
            "decides": [],
        }
    decides = [
        decide_symbol(
            r["symbol"],
            r["market"],
            tf=tf,
            fetch_bars=fetch_bars,
        )
        for r in slim
        if r["symbol"]
    ]
    return {
        "ok": True,
        "live_orders": False,
        "tf": tf,
        "tz": TZ_NAME,
        "n": len(decides),
        "names": slim,
        "decides": decides,
    }
