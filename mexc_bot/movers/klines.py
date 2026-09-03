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
    "1m": ("1m", "Min1"),
    "5m": ("5m", "Min5"),
    "15m": ("15m", "Min15"),
    "1h": ("60m", "Min60"),
    "4h": ("4h", "Hour4"),
    "8h": ("8h", "Hour8"),
    "12h": ("12h", "Hour12"),
    "1d": ("1d", "Day1"),
    "1D": ("1d", "Day1"),
    "1w": ("1w", "Week1"),
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
        self,
        market: str,
        symbol: str,
        tf: str,
        limit: int = 96,
        *,
        include_forming: bool = False,
    ) -> List[dict]:
        """Full OHLCV oldest→newest. Soft-fail → []. Forming dropped unless asked."""
        if tf not in _INTERVALS:
            return []
        try:
            if market.lower() == "futures":
                bars = self._fetch_futures_ohlcv(symbol, tf, limit=limit)
            else:
                bars = self._fetch_spot_ohlcv(symbol, tf, limit=limit)
            if len(bars) > 1 and not include_forming:
                bars = bars[:-1]  # drop forming
            return bars
        except Exception as e:
            logger.debug("get_ohlcv failed %s:%s %s: %s", market, symbol, tf, e)
            return []

    def get_ohlcv_around(
        self,
        market: str,
        symbol: str,
        tf: str,
        around_ts: float,
        lookback_seconds: int = 6 * 3600,
        lookahead_seconds: int = 2 * 3600,
        limit: int = 500,
    ) -> List[dict]:
        """OHLCV for one incident window (not 'now'). Soft-fail → [].

        Includes the fire's own bar even if it is still forming.
        Bars outside [around_ts − lookback, around_ts + lookahead] are dropped
        so a live book cannot masquerade as that dump.
        """
        if tf not in _INTERVALS:
            return []
        try:
            center = float(around_ts)
        except (TypeError, ValueError):
            return []
        if center > 1e12:
            center /= 1000.0
        if center <= 0:
            return []
        start = center - max(60, int(lookback_seconds or 0))
        end = center + max(0, int(lookahead_seconds or 0))
        end = min(end, time.time() + 60)
        try:
            if market.lower() == "futures":
                bars = self._fetch_futures_ohlcv(
                    symbol, tf, limit=limit, start_ts=start, end_ts=end
                )
            else:
                bars = self._fetch_spot_ohlcv(
                    symbol, tf, limit=limit, start_ts=start, end_ts=end
                )
        except Exception as e:
            logger.debug(
                "get_ohlcv_around failed %s:%s %s: %s", market, symbol, tf, e
            )
            return []
        out: List[dict] = []
        lo, hi = start - 1.0, end + 1.0
        for b in bars or []:
            try:
                ts = float(b.get("ts") or 0)
            except (TypeError, ValueError):
                continue
            if lo <= ts <= hi:
                out.append(b)
        return out

    def fetch_1m_live(self, market: str, symbol: str, limit: int = 20) -> List[dict]:
        """Last N 1m bars **including the forming candle** (running high/low).

        Used for wick-aware mover fires. Soft-fail → [].
        """
        try:
            if market.lower() == "futures":
                bars = self._fetch_futures_ohlcv(symbol, "1m", limit=limit)
            else:
                bars = self._fetch_spot_ohlcv(symbol, "1m", limit=limit)
            return bars[-max(2, int(limit)) :] if bars else []
        except Exception as e:
            logger.debug("fetch_1m_live failed %s:%s: %s", market, symbol, e)
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
        self,
        symbol: str,
        tf: str,
        limit: int = 96,
        start_ts: Optional[float] = None,
        end_ts: Optional[float] = None,
    ) -> List[dict]:
        spot_iv, _ = _INTERVALS[tf]
        sym = symbol.upper().replace("_", "")
        if not sym.endswith("USDT") and "USDT" not in sym:
            sym = sym + "USDT"
        url = f"{self.spot_base}/klines"
        params = {
            "symbol": sym,
            "interval": spot_iv,
            "limit": max(10, min(int(limit), 500)),
        }
        if start_ts is not None:
            params["startTime"] = int(float(start_ts) * 1000)
        if end_ts is not None:
            params["endTime"] = int(float(end_ts) * 1000)
        resp = self.session.get(
            url,
            params=params,
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
                bar = {
                    "ts": float(row[0]) / 1000.0,
                    "o": float(row[1]),
                    "h": float(row[2]),
                    "l": float(row[3]),
                    "c": float(row[4]),
                    "v": float(row[5]),
                }
                if len(row) > 7 and row[7] is not None:
                    try:
                        bar["q"] = float(row[7])
                    except (TypeError, ValueError):
                        pass
                out.append(bar)
            except (TypeError, ValueError, IndexError):
                continue
        return out

    def _fetch_futures_ohlcv(
        self,
        symbol: str,
        tf: str,
        limit: int = 96,
        start_ts: Optional[float] = None,
        end_ts: Optional[float] = None,
    ) -> List[dict]:
        _, fut_iv = _INTERVALS[tf]
        sym = symbol.upper()
        if "_" not in sym and sym.endswith("USDT"):
            # compact → try BASE_USDT
            base = sym[:-4]
            sym = f"{base}_USDT"
        url = f"{self.futures_base}/contract/kline/{sym}"
        params = {"interval": fut_iv}
        if start_ts is not None:
            params["start"] = int(float(start_ts))
        if end_ts is not None:
            params["end"] = int(float(end_ts))
        resp = self.session.get(
            url,
            params=params,
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
