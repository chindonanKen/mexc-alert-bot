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
            if market.lower() == "futures":
                candles = self._fetch_futures(symbol, tf)
            else:
                candles = self._fetch_spot(symbol, tf)
        except Exception as e:
            logger.debug("Kline fetch failed %s:%s %s: %s", market, symbol, tf, e)
            candles = []

        self._cache[key] = (now, candles)
        return candles

    def _fetch_spot(self, symbol: str, tf: str) -> List[Tuple[float, float]]:
        spot_iv, _ = _INTERVALS[tf]
        url = f"{self.spot_base}/klines"
        resp = self.session.get(
            url,
            params={"symbol": symbol.upper(), "interval": spot_iv, "limit": 50},
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        if not isinstance(data, list):
            return []
        # [openTime, open, high, low, close, volume, ...]
        # Drop last if it might be forming — MEXC includes current bar; treat last as open
        rows = data[:-1] if len(data) > 1 else data
        out: List[Tuple[float, float]] = []
        for row in rows:
            try:
                o = float(row[1])
                c = float(row[4])
                out.append((o, c))
            except (TypeError, ValueError, IndexError):
                continue
        return out

    def _fetch_futures(self, symbol: str, tf: str) -> List[Tuple[float, float]]:
        _, fut_iv = _INTERVALS[tf]
        # GET /api/v1/contract/kline/{symbol}?interval=Min15
        url = f"{self.futures_base}/contract/kline/{symbol.upper()}"
        resp = self.session.get(
            url,
            params={"interval": fut_iv},
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            return []
        payload = resp.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        # Common shapes: dict of arrays, or list of candles
        pairs = _parse_futures_kline_payload(data)
        if len(pairs) > 1:
            pairs = pairs[:-1]  # drop forming
        return pairs


def _parse_futures_kline_payload(data) -> List[Tuple[float, float]]:
    if data is None:
        return []
    if isinstance(data, dict):
        # Often: {"time":[], "open":[], "close":[], ...}
        opens = data.get("open") or data.get("o")
        closes = data.get("close") or data.get("c")
        if isinstance(opens, list) and isinstance(closes, list):
            out = []
            for o, c in zip(opens, closes):
                try:
                    out.append((float(o), float(c)))
                except (TypeError, ValueError):
                    continue
            return out
    if isinstance(data, list):
        out = []
        for row in data:
            if isinstance(row, dict):
                try:
                    o = float(row.get("open", row.get("o")))
                    c = float(row.get("close", row.get("c")))
                    out.append((o, c))
                except (TypeError, ValueError):
                    continue
            elif isinstance(row, (list, tuple)) and len(row) >= 5:
                try:
                    out.append((float(row[1]), float(row[4])))
                except (TypeError, ValueError):
                    continue
        return out
    return []


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
