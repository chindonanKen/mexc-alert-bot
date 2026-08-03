"""Kline helpers for consecutive red-candle counts (fire-time enrichment).

Public MEXC REST only. Failures return empty — never block mover fires.
"""

from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

# interval key -> (spot interval, futures interval)
_INTERVALS = {
    "5m": ("5m", "Min5"),
    "15m": ("15m", "Min15"),
    "1h": ("60m", "Min60"),
    "4h": ("4h", "Hour4"),
}

CacheKey = Tuple[str, str, str]  # market, symbol, tf


class KlineClient:
    """Thin kline fetcher with short TTL cache."""

    def __init__(
        self,
        spot_base: str = "https://api.mexc.com/api/v3",
        futures_base: str = "https://contract.mexc.com/api/v1",
        timeout: float = 6.0,
        cache_ttl: float = 75.0,
    ):
        self.spot_base = spot_base.rstrip("/")
        self.futures_base = futures_base.rstrip("/")
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "mexc-alert-bot/0.4-klines"})
        self._cache: Dict[CacheKey, Tuple[float, List[Tuple[float, float]]]] = {}

    def close(self) -> None:
        try:
            self.session.close()
        except Exception:
            pass

    def consecutive_reds(
        self,
        market: str,
        symbol: str,
        timeframes: Optional[List[str]] = None,
    ) -> Dict[str, int]:
        """Return {tf: consecutive_closed_red_count} for requested TFs."""
        tfs = timeframes or ["5m", "15m", "1h", "4h"]
        out: Dict[str, int] = {}
        for tf in tfs:
            if tf not in _INTERVALS:
                continue
            candles = self._get_ohlc_closed(market, symbol, tf)
            out[tf] = consecutive_red_count(candles)
        return out

    def get_ohlcv(
        self, market: str, symbol: str, tf: str, limit: int = 96
    ) -> List[dict]:
        """Full OHLCV closed bars oldest→newest. Soft-fail → []."""
        if tf not in _INTERVALS:
            return []
        try:
            if market.lower() == "futures":
                bars = self._fetch_futures_ohlcv(symbol, tf, limit=limit)
            else:
                bars = self._fetch_spot_ohlcv(symbol, tf, limit=limit)
            if len(bars) > 1:
                bars = bars[:-1]  # drop forming
            return bars
        except Exception as e:
            logger.debug("get_ohlcv failed %s:%s %s: %s", market, symbol, tf, e)
            return []

    def _get_ohlc_closed(
        self, market: str, symbol: str, tf: str
    ) -> List[Tuple[float, float]]:
        """List of (open, close) for closed candles, oldest → newest."""
        key: CacheKey = (market.lower(), symbol.upper(), tf)
        now = time.time()
        hit = self._cache.get(key)
        if hit and (now - hit[0]) < self.cache_ttl:
            return hit[1]

        candles: List[Tuple[float, float]] = []
        try:
            bars = self.get_ohlcv(market, symbol, tf, limit=50)
            candles = [(b["o"], b["c"]) for b in bars]
        except Exception as e:
            logger.debug("Kline fetch failed %s:%s %s: %s", market, symbol, tf, e)
            candles = []

        self._cache[key] = (now, candles)
        return candles

    def _fetch_spot_ohlcv(
        self, symbol: str, tf: str, limit: int = 96
    ) -> List[dict]:
        spot_iv, _ = _INTERVALS[tf]
        sym = symbol.upper().replace("_", "")
        if not sym.endswith("USDT") and "USDT" not in sym:
            sym = sym + "USDT"
        url = f"{self.spot_base}/klines"
        resp = self.session.get(
            url,
            params={
                "symbol": sym,
                "interval": spot_iv,
                "limit": max(10, min(int(limit), 500)),
            },
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        if not isinstance(data, list):
            return []
        out: List[dict] = []
        for row in data:
            try:
                out.append(
                    {
                        "ts": float(row[0]) / 1000.0,
                        "o": float(row[1]),
                        "h": float(row[2]),
                        "l": float(row[3]),
                        "c": float(row[4]),
                        "v": float(row[5]),
                    }
                )
            except (TypeError, ValueError, IndexError):
                continue
        return out

    def _fetch_futures_ohlcv(
        self, symbol: str, tf: str, limit: int = 96
    ) -> List[dict]:
        _, fut_iv = _INTERVALS[tf]
        sym = symbol.upper()
        if "_" not in sym and sym.endswith("USDT"):
            # compact → try BASE_USDT
            base = sym[:-4]
            sym = f"{base}_USDT"
        url = f"{self.futures_base}/contract/kline/{sym}"
        resp = self.session.get(
            url,
            params={"interval": fut_iv},
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            return []
        payload = resp.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        return _parse_futures_ohlcv(data, limit=limit)


def _parse_futures_ohlcv(data, limit: int = 96) -> List[dict]:
    if data is None:
        return []
    out: List[dict] = []
    if isinstance(data, dict):
        times = data.get("time") or data.get("t") or []
        opens = data.get("open") or data.get("o") or []
        highs = data.get("high") or data.get("h") or []
        lows = data.get("low") or data.get("l") or []
        closes = data.get("close") or data.get("c") or []
        vols = data.get("vol") or data.get("volume") or data.get("v") or []
        n = min(len(opens), len(closes), len(highs), len(lows))
        for i in range(n):
            try:
                ts = float(times[i]) if i < len(times) else float(i)
                if ts > 1e12:
                    ts /= 1000.0
                out.append(
                    {
                        "ts": ts,
                        "o": float(opens[i]),
                        "h": float(highs[i]),
                        "l": float(lows[i]),
                        "c": float(closes[i]),
                        "v": float(vols[i]) if i < len(vols) else 0.0,
                    }
                )
            except (TypeError, ValueError, IndexError):
                continue
        return out[-limit:] if limit else out
    if isinstance(data, list):
        for row in data:
            try:
                if isinstance(row, dict):
                    ts = float(row.get("time", row.get("t", 0)))
                    if ts > 1e12:
                        ts /= 1000.0
                    out.append(
                        {
                            "ts": ts,
                            "o": float(row.get("open", row.get("o"))),
                            "h": float(row.get("high", row.get("h"))),
                            "l": float(row.get("low", row.get("l"))),
                            "c": float(row.get("close", row.get("c"))),
                            "v": float(row.get("vol", row.get("volume", row.get("v", 0)))),
                        }
                    )
                elif isinstance(row, (list, tuple)) and len(row) >= 6:
                    ts = float(row[0])
                    if ts > 1e12:
                        ts /= 1000.0
                    out.append(
                        {
                            "ts": ts,
                            "o": float(row[1]),
                            "h": float(row[2]),
                            "l": float(row[3]),
                            "c": float(row[4]),
                            "v": float(row[5]),
                        }
                    )
            except (TypeError, ValueError, IndexError):
                continue
        return out[-limit:] if limit else out
    return []


def _parse_futures_kline_payload(data) -> List[Tuple[float, float]]:
    bars = _parse_futures_ohlcv(data)
    return [(b["o"], b["c"]) for b in bars]


def consecutive_red_count(candles: List[Tuple[float, float]]) -> int:
    """Count consecutive reds (close < open) ending at the newest closed candle."""
    if not candles:
        return 0
    n = 0
    for o, c in reversed(candles):
        if c < o:
            n += 1
        else:
            break
    return n


def format_reds_line(counts: Dict[str, int]) -> str:
    if not counts:
        return ""
    order = ["5m", "15m", "1h", "4h"]
    parts = []
    for tf in order:
        if tf in counts:
            parts.append(f"{tf}×{counts[tf]}")
    if not parts:
        for tf, n in counts.items():
            parts.append(f"{tf}×{n}")
    return "Reds (closed): " + " · ".join(parts)
