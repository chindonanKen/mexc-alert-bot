"""Multi-CEX delist / monitoring-tag radar — background cache only.

Binance HTML announcement pages are SPA shells (empty of titles). We use the
public CMS JSON API for catalog 161 (Delisting) and optional article detail
for ticker extraction. Other CEXs remain best-effort HTML scrapes.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

try:
    import certifi

    _CA = certifi.where()
except Exception:  # pragma: no cover
    _CA = True

from .store import InvestigatorStore

logger = logging.getLogger(__name__)

# Official-ish announcement surfaces
# kind: "cms" = Binance JSON CMS; "html" = soft-fail scrape
CEX_SOURCES: List[Tuple[str, str, str]] = [
    # exchange, url_or_endpoint, fetch_mode
    (
        "binance",
        "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
        "?type=1&pageNo=1&pageSize=20&catalogId=161",
        "binance_cms",
    ),
    ("okx", "https://www.okx.com/help/section/announcements-delistings", "html"),
    ("bybit", "https://announcements.bybit.com/en/?category=delistings", "html"),
    ("bybit", "https://announcements.bybit.com/en/", "html"),
    ("mexc", "https://www.mexc.com/support/announcement", "html"),
]

# Binance delist catalog (confirmed live)
_BINANCE_CMS_LIST = (
    "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
    "?type=1&pageNo=1&pageSize=20&catalogId=161"
)
_BINANCE_CMS_DETAIL = (
    "https://www.binance.com/bapi/composite/v1/public/cms/article/detail/query"
    "?articleCode={code}"
)
_BINANCE_ANN_URL = "https://www.binance.com/en/support/announcement/detail/{code}"

_DELIST_RE = re.compile(
    r"(delist|delisting|will remove|removal of.*(spot|pair|contract)|"
    r"cease trading|monitoring tag|seed tag|remove.*trading pair)",
    re.I,
)
# Rough base extraction: CAPS tickers 2–12 chars near delist context
_TICKER_RE = re.compile(r"\b([A-Z]{2,12})(?:USDT|USD|/USDT)?\b")
# Binance often writes "Across Protocol (ACX)" or lists in titles
_PAREN_TICKER_RE = re.compile(r"\(([A-Z]{2,12})\)")


def _session() -> requests.Session:
    s = requests.Session()
    s.verify = _CA
    s.headers.update(
        {
            # Browser-like UA — Binance CMS returns 400 on bot-looking agents
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json,text/html,*/*",
            "lang": "en",
            "clienttype": "web",
        }
    )
    return s


def _fp(exchange: str, title: str) -> str:
    return hashlib.sha256(f"{exchange}|{title}".encode()).hexdigest()[:40]


# Common English / announcement noise that is not a crypto base
_STOP_BASES = {
    "THE", "AND", "FOR", "WILL", "SPOT", "PAIR", "PAIRS", "USDT", "USD", "BUSD",
    "PERPETUAL", "CONTRACT", "CONTRACTS", "FUTURES", "MARGIN", "TRADING", "NOTICE",
    "REMOVAL", "BINANCE", "OKX", "BYBIT", "MEXC", "UTC", "FROM", "WITH", "THAT",
    "THIS", "HTTP", "HTTPS", "WWW", "COM", "EN", "SUPPORT", "PROTOCOL", "TOKEN",
    "COIN", "PROJECT", "REGARDING", "CONVERSION", "MULTIPLE", "FOLLOWING",
    "GENERAL", "EXCHANGE", "DELIST", "DELISTING", "DELISTINGS", "REMOVE",
    "REMOVED", "CEASE", "SERVICES", "PRODUCTS", "REFERRED", "HERE", "MAY",
    "NOT", "BE", "AVAILABLE", "YOUR", "REGION", "FELLOW", "BINANCIANS", "WHEN",
    "WE", "CONDUCT", "THESE", "REVIEWS", "CONSIDER", "VARIETY", "FACTORS",
    "BELOW", "ARE", "UPDATED", "METRICS", "LOOK", "INFLUENCE", "WHETHER",
    "DECIDE", "DIGITAL", "ASSET", "ASSETS", "COMMITMENT", "TEAM", "LEVEL",
    "QUALITY", "DEVELOPMENT", "ACTIVITY", "VOLUME", "LIQUIDITY", "STABILITY",
    "SAFETY", "NETWORK", "ATTACKS", "PUBLIC", "COMMUNICATION", "COMMUNITY",
    "ENGAGEMENT", "TRANSPARENCY", "RESPONSIVENESS", "PERIODIC", "DUE",
    "DILIGENCE", "REQUESTS", "EVIDENCE", "UNETHICAL", "FRAUDULENT", "CONDUCT",
    "NEGLIGENCE", "NEW", "REGULATORY", "REQUIREMENTS", "MATERIAL", "INCREASE",
    "SUPPLY", "CHANGES", "TOKENOMICS", "IMPACT", "OWNERSHIP", "STRUCTURE",
    "CORE", "MEMBERS", "SENTIMENTS", "BASED", "OUR", "MOST", "RECENT",
    "HAVE", "DECIDED", "ALL", "FOLLOWING", "AT", "ON", "OF", "TO", "IS", "AN",
    "OR", "IN", "AS", "BY", "ITS", "HAS", "WAS", "WERE", "CAN", "MORE", "THAN",
    "ALSO", "PLEASE", "NOTE", "USERS", "USER", "WHO", "HOLD", "SHOULD", "BEFORE",
    "AFTER", "DURING", "UNTIL", "VIA", "API", "WEB", "APP", "USD", "EUR", "GBP",
}


def _is_plausible_base(tok: str) -> bool:
    if not tok or len(tok) < 2 or len(tok) > 12:
        return False
    if tok in _STOP_BASES or tok.isdigit():
        return False
    # Require at least one letter; reject pure short English-looking all-vowel noise
    if not re.search(r"[A-Z]", tok):
        return False
    # Very common English 2–3 letter words already in stop; keep short tickers like OP, AR
    return True


def _extract_bases(title: str) -> List[str]:
    """Prefer (TICKER) and Delist TITLE lists; avoid English body words."""
    found: List[str] = []
    seen = set()
    text = title.upper()

    def _add(m: str) -> None:
        if m in seen or not _is_plausible_base(m):
            return
        seen.add(m)
        found.append(m)

    # 1) Parenthetical tickers: "Across Protocol (ACX)"
    for m in _PAREN_TICKER_RE.findall(text):
        _add(m)

    # 2) Explicit "Will Delist A, B, C on DATE" / "Delist A B C"
    m = re.search(
        r"(?:WILL\s+)?DELIST(?:\s+USD[ⓈS]?-?M)?\s+([A-Z0-9_,\s/-]+?)(?:\s+ON\s+|\s+PERPETUAL|\s*$)",
        text,
    )
    if m:
        chunk = m.group(1)
        for part in re.split(r"[\s,/]+", chunk):
            part = re.sub(r"(?:USDT|BUSD|USD)$", "", part)
            part = part.strip("_-")
            if part:
                _add(part)

    # 3) PAIR forms BASE/QUOTE (QNT/BTC, RPL/USDC) or BASEUSDT
    for m in re.findall(r"\b([A-Z]{2,12})/(?:USDT|USDC|BUSD|BTC|ETH|BNB|FDUSD|TUSD)\b", text):
        _add(m)
    for m in re.findall(r"\b([A-Z]{2,12})(?:USDT|USDC)\b", text):
        _add(m)

    # 4) If still empty and short title-like, fall back to CAPS tokens (title only)
    if not found and len(text) < 160:
        for m in _TICKER_RE.findall(text):
            _add(m)

    return found[:16]


def _html_text_candidates(html: str) -> List[str]:
    candidates = re.findall(
        r"(?:>)([^<>]{15,200}(?:[Dd]elist|[Rr]emov|[Mm]onitoring)[^<>]{0,120})<",
        html,
    )
    if not candidates:
        candidates = re.findall(
            r'"(?:title|Title)"\s*:\s*"([^"]{10,200})"',
            html,
        )
    return candidates


def fetch_binance_cms(timeout: float = 20.0) -> List[Dict[str, Any]]:
    """Pull Binance Delisting catalog (catalogId=161) via public CMS API."""
    out: List[Dict[str, Any]] = []
    try:
        resp = _session().get(_BINANCE_CMS_LIST, timeout=timeout)
        if resp.status_code != 200:
            logger.warning("binance CMS list HTTP %s", resp.status_code)
            return out
        payload = resp.json()
        if str(payload.get("code")) not in ("000000", "0", ""):
            logger.warning("binance CMS list code=%s", payload.get("code"))
            return out
        data = payload.get("data") or {}
        articles: List[dict] = []
        catalogs = data.get("catalogs") or []
        for cat in catalogs:
            if int(cat.get("catalogId") or 0) == 161 or str(
                cat.get("catalogName") or ""
            ).lower() == "delisting":
                articles.extend(cat.get("articles") or [])
        if not articles and data.get("articles"):
            articles = list(data.get("articles") or [])

        seen = set()
        for art in articles[:40]:
            title = (art.get("title") or "").strip()
            if not title or len(title) < 8:
                continue
            key = title.lower()
            if key in seen:
                continue
            seen.add(key)
            # Keep delist / removal / convert notices
            if not _DELIST_RE.search(title) and "delist" not in title.lower():
                if "remov" not in title.lower() and "conversion" not in title.lower():
                    continue
            code = art.get("code") or ""
            url = _BINANCE_ANN_URL.format(code=code) if code else _BINANCE_CMS_LIST
            bases = _extract_bases(title)
            # Enrich bases from article body when title is generic
            # ("Notice of Removal of Spot Trading Pairs")
            if (len(bases) < 2 or "removal of" in title.lower()) and code:
                more = _binance_detail_bases(code, timeout=min(12.0, timeout))
                for b in more:
                    if b not in bases:
                        bases.append(b)
                bases = bases[:16]
            release = art.get("releaseDate")
            ts = time.time()
            if release is not None:
                try:
                    # CMS uses ms epoch
                    ts = float(release) / 1000.0 if float(release) > 1e12 else float(release)
                except (TypeError, ValueError):
                    pass
            kind = "monitor_tag" if re.search(r"monitor|seed tag", title, re.I) else "delist"
            out.append(
                {
                    "exchange": "binance",
                    "title": title,
                    "url": url,
                    "kind": kind,
                    "bases": bases,
                    "ts": ts,
                    "article_code": code,
                }
            )
        return out
    except Exception as e:
        logger.warning("binance CMS fetch failed: %s", e)
        return out


def _binance_detail_bases(article_code: str, timeout: float = 12.0) -> List[str]:
    """Parse tickers from article body JSON (soft-fail)."""
    try:
        url = _BINANCE_CMS_DETAIL.format(code=article_code)
        resp = _session().get(url, timeout=timeout)
        if resp.status_code != 200:
            return []
        payload = resp.json()
        data = payload.get("data") or {}
        body = data.get("body") or ""
        title = data.get("title") or ""
        texts = re.findall(r'"text"\s*:\s*"((?:\\.|[^"\\])*)"', body)
        plain = " ".join(texts) if texts else body
        # Unescape common JSON string escapes
        plain = plain.replace("\\n", " ").replace("\\t", " ").replace('\\"', '"')
        plain = re.sub(r"\\u[0-9a-fA-F]{4}", " ", plain)
        plain = re.sub(r"<[^>]+>", " ", plain)
        plain = re.sub(r"\s+", " ", plain)
        return _extract_bases(f"{title} {plain}")
    except Exception as e:
        logger.debug("binance detail %s: %s", article_code, e)
        return []


def fetch_cex_titles(exchange: str, url: str, timeout: float = 15.0) -> List[Dict[str, Any]]:
    """Best-effort title harvest — CMS for Binance, HTML for others."""
    if exchange == "binance" or "bapi/composite" in (url or ""):
        return fetch_binance_cms(timeout=timeout)

    out: List[Dict[str, Any]] = []
    try:
        resp = _session().get(url, timeout=timeout)
        if resp.status_code != 200:
            logger.warning("delist radar %s HTTP %s", exchange, resp.status_code)
            return out
        html = resp.text
        candidates = _html_text_candidates(html)
        seen = set()
        for raw in candidates:
            title = re.sub(r"\s+", " ", raw).strip()
            if len(title) < 12:
                continue
            if not _DELIST_RE.search(title) and "delist" not in title.lower():
                if "monitor" not in title.lower() and "seed" not in title.lower():
                    continue
            key = title.lower()
            if key in seen:
                continue
            seen.add(key)
            kind = "monitor_tag" if re.search(r"monitor|seed tag", title, re.I) else "delist"
            bases = _extract_bases(title)
            out.append(
                {
                    "exchange": exchange,
                    "title": title,
                    "url": url,
                    "kind": kind,
                    "bases": bases,
                    "ts": time.time(),
                }
            )
        return out[:40]
    except Exception as e:
        logger.warning("delist radar fetch %s failed: %s", exchange, e)
        return out


class DelistRadar:
    """Background poller that fills delist_cache from major CEXs."""

    def __init__(
        self,
        store: InvestigatorStore,
        *,
        poll_seconds: float = 180.0,
    ):
        self.store = store
        self.poll_seconds = max(60.0, float(poll_seconds))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_cycle_ms = 0
        self._items = 0
        self._by_exchange: Dict[str, int] = {}

    def get_health(self) -> dict:
        return {
            "running": self._thread is not None and self._thread.is_alive(),
            "last_cycle_ms": self._last_cycle_ms,
            "items_cached": self._items,
            "by_exchange": dict(self._by_exchange),
        }

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self.run, name="delist-radar", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)

    def run(self) -> None:
        logger.info("Delist radar started poll=%ss", self.poll_seconds)
        while not self._stop.is_set():
            try:
                self._check_once()
            except Exception as e:
                logger.exception("Delist radar error: %s", e)
            slept = 0.0
            while slept < self.poll_seconds and not self._stop.is_set():
                time.sleep(min(1.0, self.poll_seconds - slept))
                slept += 1.0

    def _check_once(self) -> None:
        t0 = time.perf_counter()
        cycle_counts: Dict[str, int] = {}
        for exchange, url, mode in CEX_SOURCES:
            if mode == "binance_cms" or exchange == "binance":
                items = fetch_binance_cms()
            else:
                items = fetch_cex_titles(exchange, url)
            for item in items:
                bases = item.get("bases") or []
                if not bases:
                    bases = [None]
                for base in bases:
                    self.store.upsert_delist(
                        exchange=item.get("exchange") or exchange,
                        base=base,
                        title=item["title"],
                        url=item.get("url"),
                        kind=item.get("kind") or "delist",
                        ts=float(item.get("ts") or time.time()),
                        fingerprint=_fp(
                            item.get("exchange") or exchange,
                            f"{base}|{item['title']}",
                        ),
                        raw=item,
                    )
                    self._items += 1
                    ex = item.get("exchange") or exchange
                    cycle_counts[ex] = cycle_counts.get(ex, 0) + 1
        for ex, n in cycle_counts.items():
            self._by_exchange[ex] = self._by_exchange.get(ex, 0) + n
        self._last_cycle_ms = int((time.perf_counter() - t0) * 1000)
        logger.info(
            "Delist radar cycle %sms items≈%s by_ex=%s",
            self._last_cycle_ms,
            self._items,
            cycle_counts,
        )

    def refresh_now(self) -> None:
        """Optional on-demand refresh (still async from fire path if called from worker)."""
        try:
            self._check_once()
        except Exception as e:
            logger.warning("delist refresh_now failed: %s", e)
