"""AD chart features from public OHLCV: sharpness, AD depth, volume, RSI divergence.

Soft-fail always — never block fires or learning writes.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .red_streak import consecutive_red_streak, streak_label, streak_pack

logger = logging.getLogger(__name__)

Bar = Dict[str, float]  # ts, o, h, l, c, v

# Probe ladder 1m→1w; skip missing. Owner may click any TF (not exclusive).
CANDIDATE_TFS: Tuple[str, ...] = (
    "1m",
    "5m",
    "15m",
    "1h",
    "4h",
    "8h",
    "12h",
    "1d",
    "1w",
)


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _norm(x: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 0.0
    return _clip((x - lo) / (hi - lo))


def _pct(a: float, b: float) -> Optional[float]:
    if b is None or b <= 0:
        return None
    return (a - b) / b * 100.0


def rsi_wilder(closes: Sequence[float], period: int = 14) -> List[Optional[float]]:
    n = len(closes)
    out: List[Optional[float]] = [None] * n
    if n < period + 1:
        return out
    gains = []
    losses = []
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_g = sum(gains) / period
    avg_l = sum(losses) / period
    if avg_l == 0:
        out[period] = 100.0
    else:
        rs = avg_g / avg_l
        out[period] = 100.0 - 100.0 / (1.0 + rs)
    for i in range(period + 1, n):
        d = closes[i] - closes[i - 1]
        g, l = max(d, 0.0), max(-d, 0.0)
        avg_g = (avg_g * (period - 1) + g) / period
        avg_l = (avg_l * (period - 1) + l) / period
        if avg_l == 0:
            out[i] = 100.0
        else:
            rs = avg_g / avg_l
            out[i] = 100.0 - 100.0 / (1.0 + rs)
    return out


def swing_indices(vals: Sequence[float], left: int = 2, right: int = 2) -> List[int]:
    out = []
    n = len(vals)
    for i in range(left, n - right):
        window = vals[i - left : i + right + 1]
        if vals[i] == max(window) or vals[i] == min(window):
            # only pure local extremes
            if vals[i] >= max(vals[i - left : i]) and vals[i] >= max(
                vals[i + 1 : i + right + 1]
            ):
                out.append(i)
            elif vals[i] <= min(vals[i - left : i]) and vals[i] <= min(
                vals[i + 1 : i + right + 1]
            ):
                out.append(i)
    return out


def swing_lows(lows: Sequence[float], left: int = 2, right: int = 2) -> List[int]:
    out = []
    n = len(lows)
    for i in range(left, n - right):
        if lows[i] <= min(lows[i - left : i + right + 1]):
            out.append(i)
    return out


def swing_highs(highs: Sequence[float], left: int = 2, right: int = 2) -> List[int]:
    out = []
    n = len(highs)
    for i in range(left, n - right):
        if highs[i] >= max(highs[i - left : i + right + 1]):
            out.append(i)
    return out


def fetch_bars(
    market: str, symbol: str, tf: str = "15m", limit: int = 96
) -> List[Bar]:
    try:
        from ..movers.klines import KlineClient

        client = KlineClient()
        bars = client.get_ohlcv(market, symbol, tf, limit=limit)
        client.close()
        return bars or []
    except Exception as e:
        logger.debug("fetch_bars %s %s %s: %s", market, symbol, tf, e)
        return []


def compute_fire_features(
    *,
    market: str,
    symbol: str,
    fire_px: float,
    fire_ts: float,
    peak_px: Optional[float] = None,
    peak_ts: Optional[float] = None,
    heat_breadth: Optional[int] = None,
    velocity_band: Optional[str] = None,
) -> Dict[str, Any]:
    """Fire-time chart features. Soft-fail → {}. """
    try:
        if not fire_px or fire_px <= 0:
            return {"ok": False, "error": "bad fire_px"}
        bars5 = fetch_bars(market, symbol, "5m", 96)
        bars15 = fetch_bars(market, symbol, "15m", 96)
        bars1h = fetch_bars(market, symbol, "1h", 120)
        bars_by_tf: Dict[str, List[Bar]] = {}
        for tf in CANDIDATE_TFS:
            if tf == "5m":
                bars_by_tf[tf] = bars5
            elif tf == "15m":
                bars_by_tf[tf] = bars15
            elif tf == "1h":
                bars_by_tf[tf] = bars1h
            else:
                bars_by_tf[tf] = fetch_bars(market, symbol, tf, 80)
        if not bars5 and not bars15:
            return {"ok": False, "error": "no klines"}

        # Peak from ring or swing high on 15m
        if peak_px is None or peak_px <= 0:
            src = bars15 or bars5
            highs = [b["h"] for b in src[-20:]]
            peak_px = max(highs) if highs else fire_px
            peak_ts = fire_ts - 900

        dd_pct = abs(_pct(fire_px, peak_px) or 0.0)
        minutes = max((fire_ts - (peak_ts or fire_ts - 900)) / 60.0, 1 / 60)
        vel = dd_pct / minutes
        if velocity_band:
            band = velocity_band.upper()
        elif vel >= 2.0:
            band = "PANIC"
        elif vel >= 0.8:
            band = "FAST"
        else:
            band = "GRIND"

        # red streak 5m
        red_streak = 0
        for b in reversed(bars5 or []):
            if b["c"] < b["o"]:
                red_streak += 1
            else:
                break

        bodies = []
        for b in (bars5 or [])[-max(red_streak, 3) :]:
            rng = b["h"] - b["l"]
            if rng > 0:
                bodies.append(abs(b["c"] - b["o"]) / rng)
        body_ratio = sum(bodies) / len(bodies) if bodies else 0.5
        grind_flag = 1.0 if band == "GRIND" else 0.0
        sharp_score = _clip(
            0.45 * _norm(vel, 0, 4)
            + 0.25 * _norm(float(red_streak), 0, 8)
            + 0.20 * body_ratio
            + 0.10 * (1.0 - grind_flag)
        )

        # AD depth from 15m swing drops
        ad = _ad_depth(bars15 or bars5, fire_px, dd_pct)

        # Volume
        vol = _volume_scores(bars5 or bars15, red_streak)

        # RSI + div
        rsi_pack = _rsi_div(bars5 or bars15)

        setup_prior = _clip(
            0.30 * sharp_score
            + 0.30 * ad["ad_score"]
            + 0.25 * vol["vol_score"]
            + 0.15 * rsi_pack["rsi_score"]
        )
        if heat_breadth is not None and int(heat_breadth) >= 3:
            setup_prior = min(1.0, setup_prior + 0.05)
        if vol.get("vol_flag") == "dry" and band == "GRIND":
            setup_prior *= 0.75

        ad_by_tf = []
        for tf, bars in bars_by_tf.items():
            if not bars or len(bars) < 12:
                continue
            pack = _tf_ad_pack(tf, bars, fire_px, dd_pct)
            if pack:
                ad_by_tf.append(pack)

        regime_guess = _regime_guess(bars_by_tf.get("4h") or bars1h or bars15, fire_px)
        hint = _tf_hint(ad_by_tf, band)
        timing = _timing_gate(ad_by_tf, hint.get("tf") if hint else None)
        alignment = _factor_alignment(
            timing=timing,
            band=band,
            regime=regime_guess,
            vol_flag=vol.get("vol_flag"),
            heat_breadth=heat_breadth,
        )

        return {
            "ok": True,
            "sharp_score": round(sharp_score, 4),
            "vel_pct_min": round(vel, 4),
            "band": band,
            "dd_pct": round(dd_pct, 4),
            "red_streak_5m": red_streak,
            "ad_depth_ratio": ad.get("ad_depth_ratio"),
            "ad_zone": ad.get("ad_zone"),
            "ad_ready": ad.get("ad_ready"),
            "ad_median": ad.get("ad_median"),
            "ad_score": ad.get("ad_score"),
            "vol_ratio": vol.get("vol_ratio"),
            "vol_flag": vol.get("vol_flag"),
            "vol_score": vol.get("vol_score"),
            "rsi_now_5m": rsi_pack.get("rsi_now"),
            "div_bull": rsi_pack.get("div_bull"),
            "div_strength": rsi_pack.get("div_strength"),
            "rsi_score": rsi_pack.get("rsi_score"),
            "setup_prior": round(setup_prior, 4),
            "heat_breadth": heat_breadth,
            # P1 production index (Rule 1 / 1.5 / 2.5) — observations, not a decision
            "ad_by_tf": ad_by_tf,
            "regime_guess": regime_guess,
            "tf_hint": hint.get("tf"),
            "tf_hint_reason": hint.get("reason"),
            "tf_hint_kind": "hint",
            "timing_gate": timing,
            "factor_alignment": alignment,
        }
    except Exception as e:
        logger.debug("compute_fire_features: %s", e)
        return {"ok": False, "error": str(e)[:160]}


def _ad_depth(bars: List[Bar], fire_px: float, dd_pct: float) -> Dict[str, Any]:
    if len(bars) < 20:
        return {
            "ad_ready": False,
            "ad_score": 0.5,
            "ad_zone": "unknown",
            "ad_median": None,
            "ad_depth_ratio": None,
        }
    highs = [b["h"] for b in bars]
    lows = [b["l"] for b in bars]
    sh = swing_highs(highs)
    sl = swing_lows(lows)
    drops = []
    for hi in sh:
        # next swing low after high
        after = [j for j in sl if j > hi]
        if not after:
            continue
        lo_i = after[0]
        if highs[hi] <= 0:
            continue
        # regime filter
        if not (0.85 * fire_px <= highs[hi] <= 1.25 * fire_px):
            continue
        d = abs(_pct(lows[lo_i], highs[hi]) or 0)
        if d > 0.5:
            drops.append(d)
    drops = drops[-12:]
    if len(drops) < 3:
        return {
            "ad_ready": False,
            "ad_score": 0.5,
            "ad_zone": "unknown",
            "ad_median": None,
            "ad_depth_ratio": None,
        }
    drops_s = sorted(drops)
    med = drops_s[len(drops_s) // 2]
    ratio = dd_pct / med if med > 0 else None
    if ratio is None:
        zone, score = "unknown", 0.5
    elif ratio < 0.75:
        zone, score = "shallow", 0.35
    elif ratio <= 1.15:
        zone, score = "at_ad", 0.85
    elif ratio <= 1.60:
        zone, score = "extension", 1.0
    else:
        zone, score = "deep_ext", 0.70
    return {
        "ad_ready": True,
        "ad_median": round(med, 4),
        "ad_depth_ratio": round(ratio, 4) if ratio is not None else None,
        "ad_zone": zone,
        "ad_score": score,
    }


def _volume_scores(bars: List[Bar], dump_bars: int) -> Dict[str, Any]:
    if len(bars) < 25:
        return {"vol_score": 0.5, "vol_flag": "unknown", "vol_ratio": None}
    n_dump = max(dump_bars, 3)
    dump = bars[-n_dump:]
    base = bars[-(n_dump + 20) : -n_dump]
    if not base:
        return {"vol_score": 0.5, "vol_flag": "unknown", "vol_ratio": None}
    vol_base = sum(b["v"] for b in base) / len(base)
    if vol_base <= 0:
        return {"vol_score": 0.5, "vol_flag": "unknown", "vol_ratio": None}
    vol_dump = sum(b["v"] for b in dump) / len(dump)
    vol_ratio = vol_dump / vol_base
    vol_climax = bars[-1]["v"] / vol_base
    vol_score = _clip(
        0.6 * _norm(vol_ratio, 0.8, 2.5) + 0.4 * _norm(vol_climax, 1.0, 3.0)
    )
    if vol_ratio >= 1.4:
        flag = "expand"
    elif vol_ratio >= 0.9:
        flag = "flat"
    else:
        flag = "dry"
    return {
        "vol_ratio": round(vol_ratio, 4),
        "vol_flag": flag,
        "vol_score": round(vol_score, 4),
    }


def _rsi_div(bars: List[Bar]) -> Dict[str, Any]:
    if len(bars) < 30:
        return {
            "rsi_now": None,
            "div_bull": False,
            "div_strength": 0.0,
            "rsi_score": 0.3,
        }
    closes = [b["c"] for b in bars]
    lows = [b["l"] for b in bars]
    rsis = rsi_wilder(closes, 14)
    rsi_now = rsis[-1]
    lows_idx = swing_lows(lows[-40:]) if len(lows) >= 40 else swing_lows(lows)
    # map to full index
    offset = max(0, len(lows) - 40)
    lows_idx = [i + offset for i in lows_idx]
    div_bull = False
    div_strength = 0.0
    if len(lows_idx) >= 2 and rsis[-1] is not None:
        i0, i1 = lows_idx[-2], lows_idx[-1]
        if i1 > i0 and rsis[i0] is not None and rsis[i1] is not None:
            if lows[i1] < lows[i0] * 0.998 and rsis[i1] > rsis[i0] + 1.0:
                div_bull = True
                div_strength = _clip((rsis[i1] - rsis[i0]) / 15.0)
    rsi_oversold = rsi_now is not None and rsi_now <= 30
    if rsi_now is None:
        rsi_score = 0.3
    else:
        oversold_part = (
            1.0
            if rsi_oversold
            else (_norm(40 - rsi_now, 0, 20) if rsi_now < 40 else 0.0)
        )
        rsi_score = 0.55 * div_strength + 0.45 * oversold_part
    return {
        "rsi_now": round(rsi_now, 2) if rsi_now is not None else None,
        "div_bull": div_bull,
        "div_strength": round(div_strength, 4),
        "rsi_score": round(_clip(rsi_score), 4),
    }


def _red_streak(bars: List[Bar]) -> int:
    """Closed-bar consecutive reds — see red_streak.py / Rule 2.5."""
    return consecutive_red_streak(bars, include_forming=False)


def _tf_ad_pack(
    tf: str, bars: List[Bar], fire_px: float, dd_pct: float
) -> Optional[Dict[str, Any]]:
    ad = _ad_depth(bars, fire_px, dd_pct)
    reds_pack = streak_pack(bars)
    reds = int(reds_pack.get("red_streak") or 0)
    vol = _volume_scores(bars, max(reds, 3))
    last_v = None
    vol_panic_bar = False
    try:
        last = bars[-1]
        last_v = float(last.get("v") or 0)
        base = bars[-(max(reds, 3) + 20) : -max(reds, 3)] or bars[:-1]
        vb = sum(float(b.get("v") or 0) for b in base) / max(len(base), 1)
        if vb > 0 and last_v >= 1.6 * vb:
            vol_panic_bar = True
    except Exception:
        last_v = None
    highs = [b["h"] for b in bars]
    in_range = False
    if fire_px and highs:
        mx = max(highs[-20:]) if len(highs) >= 8 else max(highs)
        mn = min(b["l"] for b in bars[-20:]) if len(bars) >= 8 else min(b["l"] for b in bars)
        if mn > 0:
            in_range = mn * 0.85 <= fire_px <= mx * 1.15
    n_swings = len(swing_highs(highs)) + len(swing_lows([b["l"] for b in bars]))
    ad_met = ad.get("ad_zone") in ("at_ad", "extension", "deep_ext")
    return {
        "tf": tf,
        "ad_median": ad.get("ad_median"),
        "ad_zone": ad.get("ad_zone"),
        "ad_ready": bool(ad.get("ad_ready")),
        "ad_depth_ratio": ad.get("ad_depth_ratio"),
        "n_swings": n_swings,
        "in_range": in_range,
        "red_streak": reds,
        "red_label": reds_pack.get("red_label") or streak_label(reds),
        "entry_red_window": bool(reds_pack.get("entry_red_window")),
        "vol_flag": vol.get("vol_flag"),
        "vol_ratio": vol.get("vol_ratio"),
        "vol_on_last_bar": last_v,
        "vol_panic_bar": vol_panic_bar,
        "ad_met": ad_met,
        "first_red": reds == 1,
    }


def _regime_guess(htf_bars: Optional[List[Bar]], fire_px: float) -> str:
    if not htf_bars or not fire_px or fire_px <= 0:
        return "unknown"
    highs = [b["h"] for b in htf_bars[-30:]]
    lows = [b["l"] for b in htf_bars[-30:]]
    if not highs or not lows:
        return "unknown"
    hi, lo = max(highs), min(lows)
    if hi <= 0:
        return "unknown"
    # Near range high → discovery; near range low → new lows
    if fire_px >= hi * 0.97:
        return "new_high"
    if fire_px <= lo * 1.03:
        return "new_low"
    return "familiar"


def _tf_hint(ad_by_tf: List[Dict[str, Any]], band: str) -> Dict[str, Any]:
    """Heuristic only — not a P2 decision."""
    if not ad_by_tf:
        return {}
    scored = []
    for p in ad_by_tf:
        if not p.get("ad_ready") and p.get("n_swings", 0) < 4:
            continue
        reds = int(p.get("red_streak") or 0)
        score = 0.0
        if p.get("ad_met"):
            score += 2.0
        if 3 <= reds <= 5:
            score += 2.0
        elif reds >= 2:
            score += 0.5
        if p.get("vol_panic_bar"):
            score += 1.5
        if p.get("in_range"):
            score += 0.5
        if band == "GRIND" and p["tf"] in ("5m", "15m"):
            score -= 1.0
        scored.append((score, p))
    if not scored:
        return {"tf": ad_by_tf[0]["tf"], "reason": "first readable TF"}
    scored.sort(key=lambda x: -x[0])
    best = scored[0][1]
    return {
        "tf": best["tf"],
        "reason": (
            f"hint {best['tf']} ad={best.get('ad_zone')} "
            f"reds={best.get('red_streak')} vol_panic={best.get('vol_panic_bar')}"
        ),
    }


def _timing_gate(
    ad_by_tf: List[Dict[str, Any]], hint_tf: Optional[str]
) -> Dict[str, Any]:
    pack = None
    if hint_tf:
        pack = next((p for p in ad_by_tf if p.get("tf") == hint_tf), None)
    if pack is None and ad_by_tf:
        pack = ad_by_tf[0]
    if not pack:
        return {
            "red_streak": None,
            "ad_met": False,
            "first_red": False,
            "vol_panic_on_that_bar": False,
            "tf": None,
        }
    reds = int(pack.get("red_streak") or 0)
    return {
        "tf": pack.get("tf"),
        "red_streak": reds,
        "red_label": pack.get("red_label") or streak_label(reds),
        "ad_met": bool(pack.get("ad_met")),
        "first_red": reds == 1,
        "first_or_second_red": 0 < reds < 3,
        "vol_panic_on_that_bar": bool(pack.get("vol_panic_bar")),
        "typical_red_band": bool(pack.get("entry_red_window")),
    }


def _factor_alignment(
    *,
    timing: Dict[str, Any],
    band: str,
    regime: str,
    vol_flag: Optional[str],
    heat_breadth: Optional[int],
) -> Dict[str, Any]:
    """How well the live dump matches printed history. Size hint — chart wins."""
    reds = int(timing.get("red_streak") or 0)
    factors = {
        "ad_in_range": "yes" if timing.get("ad_met") else "no",
        "volume": (
            "yes"
            if timing.get("vol_panic_on_that_bar") or vol_flag == "expand"
            else ("weak" if vol_flag == "flat" else "no")
        ),
        "pace": (
            "yes"
            if str(band).upper() == "PANIC"
            else ("weak" if str(band).upper() == "FAST" else "no")
        ),
        "red_streak": (
            "yes"
            if 3 <= reds <= 5
            else ("weak" if reds in (2, 6) else "no")
        ),
        "regime": (
            "yes"
            if regime == "familiar"
            else ("weak" if regime in ("new_low", "new_high") else "no")
        ),
        "breadth": (
            "yes"
            if heat_breadth is not None and int(heat_breadth) >= 3
            else ("weak" if heat_breadth is not None else "no")
        ),
    }
    yes = sum(1 for v in factors.values() if v == "yes")
    weak = sum(1 for v in factors.values() if v == "weak")
    score = yes + 0.5 * weak
    if score >= 4.5:
        size_hint = "press"
    elif score >= 3.0:
        size_hint = "standard"
    elif score >= 1.5:
        size_hint = "lean"
    else:
        size_hint = "pass"
    return {
        "factors": factors,
        "yes_count": yes,
        "weak_count": weak,
        "score": round(score, 2),
        "size_hint": size_hint,
        "note": "vs this chart history — size to stack; candles beat speech",
    }


def apply_teach_feature_tags(
    feats: Dict[str, Any],
    *,
    chips: Optional[Sequence[str]] = None,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    """Parse owner tags from chips/note into features (judgment overlay)."""
    out = dict(feats or {})
    tokens: List[str] = []
    for c in chips or []:
        tokens.append(str(c).strip().lower())
    if note:
        for raw in str(note).replace(",", " ").split():
            tokens.append(raw.strip().lower())
    for t in tokens:
        if t.startswith("tf:") and len(t) > 3:
            out["tf_taught"] = t.split(":", 1)[1]
        elif t.startswith("regime:") and len(t) > 7:
            out["regime_taught"] = t.split(":", 1)[1]
        elif t.startswith("vol:") and len(t) > 4:
            out["vol_taught"] = t.split(":", 1)[1]
        elif t.startswith("reds:") and len(t) > 5:
            try:
                out["reds_taught"] = int(t.split(":", 1)[1])
            except ValueError:
                pass
        elif t in ("first_red", "first-red"):
            out["first_red_taught"] = True
    return out


def setup_posterior(
    setup_prior: float,
    max_bounce_pct: Optional[float],
    max_dd_pct: Optional[float],
    ad_median: Optional[float] = None,
) -> float:
    ref = ad_median if ad_median and ad_median > 0 else 5.0
    bounce = float(max_bounce_pct or 0.0)
    dd = abs(float(max_dd_pct or 0.0))
    path = _clip(0.7 * _norm(bounce, 0, ref) + 0.3 * (1.0 - _norm(dd, 0, ref)))
    return round(_clip(0.55 * setup_prior + 0.45 * path), 4)
