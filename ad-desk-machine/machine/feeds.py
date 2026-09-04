"""Live MEXC public klines / last. Price, volume, reds. No private keys."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests

from .settings import MEXC_SPOT_API

logger = logging.getLogger(__name__)

# interval key → MEXC spot interval
_INTERVALS = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "1h": "60m",
    "4h": "4h",
    "8h": "8h",
    "12h": "60m",  # spot has no 12h; callers should prefer a real TF
    "1d": "1d",
    "1D": "1d",
    "1w": "1W",
}

CacheKey = Tuple[str, str]


def _spot_symbol(symbol: str) -> str:
    s = str(symbol or "").upper().replace("_", "").replace("-", "")
    if s.endswith("USDT") or s.endswith("USDC"):
        return s
    return f"{s}USDT" if s else s


def is_red_bar(bar: Optional[Dict[str, Any]]) -> bool:
    if not bar:
        return False
    try:
        o = float(bar.get("o") if bar.get("o") is not None else bar.get("open"))
        c = float(bar.get("c") if bar.get("c") is not None else bar.get("close"))
    except (TypeError, ValueError):
        return False
    return c < o


def consecutive_reds(bars: Sequence[Dict[str, Any]], *, include_forming: bool = False) -> int:
    """Closed bars only. Newest → older. Forming candle dropped by default."""
    seq = [b for b in bars or [] if isinstance(b, dict)]
    if not seq:
        return 0
    if not include_forming and len(seq) >= 2:
        seq = seq[:-1]
    n = 0
    for b in reversed(seq):
        if is_red_bar(b):
            n += 1
        else:
            break
    return n


def dollar_volume(bars: Optional[Sequence[Dict[str, Any]]]) -> Optional[float]:
    seq = [b for b in (bars or []) if isinstance(b, dict)]
    for bar in reversed(seq):
        for key in ("q", "quote_volume", "quoteVolume"):
            try:
                v = float(bar.get(key))
                if v > 0:
                    return v
            except (TypeError, ValueError):
                continue
        try:
            coin = float(bar.get("v") if bar.get("v") is not None else bar.get("volume") or 0)
            close = float(bar.get("c") if bar.get("c") is not None else bar.get("close") or 0)
            if coin > 0 and close > 0:
                return coin * close
        except (TypeError, ValueError):
            continue
    return None


def parse_mexc_klines(raw: Any) -> List[Dict[str, Any]]:
    """Spot kline row: [openTime, open, high, low, close, volume, ..., quoteVolume]."""
    out: List[Dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for row in raw:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        try:
            out.append(
                {
                    "ts": float(row[0]) / 1000.0 if float(row[0]) > 1e12 else float(row[0]),
                    "o": float(row[1]),
                    "h": float(row[2]),
                    "l": float(row[3]),
                    "c": float(row[4]),
                    "v": float(row[5]),
                    "q": float(row[7]) if len(row) > 7 else None,
                }
            )
        except (TypeError, ValueError, IndexError):
            continue
    return out


class MexcPublicFeed:
    """Public api.mexc.com only. Never signs. Never posts orders."""

    def __init__(self, base: str = MEXC_SPOT_API, timeout: float = 6.0, session: Optional[requests.Session] = None):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "ad-desk-machine/0.1"})
        self._kline_cache: Dict[CacheKey, Tuple[float, List[Dict[str, Any]]]] = {}
        self.cache_ttl = 8.0

    def ticker_price(self, symbol: str) -> Optional[float]:
        sym = _spot_symbol(symbol)
        url = f"{self.base}/ticker/price"
        try:
            r = self.session.get(url, params={"symbol": sym}, timeout=self.timeout)
            if r.status_code != 200:
                return None
            d = r.json()
            px = float(d.get("price") or 0)
            return px if px > 0 else None
        except Exception:
            logger.debug("ticker %s", symbol, exc_info=True)
            return None

    def klines(self, symbol: str, tf: str, limit: int = 100) -> List[Dict[str, Any]]:
        interval = _INTERVALS.get(str(tf).strip(), "4h")
        sym = _spot_symbol(symbol)
        url = f"{self.base}/klines"
        try:
            r = self.session.get(
                url,
                params={"symbol": sym, "interval": interval, "limit": int(limit)},
                timeout=self.timeout,
            )
            if r.status_code != 200:
                return []
            return parse_mexc_klines(r.json())
        except Exception:
            logger.debug("klines %s %s", symbol, tf, exc_info=True)
            return []

    def snapshot(self, symbol: str, tf: str, faster_tf: str) -> Dict[str, Any]:
        last = self.ticker_price(symbol)
        bars = self.klines(symbol, tf)
        fast = self.klines(symbol, faster_tf)
        if last is None and bars:
            try:
                last = float(bars[-1].get("c") or 0) or None
            except (TypeError, ValueError):
                last = None
        return {
            "current_price": last,
            "bars": bars,
            "faster_bars": fast,
            "chosen_tf_reds": consecutive_reds(bars),
            "faster_tf_reds": consecutive_reds(fast),
            "vol_usd": dollar_volume(bars),
            "vol_usd_fast": dollar_volume(fast),
            "source": "api.mexc.com",
        }
