"""Read-only MEXC private REST (spot trades). Never places orders.

HMAC-signed requests. Soft-fail on errors. Keys only via env — never log secrets.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import requests

try:
    import certifi

    _CA = certifi.where()
except Exception:  # pragma: no cover
    _CA = True

logger = logging.getLogger(__name__)


class MexcPrivateSpotClient:
    """Spot private API — myTrades only (read)."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str = "https://api.mexc.com",
        timeout: float = 15.0,
    ):
        self.api_key = (api_key or "").strip()
        self.api_secret = (api_secret or "").strip()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.verify = _CA
        self.session.headers.update(
            {
                "User-Agent": "mexc-alert-bot-private-read/1.0",
                "X-MEXC-APIKEY": self.api_key,
            }
        )

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def _sign(self, params: Dict[str, Any]) -> Dict[str, Any]:
        params = dict(params)
        params["timestamp"] = int(time.time() * 1000)
        query = urlencode(params)
        sig = hmac.new(
            self.api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        params["signature"] = sig
        return params

    def get_my_trades(
        self, symbol: str, *, limit: int = 50, start_time_ms: Optional[int] = None
    ) -> List[dict]:
        """GET /api/v3/myTrades — requires symbol. Returns [] on error."""
        if not self.configured:
            return []
        sym = symbol.upper().replace("_", "").replace("-", "")
        if not sym.endswith("USDT") and "USDT" not in sym:
            # allow BTCUSDT form
            pass
        params: Dict[str, Any] = {"symbol": sym, "limit": max(1, min(int(limit), 100))}
        if start_time_ms:
            params["startTime"] = int(start_time_ms)
        try:
            signed = self._sign(params)
            url = f"{self.base_url}/api/v3/myTrades"
            resp = self.session.get(url, params=signed, timeout=self.timeout)
            if resp.status_code != 200:
                logger.warning(
                    "myTrades %s status=%s body=%s",
                    sym,
                    resp.status_code,
                    (resp.text or "")[:200],
                )
                return []
            data = resp.json()
            if not isinstance(data, list):
                return []
            return data
        except Exception as e:
            logger.warning("myTrades failed %s: %s", symbol, e)
            return []

    def close(self) -> None:
        try:
            self.session.close()
        except Exception:
            pass


def normalize_spot_symbol_from_mexc(sym: str) -> str:
    return (sym or "").upper().replace("_", "").replace("-", "")


def trade_to_fill_row(trade: dict, user_id: int) -> Optional[dict]:
    """Map MEXC myTrades item → journal fill fields."""
    try:
        tid = str(trade.get("id") or trade.get("tradeId") or "")
        if not tid:
            return None
        symbol = normalize_spot_symbol_from_mexc(str(trade.get("symbol") or ""))
        if not symbol:
            return None
        price = float(trade.get("price"))
        qty = float(trade.get("qty") or trade.get("quantity") or 0)
        quote = trade.get("quoteQty")
        quote_f = float(quote) if quote is not None else price * qty
        is_buyer = trade.get("isBuyer")
        side = "buy" if is_buyer in (True, "true", "TRUE", 1, "1") else "sell"
        ts_ms = int(trade.get("time") or trade.get("timestamp") or 0)
        return {
            "user_id": user_id,
            "exchange_trade_id": tid,
            "symbol": symbol,
            "market": "spot",
            "side": side,
            "price": price,
            "qty": qty,
            "quote_qty": quote_f,
            "ts": ts_ms / 1000.0 if ts_ms > 1e12 else float(ts_ms),
            "raw": trade,
        }
    except Exception:
        return None
