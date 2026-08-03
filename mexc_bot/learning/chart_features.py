"""AD chart features from public OHLCV: sharpness, AD depth, volume, RSI divergence.

Soft-fail always — never block fires or learning writes.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

Bar = Dict[str, float]  # ts, o, h, l, c, v


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
