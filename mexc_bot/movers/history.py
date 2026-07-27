"""In-memory price ring buffer for lookback % calculations."""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Deque, Dict, Optional, Tuple


class PriceHistory:
    """Keeps (timestamp, price) samples per market:symbol key.

    Retention is slightly longer than lookback so we can always find a sample
    at or before (now - lookback).

    Thread-safe for scanner write + bot/heat read.
    """

    def __init__(self, max_age_seconds: float = 1200.0):
        self.max_age_seconds = max(max_age_seconds, 60.0)
        # key -> deque[(ts, price)] oldest first
        self._series: Dict[str, Deque[Tuple[float, float]]] = {}
        self._lock = threading.RLock()

    @staticmethod
    def make_key(market: str, symbol: str) -> str:
        return f"{market.lower()}:{symbol.upper()}"

    def record(self, market: str, symbol: str, price: float, ts: Optional[float] = None) -> None:
        if price is None or price <= 0:
            return
        now = ts if ts is not None else time.time()
        key = self.make_key(market, symbol)
        with self._lock:
            series = self._series.get(key)
            if series is None:
                series = deque()
                self._series[key] = series
            series.append((now, float(price)))
            self._prune(key, now)

    def _prune(self, key: str, now: float) -> None:
        series = self._series.get(key)
        if not series:
            return
        cutoff = now - self.max_age_seconds
        while series and series[0][0] < cutoff:
            series.popleft()

    def price_at_or_before(self, market: str, symbol: str, target_ts: float) -> Optional[float]:
        """Return the newest sample with timestamp <= target_ts, or None."""
        key = self.make_key(market, symbol)
        with self._lock:
            series = self._series.get(key)
            if not series:
                return None
            for ts, price in reversed(series):
                if ts <= target_ts:
                    return price
        return None

    def latest(self, market: str, symbol: str) -> Optional[Tuple[float, float]]:
        key = self.make_key(market, symbol)
        with self._lock:
            series = self._series.get(key)
            if not series:
                return None
            return series[-1]

    def oldest(self, market: str, symbol: str) -> Optional[Tuple[float, float]]:
        key = self.make_key(market, symbol)
        with self._lock:
            series = self._series.get(key)
            if not series:
                return None
            return series[0]

    def pct_change_over(
        self,
        market: str,
        symbol: str,
        lookback_seconds: float,
        now: Optional[float] = None,
    ) -> Optional[float]:
        """
        Endpoint-to-endpoint: (price_now - price_then) / price_then.
        Returns None if history is insufficient.
        """
        result = self.endpoint_change(market, symbol, lookback_seconds, now=now)
        if result is None:
            return None
        return result[0]

    def endpoint_change(
        self,
        market: str,
        symbol: str,
        lookback_seconds: float,
        now: Optional[float] = None,
    ) -> Optional[Tuple[float, float, float]]:
        """
        (change_frac, price_then, price_now) for price at/before (now-lookback) → latest.
        """
        now = now if now is not None else time.time()
        latest = self.latest(market, symbol)
        if latest is None:
            return None
        _, price_now = latest
        then_ts = now - lookback_seconds
        price_then = self.price_at_or_before(market, symbol, then_ts)
        if price_then is None or price_then <= 0:
            return None
        return ((price_now - price_then) / price_then, price_then, price_now)

    def peak_drawdown(
        self,
        market: str,
        symbol: str,
        lookback_seconds: float,
        now: Optional[float] = None,
    ) -> Optional[Tuple[float, float, float, float]]:
        """
        Rolling high → now drawdown within the lookback window.

        Returns (change_frac, peak_price, price_now, peak_ts).
        change_frac is <= 0 when price_now is at/below the peak.
        peak_ts is the timestamp of the first sample that achieved the peak
        (used for velocity scoring).

        Requires history that reaches back to the lookback boundary so cold
        start does not false-fire on a short series.
        """
        now = now if now is not None else time.time()
        latest = self.latest(market, symbol)
        if latest is None:
            return None
        _, price_now = latest

        window_start = now - lookback_seconds
        left = self.price_at_or_before(market, symbol, window_start)
        if left is None or left <= 0:
            return None

        peak = float(left)
        peak_ts = float(window_start)
        key = self.make_key(market, symbol)
        with self._lock:
            series = self._series.get(key)
            if not series:
                return None

            # Left-edge sample timestamp (newest <= window_start)
            for ts, price in reversed(series):
                if ts <= window_start:
                    peak = float(price)
                    peak_ts = float(ts)
                    break

            for ts, price in series:
                if ts < window_start:
                    continue
                if ts > now:
                    break
                if price > peak:
                    peak = float(price)
                    peak_ts = float(ts)

        if peak <= 0:
            return None
        return ((price_now - peak) / peak, peak, price_now, peak_ts)

    def tracked_count(self) -> int:
        with self._lock:
            return len(self._series)
