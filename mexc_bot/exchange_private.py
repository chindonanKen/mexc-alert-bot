"""Read-only MEXC private REST (spot + futures trades). Never places orders.

HMAC-signed requests. Soft-fail on errors. Keys only via env — never log secrets.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Any, Dict, List, Optional, Tuple
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


def normalize_futures_symbol(sym: str) -> str:
    """Prefer BASE_USDT form for futures contracts."""
    s = (sym or "").upper().replace("-", "_").strip()
    if not s:
        return ""
    if "_" in s:
        return s
    if s.endswith("USDT") and len(s) > 4:
        return s[:-4] + "_USDT"
    return s


def trade_to_fill_row(trade: dict, user_id: int) -> Optional[dict]:
    """Map MEXC spot myTrades item → journal fill fields."""
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
            # bare id — keep stable so existing prod fills are not duplicated
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


class MexcPrivateFuturesClient:
    """Futures private API — open positions + order deals (read-only).

    Sign: HMAC-SHA256(apiKey + timestamp + sortedQuery). Headers ApiKey,
    Request-Time, Signature. Base https://contract.mexc.com
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str = "https://contract.mexc.com",
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
                "User-Agent": "mexc-alert-bot-futures-read/1.0",
                "Content-Type": "application/json",
                "ApiKey": self.api_key,
            }
        )

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def _sign(self, timestamp: str, params: Dict[str, Any]) -> str:
        items = sorted((k, v) for k, v in params.items() if v is not None)
        query = "&".join(f"{k}={v}" for k, v in items)
        payload = f"{self.api_key}{timestamp}{query}"
        return hmac.new(
            self.api_secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        if not self.configured:
            return None
        params = {k: v for k, v in (params or {}).items() if v is not None}
        ts = str(int(time.time() * 1000))
        headers = {
            "ApiKey": self.api_key,
            "Request-Time": ts,
            "Signature": self._sign(ts, params),
            "Content-Type": "application/json",
        }
        try:
            url = f"{self.base_url}{path if path.startswith('/') else '/' + path}"
            resp = self.session.get(
                url, params=params, headers=headers, timeout=self.timeout
            )
            if resp.status_code != 200:
                logger.warning(
                    "futures private %s status=%s body=%s",
                    path,
                    resp.status_code,
                    (resp.text or "")[:200],
                )
                return None
            data = resp.json()
            if isinstance(data, dict) and data.get("success") is False:
                logger.warning(
                    "futures private %s fail code=%s msg=%s",
                    path,
                    data.get("code"),
                    data.get("message"),
                )
                return None
            return data
        except Exception as e:
            logger.warning("futures private %s error: %s", path, e)
            return None

    def get_open_positions(self, symbol: Optional[str] = None) -> List[dict]:
        """GET /api/v1/private/position/open_positions"""
        params: Dict[str, Any] = {}
        if symbol:
            params["symbol"] = normalize_futures_symbol(symbol)
        data = self._get("/api/v1/private/position/open_positions", params)
        if not data:
            return []
        rows = data.get("data") if isinstance(data, dict) else data
        return rows if isinstance(rows, list) else []

    def get_order_deals(
        self,
        symbol: str,
        *,
        page_num: int = 1,
        page_size: int = 100,
        start_time_ms: Optional[int] = None,
        end_time_ms: Optional[int] = None,
    ) -> List[dict]:
        """GET /api/v1/private/order/list/order_deals/v3 — max page_size 1000."""
        sym = normalize_futures_symbol(symbol)
        if not sym:
            return []
        params: Dict[str, Any] = {
            "symbol": sym,
            "page_num": max(1, int(page_num)),
            "page_size": max(1, min(int(page_size), 100)),
        }
        if start_time_ms:
            params["start_time"] = int(start_time_ms)
        if end_time_ms:
            params["end_time"] = int(end_time_ms)
        data = self._get("/api/v1/private/order/list/order_deals/v3", params)
        if not data:
            return []
        rows = data.get("data") if isinstance(data, dict) else data
        return rows if isinstance(rows, list) else []

    def get_history_positions(
        self,
        *,
        symbol: Optional[str] = None,
        page_num: int = 1,
        page_size: int = 50,
    ) -> List[dict]:
        """GET /api/v1/private/position/list/history_positions — closed rounds."""
        params: Dict[str, Any] = {
            "page_num": max(1, int(page_num)),
            "page_size": max(1, min(int(page_size), 100)),
        }
        if symbol:
            params["symbol"] = normalize_futures_symbol(symbol)
        data = self._get("/api/v1/private/position/list/history_positions", params)
        if not data:
            return []
        rows = data.get("data") if isinstance(data, dict) else data
        return rows if isinstance(rows, list) else []

    def close(self) -> None:
        try:
            self.session.close()
        except Exception:
            pass


def futures_deal_to_fill_row(deal: dict, user_id: int) -> Optional[dict]:
    """Map futures order deal → journal fill (long book: open long=buy, close=sell).

    MEXC side codes: 1 open long, 2 close short, 3 open short, 4 close long.
    One-way mode often uses side 3 + reduceOnly for closing longs.
    """
    try:
        tid = str(deal.get("id") or deal.get("dealId") or "")
        if not tid:
            return None
        symbol = normalize_futures_symbol(str(deal.get("symbol") or ""))
        if not symbol:
            return None
        price = float(deal.get("price") or 0)
        qty = float(deal.get("vol") or deal.get("qty") or deal.get("quantity") or 0)
        if price <= 0 or qty <= 0:
            return None
        side_code = deal.get("side")
        try:
            sc = int(side_code)
        except (TypeError, ValueError):
            sc = 0
        reduce_only = deal.get("reduceOnly") in (True, "true", "TRUE", 1, "1")
        # Long inventory model (AD desk): buy opens long / covers short; sell closes long / opens short
        if sc in (1, 2):
            side = "buy"
        elif sc in (3, 4):
            side = "sell"
        else:
            # fallback string sides
            raw = str(side_code or "").lower()
            if raw in ("buy", "long", "open_long"):
                side = "buy"
            elif raw in ("sell", "short", "close_long", "open_short"):
                side = "sell"
            else:
                return None
        # one-way: reduceOnly side 3 is still sell (already)
        _ = reduce_only
        ts_ms = int(deal.get("timestamp") or deal.get("time") or 0)
        return {
            "user_id": user_id,
            "exchange_trade_id": f"f:{tid}",
            "symbol": symbol,
            "market": "futures",
            "side": side,
            "price": price,
            "qty": qty,
            "quote_qty": price * qty,
            "ts": ts_ms / 1000.0 if ts_ms > 1e12 else float(ts_ms),
            "raw": deal,
        }
    except Exception:
        return None


def futures_position_snapshot(pos: dict) -> Optional[dict]:
    """Normalize open_positions row for desk override."""
    try:
        symbol = normalize_futures_symbol(str(pos.get("symbol") or ""))
        if not symbol:
            return None
        hold = float(pos.get("holdVol") or pos.get("vol") or 0)
        avg = pos.get("holdAvgPrice") or pos.get("openAvgPrice")
        avg_f = float(avg) if avg is not None else None
        # positionType 1 long 2 short
        ptype = int(pos.get("positionType") or 1)
        return {
            "symbol": symbol,
            "market": "futures",
            "hold_vol": hold,
            "entry_avg": avg_f,
            "position_type": ptype,
            "realized": float(pos.get("realised") or pos.get("realized") or 0),
            "leverage": pos.get("leverage"),
            "update_time": pos.get("updateTime") or pos.get("createTime"),
            "raw": pos,
        }
    except Exception:
        return None


def _ms_to_s(ts: Any) -> Optional[float]:
    try:
        v = float(ts)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return v / 1000.0 if v > 1e12 else v


def history_position_to_closed_entity(pos: dict) -> Optional[dict]:
    """Map MEXC history_positions row → desk closed entity (exchange truth).

    Uses openAvg/closeAvg/realised from the exchange — not fill reconstruction.
    """
    try:
        symbol = normalize_futures_symbol(str(pos.get("symbol") or ""))
        if not symbol:
            return None
        hold = float(pos.get("holdVol") or 0)
        if hold > 1e-12:
            return None  # still open — not a closed history row
        close_vol = float(pos.get("closeVol") or 0)
        if close_vol <= 0:
            return None

        entry = pos.get("openAvgPrice") or pos.get("newOpenAvgPrice") or pos.get(
            "holdAvgPrice"
        )
        exit_ = pos.get("closeAvgPrice") or pos.get("newCloseAvgPrice")
        entry_f = float(entry) if entry is not None else None
        exit_f = float(exit_) if exit_ is not None else None
        realised = float(pos.get("realised") or pos.get("realized") or 0)
        ptype = int(pos.get("positionType") or 1)
        # profitRatio is fraction (0.1379 = +13.79%) — exchange already side-aware
        pr = pos.get("profitRatio")
        pnl_pct = None
        if pr is not None:
            try:
                pnl_pct = float(pr) * 100.0
            except (TypeError, ValueError):
                pnl_pct = None
        if pnl_pct is None and entry_f and exit_f and entry_f > 0:
            raw = (exit_f - entry_f) / entry_f * 100.0
            pnl_pct = -raw if ptype == 2 else raw
        if pnl_pct is None:
            if realised > 0.5:
                pnl_pct = 1.0
            elif realised < -0.5:
                pnl_pct = -1.0
            else:
                pnl_pct = 0.0

        outcome = "flat"
        if pnl_pct > 0.5 or realised > 0.5:
            outcome = "success"
        elif pnl_pct < -0.5 or realised < -0.5:
            outcome = "miss"

        opened_at = _ms_to_s(pos.get("createTime"))
        closed_at = _ms_to_s(pos.get("updateTime")) or opened_at
        hold_s = None
        if opened_at is not None and closed_at is not None:
            hold_s = max(0.0, closed_at - opened_at)
        pid = pos.get("positionId") or pos.get("id")
        return {
            "symbol": symbol,
            "market": "futures",
            "status": "closed",
            "is_open": False,
            "outcome": outcome,
            "opened_at": opened_at,
            "closed_at": closed_at,
            "hold_seconds": hold_s,
            "hold_hours": round(hold_s / 3600.0, 2) if hold_s is not None else None,
            "entry_avg": entry_f,
            "exit_avg": exit_f,
            "entry_display": entry_f,
            "size_remaining": 0.0,
            "size_qty": close_vol,
            "size_sold": close_vol,
            "buy_orders": [],
            "sell_orders": [],
            "n_buys": 0,
            "n_sells": 0,
            "realized_pnl_pct": round(pnl_pct, 3) if pnl_pct is not None else None,
            "realized_pnl_usd": round(realised, 4),
            "leverage": pos.get("leverage"),
            "position_type": ptype,
            "position_side": "long" if ptype == 1 else "short",
            "recon_from_fills": False,
            "exchange_history": True,
            "entity_key": f"fhist:{pid or symbol}:{int(closed_at or 0)}",
            "exchange_position_id": pid,
            "close_profit_loss": pos.get("closeProfitLoss"),
            "fee": pos.get("totalFee") or pos.get("fee"),
            "notes": "MEXC history_positions",
        }
    except Exception:
        return None
