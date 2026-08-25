"""Student DECIDE — first dump on the full tape, or Kenneth's locked visual AD.

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
LOCK_PX_EPS = 0.002
FORMULA_LOCK_SOURCES = frozenset(
    {"formula", "grind", "auto", "chart_features", "system"}
)
STAFF_LOCK_SOURCES = frozenset(
    {"staff", "owner", "recut", "locked", "human", ""}
)
TF_SECONDS = {
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


def tape_bar_limit(tf: str) -> int:
    """Enough closed bars to see the first dump, not a mid-history window."""
    sec = TF_SECONDS.get((tf or DEFAULT_TF).strip() or DEFAULT_TF, 900)
    n = int(200 * 86400 / max(int(sec), 60))
    return max(240, min(n, 2000))


def parse_pht_label(label: Any) -> Optional[float]:
    raw = str(label or "").replace(" PHT", "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=MANILA).timestamp()
        except ValueError:
            continue
    return None


def usable_locked_visual_ad(vad: Any, case: Optional[Dict[str, Any]] = None) -> bool:
    """Kenneth high/low lock only. Ignore formula / grind AD rows."""
    if not isinstance(vad, dict):
        return False
    high, low = _f(vad.get("high")), _f(vad.get("low"))
    if high is None or low is None or high <= low:
        return False
    src = str(vad.get("source") or "").strip().lower()
    if src in FORMULA_LOCK_SOURCES:
        return False
    if src not in STAFF_LOCK_SOURCES:
        return False
    band = str((case or {}).get("velocity_band") or "").strip().upper()
    if band == "GRIND" and src in FORMULA_LOCK_SOURCES:
        return False
    return True


def locked_from_visual_ad(vad: Dict[str, Any]) -> Dict[str, Any]:
    high = float(vad["high"])
    low = float(vad["low"])
    high_ts = _f(vad.get("high_ts")) or parse_pht_label(vad.get("high_label"))
    low_ts = _f(vad.get("low_ts")) or parse_pht_label(vad.get("low_label"))
    return {
        "high": high,
        "low": low,
        "high_ts": high_ts,
        "low_ts": low_ts,
        "high_label": vad.get("high_label") or "",
        "low_label": vad.get("low_label") or "",
        "tf": vad.get("tf") or "",
        "source": vad.get("source") or "staff",
        "drop_len": high - low,
    }


def find_locked_visual_ad(
    event_store: Any,
    user_id: Optional[int],
    symbol: str,
    market: str,
    tf: str = DEFAULT_TF,
) -> Optional[Dict[str, Any]]:
    """Latest staff visual_ad / locked recut for this name. Not formula AD."""
    if event_store is None or not user_id:
        return None
    try:
        from .visual_ad import extract_visual_ad, parse_features_json

        rows = event_store.list_setup_cases_for_symbol(
            int(user_id), symbol, market=market, limit=40
        )
    except Exception:
        return None
    tf_s = (tf or "").strip().lower()
    best: Optional[Dict[str, Any]] = None
    best_score = -1.0
    for row in rows:
        feats = parse_features_json(row.get("features_json"))
        vad = extract_visual_ad(feats)
        if not usable_locked_visual_ad(vad, row):
            continue
        score = float(row.get("frozen_at") or 0)
        if tf_s and str((vad or {}).get("tf") or "").strip().lower() == tf_s:
            score += 1e12
        if score > best_score and vad:
            best_score = score
            best = locked_from_visual_ad(vad)
    return best


def match_locked_bars(
    bars: Sequence[Bar],
    locked: Dict[str, Any],
    *,
    tf: str = DEFAULT_TF,
) -> Optional[Dict[str, Any]]:
    """Find Kenneth's locked high/low bars on this tape. None → cannot match."""
    seq = [b for b in bars if b]
    high = _f(locked.get("high"))
    low = _f(locked.get("low"))
    if not seq or high is None or low is None or high <= low:
        return None
    tf_sec = float(TF_SECONDS.get((tf or DEFAULT_TF).strip() or DEFAULT_TF, 14400))
    slop = max(60.0, tf_sec * 0.51)
    high_ts = _f(locked.get("high_ts")) or parse_pht_label(locked.get("high_label"))
    low_ts = _f(locked.get("low_ts")) or parse_pht_label(locked.get("low_label"))

    def _near_px(got: Optional[float], want: float) -> bool:
        if got is None or want <= 0:
            return False
        return abs(float(got) - want) / want <= LOCK_PX_EPS

    def _near_ts(got: Any, want: float) -> bool:
        try:
            t = float(got or 0)
        except (TypeError, ValueError):
            return False
        if t > 1e12:
            t /= 1000.0
        return abs(t - want) <= slop

    hi = None
    for i, b in enumerate(seq):
        if high_ts is not None and not _near_ts(b.get("ts"), high_ts):
            continue
        if not _near_px(_f(b.get("h")), high):
            continue
        hi = i
        break
    if hi is None:
        return None
    li = None
    for i, b in enumerate(seq[hi:], start=hi):
        if low_ts is not None and not _near_ts(b.get("ts"), low_ts):
            continue
        if not _near_px(_f(b.get("l")), low):
            continue
        li = i
        break
    if li is None:
        return None
    return {
        "high": high,
        "low": low,
        "high_i": hi,
        "low_i": li,
        "high_bar": name_bar(seq[hi]),
        "low_bar": name_bar(seq[li]),
        "drop_len": high - low,
    }


def _pump_high_after(
    bars: Sequence[Bar], after_i: int, live: Optional[Dict[str, Any]] = None
) -> Tuple[float, Optional[int]]:
    seq = list(bars)
    window = seq[after_i + 1 :] if after_i >= 0 else seq
    if window:
        j = max(range(len(window)), key=lambda k: float(window[k].get("h") or 0))
        return float(window[j].get("h") or 0), after_i + 1 + j
    if live and live.get("pump_high") is not None:
        return float(live["pump_high"]), live.get("pump_high_i")
    if seq:
        j = max(range(len(seq)), key=lambda k: float(seq[k].get("h") or 0))
        return float(seq[j].get("h") or 0), j
    return 0.0, None


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
    locked: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """First finished dump-then-pump on this tape, or Kenneth's locked length."""
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
        "locked": None,
    }
    seq = list(bars or [])
    if not seq:
        return base

    cycles, live = walk_dump_bounces(
        seq, min_drop_pct=min_drop_pct, min_bounce_frac=min_bounce_frac
    )
    base["cycles"] = len(cycles)

    lock_used = None
    first = None
    reason = "walked"
    if locked:
        matched = match_locked_bars(seq, locked, tf=tf_s)
        if not matched:
            base["reason"] = "locked_bars_missing"
            base["locked"] = {
                "high": locked.get("high"),
                "low": locked.get("low"),
                "source": locked.get("source"),
                "matched": False,
            }
            return base
        first = matched
        lock_used = {
            "high": matched["high"],
            "low": matched["low"],
            "source": locked.get("source") or "staff",
            "matched": True,
        }
        reason = "locked"
        top, top_i = _pump_high_after(seq, int(matched["low_i"]), live)
    else:
        if len(cycles) < int(min_repeat):
            base["reason"] = "no_repeat"
            return base
        first = cycles[0]
        top = float(live.get("pump_high") or first["high"])
        top_i = live.get("pump_high_i")

    high = float(first["high"])
    low = float(first["low"])
    drop_len = high - low
    if not locked:
        top = float(live.get("pump_high") or high)
        top_i = live.get("pump_high_i")
    bottom = top - drop_len
    habit = path_habit(cycles)
    live_reds = consecutive_red_streak(seq, include_forming=False)
    high_bar = first.get("high_bar")
    low_bar = first.get("low_bar")
    copy = {
        "top": top,
        "bottom": bottom,
        "text": live_copy_text(top, bottom),
        "pump_high_bar": name_bar(seq[int(top_i)])
        if top_i is not None
        else name_bar(high_bar),
    }
    base.update(
        {
            "action": "line",
            "reason": reason,
            "locked": lock_used,
            "initial_drop": {
                "high": high,
                "low": low,
                "high_bar": high_bar,
                "low_bar": low_bar,
                "text": (
                    f"{(high_bar or {}).get('label') or ''} high {fmt_px(high)} → "
                    f"{(low_bar or {}).get('label') or ''} low {fmt_px(low)}"
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


def _fetch_symbol_candidates(market: str, symbol: str) -> List[str]:
    raw = (symbol or "").strip()
    out: List[str] = []
    if raw:
        out.append(raw)
    if (market or "").lower() == "futures":
        try:
            from ..exchange import futures_symbol_candidates

            out.extend(futures_symbol_candidates(raw))
        except Exception:
            pass
        try:
            from .symbols import normalize_learning_symbol

            norm = normalize_learning_symbol(raw, "futures")
            if norm:
                out.append(norm)
        except Exception:
            pass
    seen = set()
    uniq: List[str] = []
    for s in out:
        key = str(s).upper()
        if not key or key in seen:
            continue
        seen.add(key)
        uniq.append(s)
    return uniq


def _default_fetch(market: str, symbol: str, tf: str, limit: int) -> List[Bar]:
    from ..movers.klines import KlineClient

    client = KlineClient()
    try:
        for cand in _fetch_symbol_candidates(market, symbol):
            bars = client.get_ohlcv(market, cand, tf, limit=limit) or []
            if bars:
                return bars
        return []
    finally:
        client.close()


def decide_symbol(
    symbol: str,
    market: str = "futures",
    *,
    tf: str = DEFAULT_TF,
    bars: Optional[Sequence[Bar]] = None,
    fetch_bars: Optional[FetchBars] = None,
    limit: Optional[int] = None,
    locked: Optional[Dict[str, Any]] = None,
    event_store: Any = None,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Walk official MEXC klines unless bars are injected (tests)."""
    mkt = (market or "futures").strip().lower() or "futures"
    sym = (symbol or "").strip()
    tf_s = (tf or DEFAULT_TF).strip() or DEFAULT_TF
    if not sym:
        return decide_from_bars([], symbol="", market=mkt, tf=tf_s)
    lock = locked
    if lock is None:
        lock = find_locked_visual_ad(event_store, user_id, sym, mkt, tf_s)
    if bars is None:
        getter = fetch_bars or _default_fetch
        n = int(limit if limit is not None else tape_bar_limit(tf_s))
        try:
            bars = getter(mkt, sym, tf_s, n)
        except Exception:
            bars = []
    return decide_from_bars(bars, symbol=sym, market=mkt, tf=tf_s, locked=lock)


def collect_book_names(user_id: Optional[int] = None) -> List[Dict[str, str]]:
    """Book names: targets + watchlist + open positions. No tape walk."""
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
    event_store: Any = None,
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
            event_store=event_store,
            user_id=user_id,
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
