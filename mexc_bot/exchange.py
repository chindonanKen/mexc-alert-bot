"""Exchange price data adapters.

Defines a small PriceProvider protocol so the monitoring and command layers
do not depend on a specific price source (REST batch, WebSocket, futures,
multi-exchange, etc.). This keeps the alarm logic and future expansion clean.

Current implementation: MexcClient (spot, using efficient batch /ticker/price).
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
        self.session.headers.update({"User-Agent": "mexc-alert-bot/0.2"})

    def get_price(self, symbol: str) -> Optional[float]:
        """Single symbol fetch (kept for compatibility / manual /price checks)."""
        url = f"{self.base_url}/ticker/price"
        try:
            resp = self.session.get(url, params={"symbol": symbol.upper()}, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                return float(data["price"])
            else:
                logger.warning(f"MEXC {resp.status_code} for single {symbol}")
        except requests.RequestException as e:
            logger.warning(f"Request error (single) for {symbol}: {e}")
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"Parse error (single) for {symbol}: {e}")
        return None

    def get_all_prices(self) -> Dict[str, float]:
        """
        Fetch ALL current prices in one call.

        MEXC /ticker/price without params returns a list:
        [{"symbol": "BTCUSDT", "price": "65012.3"}, ...]

        Returns uppercase symbol -> float price.
        On any failure returns empty dict (caller should handle gracefully).
        """
        url = f"{self.base_url}/ticker/price"
        try:
            resp = self.session.get(url, timeout=self.timeout)
            if resp.status_code != 200:
                logger.warning(f"MEXC batch ticker returned {resp.status_code}")
                return {}
            data = resp.json()
            if not isinstance(data, list):
                logger.warning("Unexpected batch ticker response shape")
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
            logger.debug(f"Batch fetched {len(prices)} prices")
            return prices
        except requests.RequestException as e:
            logger.warning(f"Batch price request failed: {e}")
        except Exception as e:
            logger.warning(f"Unexpected error in batch price fetch: {e}")
        return {}

    def close(self):
        try:
            self.session.close()
        except Exception:
            pass
