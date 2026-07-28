"""Multi-CEX delist / monitoring-tag radar — background cache only."""

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

# Official-ish announcement surfaces (HTML scrape — soft-fail)
CEX_SOURCES: List[Tuple[str, str, str]] = [
    # exchange, url, kind_default
    ("binance", "https://www.binance.com/en/support/announcement/list/161", "delist"),
    ("binance", "https://www.binance.com/en/support/announcement", "announce"),
    ("okx", "https://www.okx.com/help/section/announcements-delistings", "delist"),
    ("bybit", "https://announcements.bybit.com/en/?category=delistings", "delist"),
    ("bybit", "https://announcements.bybit.com/en/", "announce"),
    ("mexc", "https://www.mexc.com/support/announcement", "announce"),
]

_DELIST_RE = re.compile(
    r"(delist|delisting|will remove|removal of.*(spot|pair|contract)|"
    r"cease trading|monitoring tag|seed tag|remove.*trading pair)",
    re.I,
)
# Rough base extraction: CAPS tickers 2–12 chars near delist context
_TICKER_RE = re.compile(r"\b([A-Z]{2,12})(?:USDT|USD|/USDT)?\b")


def _session() -> requests.Session:
    s = requests.Session()
    s.verify = _CA
    s.headers.update({"User-Agent": "mexc-alert-bot-delist-radar/1.0"})
    return s


def _fp(exchange: str, title: str) -> str:
    return hashlib.sha256(f"{exchange}|{title}".encode()).hexdigest()[:40]


def _extract_bases(title: str) -> List[str]:
    stop = {
        "THE", "AND", "FOR", "WILL", "SPOT", "PAIR", "PAIRS", "USDT", "USD",
        "PERPETUAL", "CONTRACT", "FUTURES", "MARGIN", "TRADING", "NOTICE",
        "REMOVAL", "BINANCE", "OKX", "BYBIT", "MEXC", "UTC", "FROM", "WITH",
        "THIS", "THAT", "HTTP", "HTTPS", "WWW", "COM", "EN", "SUPPORT",
    }
    found = []
    for m in _TICKER_RE.findall(title.upper()):
        if m in stop or m.isdigit():
            continue
        if len(m) < 2:
            continue
        found.append(m)
    return found[:12]


def fetch_cex_titles(exchange: str, url: str, timeout: float = 15.0) -> List[Dict[str, Any]]:
    """Best-effort title harvest from announcement HTML."""
    out: List[Dict[str, Any]] = []
    try:
        resp = _session().get(url, timeout=timeout)
        if resp.status_code != 200:
            logger.warning("delist radar %s HTTP %s", exchange, resp.status_code)
            return out
        html = resp.text
        # Common patterns: anchor text, JSON title fields
        candidates = re.findall(
            r"(?:>)([^<>]{15,200}(?:[Dd]elist|[Rr]emov|[Mm]onitoring)[^<>]{0,120})<",
            html,
        )
        if not candidates:
            candidates = re.findall(
                r'"(?:title|Title)"\s*:\s*"([^"]{10,200})"',
                html,
            )
        seen = set()
        for raw in candidates:
            title = re.sub(r"\s+", " ", raw).strip()
            if len(title) < 12:
                continue
            if not _DELIST_RE.search(title) and "delist" not in title.lower():
                # keep monitoring-tag lines too
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

    def get_health(self) -> dict:
        return {
            "running": self._thread is not None and self._thread.is_alive(),
            "last_cycle_ms": self._last_cycle_ms,
            "items_cached": self._items,
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
        # First pass soon after start
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
        for exchange, url, _kind in CEX_SOURCES:
            for item in fetch_cex_titles(exchange, url):
                bases = item.get("bases") or [None]
                if not bases:
                    bases = [None]
                for base in bases:
                    self.store.upsert_delist(
                        exchange=exchange,
                        base=base,
                        title=item["title"],
                        url=item.get("url"),
                        kind=item.get("kind") or "delist",
                        ts=float(item.get("ts") or time.time()),
                        fingerprint=_fp(exchange, f"{base}|{item['title']}"),
                        raw=item,
                    )
                    self._items += 1
        self._last_cycle_ms = int((time.perf_counter() - t0) * 1000)
        logger.info("Delist radar cycle %sms items≈%s", self._last_cycle_ms, self._items)

    def refresh_now(self) -> None:
        """Optional on-demand refresh (still async from fire path if called from worker)."""
        try:
            self._check_once()
        except Exception as e:
            logger.warning("delist refresh_now failed: %s", e)
