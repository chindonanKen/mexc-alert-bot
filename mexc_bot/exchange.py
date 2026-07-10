"""Exchange price data adapters.

Defines a small PriceProvider protocol so the monitoring and command layers
do not depend on a specific price source (REST batch, WebSocket, futures,
multi-exchange, etc.). This keeps the alarm logic and future expansion clean.

Current implementations:
- MexcClient — spot, batch /ticker/price
- MexcFuturesClient — futures/contract, batch /contract/ticker (lastPrice)
"""

import logging
from typing import Dict, Optional, Protocol

import requests

logger = logging.getLogger(__name__)


class PriceProvider(Protocol):
    """Minimal interface for anything that can supply current prices.

    Implementations can be:
    - REST batch client (current default)
    - WebSocket push client (future, for lower CPU/latency)
    - Futures / multi-exchange providers
    - Test doubles

    Only these three methods are required by the rest of the system.
    """

    def get_all_prices(self) -> Dict[str, float]:
        """Return a dict of symbol -> latest price for as many symbols as possible."""
        ...

    def get_price(self, symbol: str) -> Optional[float]:
        """Fetch a single symbol (used by /price command and fallbacks)."""
        ...

    def close(self) -> None:
        """Release any resources (connections, etc.)."""
        ...


class MexcClient:
    """MEXC spot implementation of PriceProvider.

    Uses the efficient batch /ticker/price (no symbol) endpoint.
    This is the current default and keeps CPU low even with many alerts.
    """

    def __init__(self, base_url: str = "https://api.mexc.com/api/v3", timeout: int = 8):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "mexc-alert-bot/0.3"})

    def get_price(self, symbol: str) -> Optional[float]:
        """Single symbol fetch (kept for compatibility / manual /price checks)."""
        url = f"{self.base_url}/ticker/price"
        try:
            resp = self.session.get(url, params={"symbol": symbol.upper()}, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                return float(data["price"])
            else:
                logger.warning(f"MEXC spot {resp.status_code} for single {symbol}")
        except requests.RequestException as e:
            logger.warning(f"Request error (spot single) for {symbol}: {e}")
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"Parse error (spot single) for {symbol}: {e}")
        return None

    def get_all_prices(self) -> Dict[str, float]:
        """
        Fetch ALL current spot prices in one call.

        MEXC /ticker/price without params returns a list:
        [{"symbol": "BTCUSDT", "price": "65012.3"}, ...]

        Returns uppercase symbol -> float price.
        On any failure returns empty dict (caller should handle gracefully).
        """
        url = f"{self.base_url}/ticker/price"
        try:
            resp = self.session.get(url, timeout=self.timeout)
            if resp.status_code != 200:
                logger.warning(f"MEXC spot batch ticker returned {resp.status_code}")
                return {}
            data = resp.json()
            if not isinstance(data, list):
                logger.warning("Unexpected spot batch ticker response shape")
                return {}
            prices: Dict[str, float] = {}
            for item in data:
                try:
                    sym = str(item.get("symbol", "")).upper()
                    price = float(item.get("price"))
                    if sym:
                        prices[sym] = price
                except (TypeError, ValueError):
                    continue
            logger.debug(f"Spot batch fetched {len(prices)} prices")
            return prices
        except requests.RequestException as e:
            logger.warning(f"Spot batch price request failed: {e}")
        except Exception as e:
            logger.warning(f"Unexpected error in spot batch price fetch: {e}")
        return {}

    def close(self):
        try:
            self.session.close()
        except Exception:
            pass


class MexcFuturesClient:
    """MEXC futures/contract implementation of PriceProvider.

    Public market endpoint (no API key):
      GET {base}/contract/ticker
      Optional query: symbol=BTC_USDT

    Response shape (documented):
      { "success": true, "code": 0, "data": { "symbol": "BTC_USDT", "lastPrice": ... } }
      or data as a list of ticker objects when symbol is omitted.

    Symbols use underscore form: BTC_USDT (not BTCUSDT).
    """

    def __init__(
        self,
        base_url: str = "https://contract.mexc.com/api/v1",
        timeout: int = 10,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "mexc-alert-bot/0.3"})

    def get_price(self, symbol: str) -> Optional[float]:
        url = f"{self.base_url}/contract/ticker"
        sym = symbol.upper().replace("-", "_")
        try:
            resp = self.session.get(url, params={"symbol": sym}, timeout=self.timeout)
            if resp.status_code != 200:
                logger.warning(f"MEXC futures {resp.status_code} for single {sym}")
                return None
            payload = resp.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, dict) and data.get("lastPrice") is not None:
                return float(data["lastPrice"])
            if isinstance(data, list) and data:
                item = data[0]
                if item.get("lastPrice") is not None:
                    return float(item["lastPrice"])
            logger.warning(f"Unexpected futures single-ticker shape for {sym}")
        except requests.RequestException as e:
            logger.warning(f"Request error (futures single) for {sym}: {e}")
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"Parse error (futures single) for {sym}: {e}")
        return None

    def get_all_prices(self) -> Dict[str, float]:
        """Fetch all contract last prices. Keys are uppercase with underscore (BTC_USDT)."""
        url = f"{self.base_url}/contract/ticker"
        try:
            resp = self.session.get(url, timeout=self.timeout)
            if resp.status_code != 200:
                logger.warning(f"MEXC futures batch ticker returned {resp.status_code}")
                return {}
            payload = resp.json()
            if not isinstance(payload, dict):
                logger.warning("Unexpected futures batch response (not object)")
                return {}
            data = payload.get("data")
            items = []
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                items = [data]
            else:
                logger.warning("Unexpected futures batch data shape")
                return {}

            prices: Dict[str, float] = {}
            for item in items:
                if not isinstance(item, dict):
                    continue
                try:
                    sym = str(item.get("symbol", "")).upper()
                    if not sym or item.get("lastPrice") is None:
                        continue
                    prices[sym] = float(item["lastPrice"])
                except (TypeError, ValueError):
                    continue
            logger.debug(f"Futures batch fetched {len(prices)} prices")
            return prices
        except requests.RequestException as e:
            logger.warning(f"Futures batch price request failed: {e}")
        except Exception as e:
            logger.warning(f"Unexpected error in futures batch price fetch: {e}")
        return {}

    def close(self):
        try:
            self.session.close()
        except Exception:
            pass


def normalize_spot_symbol(raw: str) -> str:
    """Uppercase + smart USDT suffix if it looks like a bare base asset."""
    common_quotes = ("USDT", "USDC", "BTC", "ETH", "BUSD", "FDUSD")
    s = raw.strip().upper().replace("-", "").replace("/", "").replace(" ", "")
    if not s:
        return s
    for q in common_quotes:
        if s.endswith(q) and len(s) > len(q):
            return s
    if s.isalpha() or (len(s) > 1 and s[:-1].isalpha() and s[-1].isdigit()):
        return s + "USDT"
    return s


def normalize_futures_symbol(raw: str) -> str:
    """Normalize to MEXC contract form BASE_QUOTE (e.g. BTC_USDT).

    Accepts: BTC, BTC_USDT, BTCUSDT, btc/usdt, BTC-USDT.
    Bare base → append _USDT.
    """
    s = raw.strip().upper().replace("-", "_").replace("/", "_").replace(" ", "")
    if not s:
        return s
    if "_" in s:
        return s
    # Already ends with known quote without underscore (BTCUSDT)
    for q in ("USDT", "USDC", "USD", "BTC", "ETH"):
        if s.endswith(q) and len(s) > len(q):
            base = s[: -len(q)]
            return f"{base}_{q}"
    # Bare base
    if s.isalpha() or (len(s) > 1 and s[:-1].isalpha() and s[-1].isdigit()):
        return f"{s}_USDT"
    return s
