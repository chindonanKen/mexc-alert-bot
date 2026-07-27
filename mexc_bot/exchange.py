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

    Symbols as returned by MEXC contract ticker, e.g.:
      BTC_USDT (crypto), TSLASTOCK_USDT (legacy stock), TSLAUSDT (compact stock UI).

    resolve_symbol() maps friendly input (TSLA, zhipu, samsung) onto the live
    contract list so users do not need to guess STOCK / compact suffixes.
    """

    def __init__(
        self,
        base_url: str = "https://contract.mexc.com/api/v1",
        timeout: int = 10,
        symbol_cache_ttl_seconds: float = 120.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.symbol_cache_ttl_seconds = symbol_cache_ttl_seconds
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "mexc-alert-bot/0.3"})
        self._price_cache: Dict[str, float] = {}
        self._volume_cache: Dict[str, float] = {}
        self._price_cache_ts: float = 0.0

    @staticmethod
    def _volume_from_ticker_item(item: dict) -> Optional[float]:
        """Prefer quote turnover (amount24) so units stay comparable across symbols."""
        for key in ("amount24", "amount24Quote", "quoteVolume24", "turnover"):
            raw = item.get(key)
            if raw is None:
                continue
            try:
                v = float(raw)
                if v > 0:
                    return v
            except (TypeError, ValueError):
                continue
        return None

    def _fetch_batch(self) -> Dict[str, float]:
        """Fetch all contract last prices. Keys are uppercase with underscore.

        Also refreshes optional volume cache when fields are present.
        """
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
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                items = [data]
            else:
                logger.warning("Unexpected futures batch data shape")
                return {}

            prices: Dict[str, float] = {}
            volumes: Dict[str, float] = {}
            for item in items:
                if not isinstance(item, dict):
                    continue
                try:
                    sym = str(item.get("symbol", "")).upper()
                    if not sym or item.get("lastPrice") is None:
                        continue
                    prices[sym] = float(item["lastPrice"])
                    vol = self._volume_from_ticker_item(item)
                    if vol is not None:
                        volumes[sym] = vol
                except (TypeError, ValueError):
                    continue
            if volumes:
                self._volume_cache = volumes
            logger.debug(f"Futures batch fetched {len(prices)} prices")
            return prices
        except requests.RequestException as e:
            logger.warning(f"Futures batch price request failed: {e}")
        except Exception as e:
            logger.warning(f"Unexpected error in futures batch price fetch: {e}")
        return {}

    def get_all_volumes(self) -> Dict[str, float]:
        """24h volume/amount map from last futures batch (may be empty)."""
        # Ensure cache is warm; get_all_prices already force-refreshes for movers
        if not self._volume_cache:
            self._ensure_price_cache(force=True)
        return dict(self._volume_cache)

    def _ensure_price_cache(self, force: bool = False) -> Dict[str, float]:
        import time as _time

        now = _time.time()
        if (
            not force
            and self._price_cache
            and (now - self._price_cache_ts) < self.symbol_cache_ttl_seconds
        ):
            return self._price_cache
        prices = self._fetch_batch()
        if prices:
            self._price_cache = prices
            self._price_cache_ts = now
        return self._price_cache

    def resolve_symbol(self, raw: str) -> Optional[str]:
        """Map user input (TSLA, ZHIPU, BTC_USDT, …) to a live contract symbol."""
        known = set(self._ensure_price_cache().keys())
        if not known:
            # Offline / API blip: still try deterministic STOCK fallbacks
            return resolve_futures_symbol(raw, known=None)
        return resolve_futures_symbol(raw, known=known)

    def get_price(self, symbol: str) -> Optional[float]:
        """Resolve friendly name, then return last price (batch cache preferred)."""
        resolved = self.resolve_symbol(symbol)
        cache = self._ensure_price_cache()
        if resolved and resolved in cache:
            return cache[resolved]

        # Try resolved + candidate list via single endpoint as fallback
        tried: list[str] = []
        for cand in futures_symbol_candidates(symbol):
            if cand in tried:
                continue
            tried.append(cand)
            if cand in cache:
                return cache[cand]
            px = self._fetch_single(cand)
            if px is not None:
                self._price_cache[cand] = px
                return px
        if resolved and resolved not in tried:
            px = self._fetch_single(resolved)
            if px is not None:
                self._price_cache[resolved] = px
                return px
        return None

    def _fetch_single(self, sym: str) -> Optional[float]:
        url = f"{self.base_url}/contract/ticker"
        try:
            resp = self.session.get(url, params={"symbol": sym}, timeout=self.timeout)
            if resp.status_code != 200:
                logger.debug(f"MEXC futures {resp.status_code} for single {sym}")
                return None
            payload = resp.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, dict) and data.get("lastPrice") is not None:
                return float(data["lastPrice"])
            if isinstance(data, list) and data:
                item = data[0]
                if item.get("lastPrice") is not None:
                    return float(item["lastPrice"])
        except requests.RequestException as e:
            logger.warning(f"Request error (futures single) for {sym}: {e}")
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"Parse error (futures single) for {sym}: {e}")
        return None

    def get_all_prices(self) -> Dict[str, float]:
        """Fetch ALL current futures prices (refreshes resolve cache)."""
        return dict(self._ensure_price_cache(force=True))

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


# Friendly aliases → MEXC-style base tickers (before STOCK / _USDT resolution)
FUTURES_BASE_ALIASES: Dict[str, str] = {
    "TESLA": "TSLA",
    "GOOGLE": "GOOGL",
    "ALPHABET": "GOOGL",
    "FACEBOOK": "META",
    "FB": "META",
    "APPLE": "AAPL",
    "MICROSOFT": "MSFT",
    "AMAZON": "AMZN",
    "NETFLIX": "NFLX",
    "NVIDIA": "NVDA",
    "NVDIA": "NVDA",  # common typo
}


def normalize_futures_symbol(raw: str) -> str:
    """Normalize to MEXC contract form BASE_QUOTE (e.g. BTC_USDT).

    Accepts: BTC, BTC_USDT, BTCUSDT, btc/usdt, BTC-USDT.
    Bare base → append _USDT.

    Note: does NOT know about *STOCK* contracts. Prefer resolve_futures_symbol()
    when a live contract list is available.
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
            base = FUTURES_BASE_ALIASES.get(base, base)
            return f"{base}_{q}"
    # Bare base
    if s.isalpha() or (len(s) > 1 and s[:-1].isalpha() and s[-1].isdigit()):
        s = FUTURES_BASE_ALIASES.get(s, s)
        return f"{s}_USDT"
    return s


def _futures_input_base(raw: str) -> str:
    """Extract a base ticker from user input (TSLA, ZHIPUSTOCK, BTC_USDT, …)."""
    s = raw.strip().upper().replace("-", "_").replace("/", "_").replace(" ", "")
    if not s:
        return s
    # Alias whole token before stripping quote
    if s in FUTURES_BASE_ALIASES:
        return FUTURES_BASE_ALIASES[s]
    if "_" in s:
        parts = s.split("_")
        if parts[-1] in ("USDT", "USDC", "USD"):
            base = "_".join(parts[:-1])
        else:
            base = s
    else:
        base = s
        for q in ("USDT", "USDC", "USD"):
            if base.endswith(q) and len(base) > len(q):
                base = base[: -len(q)]
                break
    base = FUTURES_BASE_ALIASES.get(base, base)
    return base


def _futures_symbol_body(sym: str) -> Optional[str]:
    """Strip quote from a live contract id → base body.

    Supports both crypto-style underscores and compact stock UI form:
      BTC_USDT      → BTC
      TSLASTOCK_USDT → TSLASTOCK
      TSLAUSDT      → TSLA   (MEXC stock perps often omit the underscore)
    """
    s = str(sym or "").strip().upper()
    if not s:
        return None
    if "_" in s:
        parts = s.split("_")
        if parts[-1] in ("USDT", "USDC", "USD"):
            return "_".join(parts[:-1]) or None
        return s
    for q in ("USDT", "USDC", "USD"):
        if s.endswith(q) and len(s) > len(q):
            return s[: -len(q)]
    return s


def futures_symbol_candidates(raw: str) -> list[str]:
    """Ordered candidate contract ids for a user-typed futures symbol.

    Includes underscore form (BTC_USDT), compact stock form (TSLAUSDT),
    and *STOCK* legacy stock perps (TSLASTOCK_USDT).
    """
    base = _futures_input_base(raw)
    if not base:
        return []

    cores: list[str] = []
    # Prefer as-typed core first
    cores.append(base)
    if base.endswith("STOCK") and len(base) > 5:
        cores.append(base[: -len("STOCK")])
    else:
        cores.append(base + "STOCK")

    out: list[str] = []
    seen: set[str] = set()

    def add(sym: str) -> None:
        if sym and sym not in seen:
            seen.add(sym)
            out.append(sym)

    # Exact normalized form first (crypto-style)
    add(normalize_futures_symbol(raw))
    for core in cores:
        # Compact form first for stock-like names (UI: TSLAUSDT Perpetual)
        add(f"{core}USDT")
        add(f"{core}_USDT")
        if not core.endswith("STOCK"):
            add(f"{core}STOCK_USDT")
            add(f"{core}STOCKUSDT")
        else:
            bare = core[: -len("STOCK")]
            add(f"{bare}STOCK_USDT")
            add(f"{bare}STOCKUSDT")
            add(f"{bare}_USDT")
            add(f"{bare}USDT")
    return out


def resolve_futures_symbol(
    raw: str,
    known: Optional[set] = None,
) -> Optional[str]:
    """
    Map friendly input to a real MEXC futures contract symbol.

    When `known` is a set of live contract ids (from batch ticker):
      TSLA  → TSLAUSDT (stock UI form) or TSLASTOCK_USDT (legacy) or TSLA_USDT
      ZHIPU → ZHIPUSTOCK_USDT
      BTC   → BTC_USDT
      SAMSUNG → SAMSUNGUSDT / SAMSUNGSTOCK_USDT

    When `known` is None: returns the first deterministic candidate (usually BASE_USDT).
    """
    if not raw or not str(raw).strip():
        return None

    candidates = futures_symbol_candidates(raw)
    if not candidates:
        return None

    if not known:
        return candidates[0]

    known_u = {str(s).upper() for s in known}

    for cand in candidates:
        if cand in known_u:
            return cand

    base = _futures_input_base(raw)
    if not base:
        return None

    # Ranked search against the live universe (underscore + compact USDT ids)
    ranked: list[tuple[int, str]] = []
    for sym in known_u:
        body = _futures_symbol_body(sym)
        if not body:
            continue
        if body == base:
            # Prefer compact stock UI (TSLAUSDT) and crypto BASE_USDT equally strong
            ranked.append((0, sym))
        elif body == f"{base}STOCK":
            ranked.append((1, sym))  # stock perp *STOCK*
        elif body.startswith(f"{base}STOCK"):
            ranked.append((2, sym))
        elif body.startswith(f"{base}_"):
            ranked.append((4, sym))  # rare BASE_OTHER
        elif len(base) >= 4 and body.startswith(base):
            rest = body[len(base) :]
            if rest == "STOCK":
                ranked.append((1, sym))
            elif rest.startswith("STOCK"):
                ranked.append((2, sym))
            # else: weak prefix (BTC→BTCDOM) — skip for short bases; allow long unique
            elif len(base) >= 5 and rest.isalpha() and len(rest) <= 8:
                ranked.append((6, sym))

    if not ranked:
        return None
    ranked.sort(key=lambda t: (t[0], len(t[1])))
    best_rank, best_sym = ranked[0]
    # Accept strong matches always; weak only if unique
    if best_rank <= 2:
        return best_sym
    strong = [s for r, s in ranked if r <= 2]
    if strong:
        return strong[0]
    if best_rank <= 6 and len([r for r, _ in ranked if r == best_rank]) == 1:
        return best_sym
    return None
