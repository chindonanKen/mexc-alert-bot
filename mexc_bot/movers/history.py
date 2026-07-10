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

    def pct_change_over(
        self,
        market: str,
        symbol: str,
        lookback_seconds: float,
        now: Optional[float] = None,
    ) -> Optional[float]:
        """
        (price_now - price_then) / price_then as a fraction (e.g. -0.05 = -5%).
        Returns None if history is insufficient.
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
        return (price_now - price_then) / price_then

    def tracked_count(self) -> int:
        return len(self._series)
