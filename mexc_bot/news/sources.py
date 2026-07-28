"""News source adapters — soft-fail, return normalized items."""

from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, List

import requests

try:
    import certifi

    _CA = certifi.where()
except Exception:  # pragma: no cover
    _CA = True

logger = logging.getLogger(__name__)

# Public Rekt RSS (leaderboard / posts)
REKT_RSS_URLS = (
    "https://rekt.news/rss.xml",
    "https://rekt.news/feed",
)

# MEXC announcements (HTML listing — best-effort scrape titles)
MEXC_ANNOUNCE_URLS = (
    "https://www.mexc.com/support/announcement",
    "https://www.mexc.com/announcements",
)


def _session() -> requests.Session:
    s = requests.Session()
    s.verify = _CA
    s.headers.update({"User-Agent": "mexc-alert-bot-news/1.0"})
    return s


def fetch_rekt_rss(timeout: float = 15.0) -> List[Dict[str, Any]]:
    """Return list of {title, url, ts, source, source_trust}."""
    out: List[Dict[str, Any]] = []
    sess = _session()
    for url in REKT_RSS_URLS:
        try:
            resp = sess.get(url, timeout=timeout)
            if resp.status_code != 200:
                continue
            root = ET.fromstring(resp.content)
            # RSS 2.0
            for item in root.findall(".//item"):
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                if not title:
                    continue
                out.append(
                    {
                        "title": title,
                        "url": link,
                        "ts": time.time(),
                        "source": "rekt.news",
                        "source_trust": "rekt",
                        "body": (item.findtext("description") or "")[:500],
                    }
                )
            if out:
                break
        except Exception as e:
            logger.warning("rekt RSS fetch failed %s: %s", url, e)
    return out[:40]


def fetch_mexc_announcements(timeout: float = 15.0) -> List[Dict[str, Any]]:
    """Best-effort: pull announcement-like titles from MEXC support pages."""
    out: List[Dict[str, Any]] = []
    sess = _session()
    for url in MEXC_ANNOUNCE_URLS:
        try:
            resp = sess.get(url, timeout=timeout)
            if resp.status_code != 200:
                continue
            html = resp.text
            # Titles often in <a>…delist…</a> or JSON-ish
            candidates = re.findall(
                r"(?i)>([^<]*(?:delist|suspend|remov|listing|security|hack)[^<]{0,120})<",
                html,
            )
            for t in candidates:
                title = re.sub(r"\s+", " ", t).strip()
                if len(title) < 12 or len(title) > 200:
                    continue
                out.append(
                    {
                        "title": title,
                        "url": url,
                        "ts": time.time(),
                        "source": "mexc-announcements",
                        "source_trust": "official",
                        "body": "",
                    }
                )
            if out:
                break
        except Exception as e:
            logger.warning("MEXC announce fetch failed %s: %s", url, e)
    # de-dupe titles
    seen = set()
    uniq = []
    for item in out:
        k = item["title"].lower()
        if k in seen:
            continue
        seen.add(k)
        uniq.append(item)
    return uniq[:40]
