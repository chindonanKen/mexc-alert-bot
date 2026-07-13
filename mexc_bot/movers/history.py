"""In-memory price ring buffer for lookback % calculations."""

from __future__ import annotations

import time
from collections import deque
from typing import Deque, Dict, Optional, Tuple


class PriceHistory:
    """Keeps (timestamp, price) samples per market:symbol key.

    Retention is slightly longer than lookback so we can always find a sample
    at or before (now - lookback).
    """

    def __init__(self, max_age_seconds: float = 1200.0):
        self.max_age_seconds = max(max_age_seconds, 60.0)
        # key -> deque[(ts, price)] oldest first
        self._series: Dict[str, Deque[Tuple[float, float]]] = {}

    @staticmethod
    def make_key(market: str, symbol: str) -> str:
        return f"{market.lower()}:{symbol.upper()}"

    def record(self, market: str, symbol: str, price: float, ts: Optional[float] = None) -> None:
        if price is None or price <= 0:
            return
        now = ts if ts is not None else time.time()
        key = self.make_key(market, symbol)
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
        series = self._series.get(key)
        if not series:
            return None
        # series is oldest→newest; walk from the right for efficiency
        for ts, price in reversed(series):
            if ts <= target_ts:
                return price
        return None

    def latest(self, market: str, symbol: str) -> Optional[Tuple[float, float]]:
        key = self.make_key(market, symbol)
        series = self._series.get(key)
        if not series:
            return None
        return series[-1]

    def oldest(self, market: str, symbol: str) -> Optional[Tuple[float, float]]:
        key = self.make_key(market, symbol)
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
    ) -> Optional[Tuple[float, float, float]]:
        """
        Rolling high → now drawdown within the lookback window.

        Reference peak = max(price of samples with ts in (now-lookback, now],
                             and the sample at or before now-lookback as left edge).

        Returns (change_frac, peak_price, price_now) where change_frac is <= 0
        when price_now is at/below the peak (e.g. -0.07 = -7% from high).

        This matches "dumped X% within the last N minutes" better than pure
        endpoint-to-endpoint (which misses dumps after a mid-window spike, and
        under-reports if the high was recent).

        Requires history that reaches back to the lookback boundary (same as
        endpoint_change) so cold start does not false-fire on a short series.
        """
        now = now if now is not None else time.time()
        latest = self.latest(market, symbol)
        if latest is None:
            return None
        _, price_now = latest

        window_start = now - lookback_seconds
        # Need a left-edge anchor so we know the full window is covered
        left = self.price_at_or_before(market, symbol, window_start)
        if left is None or left <= 0:
            return None

        peak = float(left)
        key = self.make_key(market, symbol)
        series = self._series.get(key)
        if not series:
            return None

        for ts, price in series:
            if ts < window_start:
                continue
            if ts > now:
                break
            if price > peak:
                peak = float(price)

        if peak <= 0:
            return None
        return ((price_now - peak) / peak, peak, price_now)

    def tracked_count(self) -> int:
        return len(self._series)
