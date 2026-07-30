"""Live public prices for the desk (MEXC primary, Binance fallback)."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import requests

try:
    import certifi

    _CA = certifi.where()
except Exception:
    _CA = True

logger = logging.getLogger(__name__)

_session = requests.Session()
_session.verify = _CA
_session.headers.update({"User-Agent": "mexc-desk-v2/0.1"})


def ticker_24h(symbol: str) -> Optional[Dict[str, Any]]:
    sym = symbol.upper().replace("_", "").replace("-", "")
    if not sym.endswith("USDT"):
        sym = sym + "USDT"
    # Prefer Binance for broader network reach; MEXC first when available
    for base, path in (
        ("https://api.mexc.com", f"/api/v3/ticker/24hr?symbol={sym}"),
        ("https://api.binance.com", f"/api/v3/ticker/24hr?symbol={sym}"),
    ):
        try:
            r = _session.get(base + path, timeout=8)
            if r.status_code != 200:
                continue
            d = r.json()
            last = float(d.get("lastPrice") or d.get("price") or 0)
            chg = float(d.get("priceChangePercent") or 0)
            if last <= 0:
                continue
            return {
                "symbol": sym,
                "price": last,
                "changePercent": chg,
                "source": "mexc" if "mexc" in base else "binance",
            }
        except Exception as e:
            logger.debug("ticker %s via %s: %s", sym, base, e)
    return None


def watchlist_tickers(symbols: List[str]) -> List[Dict[str, Any]]:
    out = []
    seen = set()
    for s in symbols:
        key = s.upper().replace("_", "")
        if key in seen:
            continue
        seen.add(key)
        t = ticker_24h(key)
        if t:
            out.append(t)
    return out


def market_context() -> Dict[str, Any]:
    """BTC/ETH/SOL pulse for AD regime context."""
    majors = []
    for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        t = ticker_24h(s)
        if t:
            majors.append(t)
    # crude regime
    btc = next((m for m in majors if m["symbol"] == "BTCUSDT"), None)
    regime = "UNKNOWN"
    if btc:
        c = btc["changePercent"]
        if c <= -3:
            regime = "RISK_OFF"
        elif c <= -1:
            regime = "SOFT"
        elif c >= 2:
            regime = "RISK_ON"
        else:
            regime = "RANGE"
    return {"majors": majors, "regime": regime}
