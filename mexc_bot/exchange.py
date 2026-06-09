"""Exchange price data adapters. Currently only MEXC public API."""

import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class MexcClient:
    """Lightweight client for MEXC public ticker API."""

    def __init__(self, base_url: str = "https://api.mexc.com/api/v3", timeout: int = 6):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        # MEXC public endpoints are generous but we stay polite
        self.session.headers.update({"User-Agent": "mexc-alert-bot/0.1"})

    def get_price(self, symbol: str) -> Optional[float]:
        """
        Fetch latest price for symbol (e.g. 'BTCUSDT').
        Returns float price or None on any error (network, parse, etc).
        """
        url = f"{self.base_url}/ticker/price"
        try:
            resp = self.session.get(url, params={"symbol": symbol.upper()}, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                price = float(data["price"])
                return price
            else:
                logger.warning(f"MEXC returned {resp.status_code} for {symbol}")
        except requests.RequestException as e:
            logger.warning(f"Request error fetching {symbol}: {e}")
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"Parse error for {symbol}: {e}")
        return None

    def close(self):
        self.session.close()
