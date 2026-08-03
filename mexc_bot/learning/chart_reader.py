"""Discretionary AD chart reader for the owner's book (not whole market).

Reads multi-TF OHLCV and produces a human-readable thesis + structure:
regime, AD estimate, panic vs grind, volume climax, RSI divergence, invalidation.
Soft-fail if klines unavailable — never blocks trading path.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .chart_features import (
    Bar,
    _pct,
    fetch_bars,
    rsi_wilder,
    swing_highs,
    swing_lows,
    compute_fire_features,
)

logger = logging.getLogger(__name__)


def _sma(vals: Sequence[float], n: int) -> Optional[float]:
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def _last_drops(
    highs: Sequence[float], lows: Sequence[float], max_n: int = 12
) -> List[float]:
    sh = swing_highs(list(highs))
    sl = swing_lows(list(lows))
    drops = []
    for hi in sh:
        after = [j for j in sl if j > hi]
        if not after or highs[hi] <= 0:
            continue
        lo_i = after[0]
        d = abs(_pct(lows[lo_i], highs[hi]) or 0)
        if 0.8 <= d <= 40:
            drops.append(d)
    return drops[-max_n:]


def read_chart(
    market: str,
    symbol: str,
    *,
    mark_price: Optional[float] = None,
    fire_price: Optional[float] = None,
    fire_ts: Optional[float] = None,
    peak_price: Optional[float] = None,
    heat_breadth: Optional[int] = None,
    velocity_band: Optional[str] = None,
) -> Dict[str, Any]:
    """Full discretionary read for one book symbol.

    Returns thesis prose + structured fields for agent/beliefs.
    """
    now = time.time()
    mkt = (market or "futures").lower()
    sym = symbol or ""
    try:
        b5 = fetch_bars(mkt, sym, "5m", 120)
        b15 = fetch_bars(mkt, sym, "15m", 120)
        b1h = fetch_bars(mkt, sym, "1h", 150)
    except Exception as e:
        return {
            "ok": False,
            "symbol": sym,
            "market": mkt,
            "error": str(e)[:160],
            "thesis": f"{sym}: chart read failed ({e}).",
        }

    if not b5 and not b15 and not b1h:
        return {
            "ok": False,
            "symbol": sym,
            "market": mkt,
            "error": "no klines",
            "thesis": f"{sym}: no candle data available right now.",
        }

    primary = b15 or b5 or b1h
    last = primary[-1]
    mark = float(mark_price or last["c"])
    fire_px = float(fire_price or mark)
    fts = float(fire_ts or now)

    # Peak: explicit or swing high recent
    peak = peak_price
    if not peak or peak <= 0:
        window = primary[-24:]
        peak = max(b["h"] for b in window) if window else mark

    # Feature pack (scores)
    feats = compute_fire_features(
        market=mkt,
        symbol=sym,
        fire_px=fire_px,
        fire_ts=fts,
        peak_px=float(peak),
        peak_ts=fts - 900,
        heat_breadth=heat_breadth,
        velocity_band=velocity_band,
    )

    # Regime 1h
    c1 = [b["c"] for b in b1h] if b1h else [b["c"] for b in primary]
    sma20 = _sma(c1, 20)
    sma50 = _sma(c1, min(50, len(c1)))
    if sma20 and sma50:
        if c1[-1] > sma20 > sma50:
            regime = "uptrend"
        elif c1[-1] < sma20 < sma50:
            regime = "downtrend"
        else:
            regime = "range_or_transition"
    else:
        regime = "unknown"

    # AD estimate from 15m history
    src = b15 or b5
    drops = _last_drops([b["h"] for b in src], [b["l"] for b in src]) if src else []
    ad_median = sorted(drops)[len(drops) // 2] if len(drops) >= 3 else None
    dd_now = abs(_pct(fire_px, float(peak)) or 0)
    if ad_median and ad_median > 0:
        ratio = dd_now / ad_median
        if ratio < 0.75:
            ad_zone = "shallow_of_typical_AD"
        elif ratio <= 1.15:
            ad_zone = "into_typical_AD"
        elif ratio <= 1.6:
            ad_zone = "extension_past_AD"
        else:
            ad_zone = "deep_extension"
    else:
        ratio = None
        ad_zone = "AD_history_forming"

    # Panic vs grind narrative
    band = (feats.get("band") or velocity_band or "—").upper()
    vol_flag = feats.get("vol_flag") or "unknown"
    if band == "PANIC" and vol_flag == "expand":
        pace = "Sharp panic dump with volume expansion — classic AD panic liquidity."
    elif band == "PANIC" and vol_flag == "dry":
        pace = "Fast price drop but dry volume — caution (move without participation)."
    elif band == "GRIND":
        pace = "Grind / slow bleed — low panic, higher trend risk for mean-reversion."
    elif band == "FAST":
        pace = "Fast dump — intermediate; confirm volume and AD zone before full layers."
    else:
        pace = "Pace unclear from candles alone."

    # RSI / div
    rsi_now = feats.get("rsi_now_5m")
    div_bull = bool(feats.get("div_bull"))
    if div_bull:
        rsi_line = (
            f"Bullish RSI divergence proxy on 5m (price LL, RSI HL); "
            f"RSI≈{rsi_now}."
        )
    elif rsi_now is not None and rsi_now <= 30:
        rsi_line = f"5m RSI oversold (~{rsi_now}); no clear bullish div yet."
    elif rsi_now is not None:
        rsi_line = f"5m RSI≈{rsi_now} — not extreme."
    else:
        rsi_line = "RSI unavailable."

    # Structure invalidation
    recent_low = min(b["l"] for b in (b15 or primary)[-8:])
    invalidation = {
        "below": round(recent_low * 0.995, 8),
        "note": "Break and hold under recent swing low / last dump low invalidates bounce thesis",
    }

    # Levels
    levels = {
        "mark": mark,
        "peak_ref": float(peak),
        "fire_or_focus": fire_px,
        "recent_swing_low": recent_low,
        "drop_from_peak_pct": round(dd_now, 2),
        "ad_median_pct": round(ad_median, 2) if ad_median else None,
        "ad_depth_ratio": round(ratio, 2) if ratio is not None else None,
    }

    # What is happening now (last few 5m bars)
    now_bits = []
    if b5 and len(b5) >= 3:
        last3 = b5[-3:]
        reds = sum(1 for b in last3 if b["c"] < b["o"])
        now_bits.append(f"last 3×5m: {reds}/3 red")
        ch = _pct(b5[-1]["c"], b5[-4]["c"] if len(b5) > 4 else b5[0]["c"])
        if ch is not None:
            now_bits.append(f"~{ch:+.1f}% over last ~20m")
    happening = "; ".join(now_bits) if now_bits else "see mark vs peak"

    thesis_lines = [
        f"{sym} [{mkt}] — discretionary AD read",
        f"Regime (1h): {regime}.",
        f"Dump: {pace}",
        f"AD: typical historical drop ≈{ad_median:.1f}%" if ad_median else "AD: not enough swing history yet.",
        f"Now: {dd_now:.1f}% off peak → zone **{ad_zone}**"
        + (f" (ratio {ratio:.2f}× AD)" if ratio else "")
        + ".",
        f"Volume: {vol_flag}. {rsi_line}",
        f"Happening now: {happening}.",
        f"Invalidation: below ~{invalidation['below']} ({invalidation['note']}).",
        f"Heat breadth: {heat_breadth if heat_breadth is not None else 'n/a'}.",
    ]
    # Plain text without markdown bold for voice
    thesis = "\n".join(
        ln.replace("**", "") for ln in thesis_lines
    )

    bias = "neutral"
    if band == "PANIC" and ad_zone in ("into_typical_AD", "extension_past_AD") and vol_flag == "expand":
        bias = "ad_long_bias"
    elif band == "GRIND" or vol_flag == "dry":
        bias = "no_trade_bias"
    elif ad_zone == "shallow_of_typical_AD":
        bias = "wait_deeper"
    elif ad_zone == "deep_extension" and band in ("PANIC", "FAST"):
        bias = "scout_or_add"

    return {
        "ok": True,
        "symbol": sym,
        "market": mkt,
        "ts": now,
        "regime": regime,
        "pace": band,
        "vol_flag": vol_flag,
        "ad_zone": ad_zone,
        "ad_median_pct": ad_median,
        "ad_depth_ratio": ratio,
        "rsi_now": rsi_now,
        "div_bull": div_bull,
        "bias": bias,
        "levels": levels,
        "invalidation": invalidation,
        "features": feats,
        "thesis": thesis,
        "happening_now": happening,
        "history_summary": {
            "n_historical_drops": len(drops),
            "drop_samples_pct": [round(x, 2) for x in drops[-6:]],
            "bars_5m": len(b5),
            "bars_15m": len(b15),
            "bars_1h": len(b1h),
        },
    }


def read_book_charts(
    book: Sequence[Tuple[str, str]],
    *,
    max_symbols: int = 25,
) -> List[Dict[str, Any]]:
    """Read charts for book list [(market, symbol), ...]."""
    out = []
    for mkt, sym in list(book)[:max_symbols]:
        out.append(read_chart(mkt, sym))
    return out


class ChartProfileStore:
    """Persist latest chart thesis per book ticker in SQLite (via EventStore connection)."""

    def __init__(self, event_store):
        self.store = event_store
        self._ensure()

    def _ensure(self) -> None:
        with self.store._lock:
            self.store._get_conn().execute(
                """
                CREATE TABLE IF NOT EXISTS chart_profiles (
                    user_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    market TEXT NOT NULL,
                    thesis TEXT,
                    read_json TEXT,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (user_id, symbol, market)
                )
                """
            )

    def save(self, user_id: int, read: Dict[str, Any]) -> None:
        if not read.get("symbol"):
            return
        now = time.time()
        with self.store._lock:
            self.store._get_conn().execute(
                """
                INSERT INTO chart_profiles (user_id, symbol, market, thesis, read_json, updated_at)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(user_id, symbol, market) DO UPDATE SET
                    thesis=excluded.thesis,
                    read_json=excluded.read_json,
                    updated_at=excluded.updated_at
                """,
                (
                    int(user_id),
                    str(read.get("symbol")),
                    str(read.get("market") or "futures"),
                    read.get("thesis") or "",
                    json.dumps(read),
                    now,
                ),
            )

    def get(
        self, user_id: int, symbol: str, market: str = "futures"
    ) -> Optional[Dict[str, Any]]:
        with self.store._lock:
            row = self.store._get_conn().execute(
                """
                SELECT * FROM chart_profiles
                WHERE user_id=? AND symbol=? AND market=?
                """,
                (int(user_id), symbol, market),
            ).fetchone()
        if not row:
            # try any market
            with self.store._lock:
                row = self.store._get_conn().execute(
                    """
                    SELECT * FROM chart_profiles
                    WHERE user_id=? AND UPPER(REPLACE(symbol,'_','')) LIKE ?
                    ORDER BY updated_at DESC LIMIT 1
                    """,
                    (
                        int(user_id),
                        f"%{symbol.upper().replace('_','').replace('USDT','')}%",
                    ),
                ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row["read_json"] or "{}")
        except Exception:
            return {"thesis": row["thesis"], "ok": True}

    def list_for_user(self, user_id: int, limit: int = 40) -> List[dict]:
        with self.store._lock:
            rows = self.store._get_conn().execute(
                """
                SELECT symbol, market, thesis, updated_at FROM chart_profiles
                WHERE user_id=? ORDER BY updated_at DESC LIMIT ?
                """,
                (int(user_id), limit),
            ).fetchall()
        return [dict(r) for r in rows]
