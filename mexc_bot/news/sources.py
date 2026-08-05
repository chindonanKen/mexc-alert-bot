"""News source adapters — soft-fail, return normalized items."""

from __future__ import annotations

import html as html_lib
import logging
import re
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import requests

try:
    import certifi

    _CA = certifi.where()
except Exception:  # pragma: no cover
    _CA = True

from .tickers import extract_delist_bases

logger = logging.getLogger(__name__)

# Public Rekt RSS (leaderboard / posts)
REKT_RSS_URLS = (
    "https://rekt.news/rss.xml",
    "https://rekt.news/feed",
)

# MEXC announcement listing surfaces (section + general)
MEXC_LIST_URLS = (
    "https://www.mexc.com/support/sections/360000254192",  # delisting notices
    "https://www.mexc.com/announcements",
    "https://www.mexc.com/support/announcement",
)

_DELIST_TITLE = re.compile(
    r"delist|delisting|remov(e|al).*pair|suspend.*trad",
    re.I,
)


def _session() -> requests.Session:
    s = requests.Session()
    s.verify = _CA
    s.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/json,*/*",
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
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
            for item in root.findall(".//item"):
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                if not title:
                    continue
                body = (item.findtext("description") or "")[:800]
                bases = extract_delist_bases(title, body)
                out.append(
                    {
                        "title": title,
                        "url": link,
                        "ts": time.time(),
                        "source": "rekt.news",
                        "source_trust": "rekt",
                        "body": body,
                        "bases": bases,
                    }
                )
            if out:
                break
        except Exception as e:
            logger.warning("rekt RSS fetch failed %s: %s", url, e)
    return out[:40]


def _list_mexc_article_links(html: str, base_url: str) -> List[Dict[str, str]]:
    """Parse listing HTML for announcement article path + title."""
    found: List[Dict[str, str]] = []
    seen = set()
    # href first
    for path, title in re.findall(
        r'href="(/announcements/article/[^"]+)"[^>]*?(?:title|aria-label)="([^"]+)"',
        html,
    ):
        key = path.lower()
        if key in seen:
            continue
        seen.add(key)
        found.append({"path": path, "title": html_lib.unescape(title).strip()})
    if not found:
        for title, path in re.findall(
            r'(?:title|aria-label)="([^"]+)"[^>]*?href="(/announcements/article/[^"]+)"',
            html,
        ):
            key = path.lower()
            if key in seen:
                continue
            seen.add(key)
            found.append({"path": path, "title": html_lib.unescape(title).strip()})
    # absolute
    for path, title in re.findall(
        r'href="(https://www\.mexc\.com/announcements/article/[^"]+)"[^>]*?(?:title|aria-label)="([^"]+)"',
        html,
    ):
        key = path.lower()
        if key in seen:
            continue
        seen.add(key)
        found.append({"path": path, "title": html_lib.unescape(title).strip()})
    for item in found:
        p = item["path"]
        if p.startswith("http"):
            item["url"] = p
        else:
            item["url"] = urljoin(base_url, p)
    return found


def _enrich_mexc_article(
    sess: requests.Session, url: str, title: str, timeout: float
) -> Dict[str, Any]:
    """Fetch article page; pull full ticker list from meta/body (not just teaser title)."""
    body = ""
    bases: List[str] = []
    try:
        resp = sess.get(url, timeout=timeout)
        if resp.status_code != 200:
            return {"body": body, "bases": extract_delist_bases(title)}
        raw = resp.text
        plain = html_lib.unescape(raw)
        # meta description often has full list: "delisting UPST, AFRM, AKAM, HBM and"
        for m in re.finditer(
            r'<meta\s+name="description"\s+content="([^"]{20,500})"',
            raw,
            re.I,
        ):
            body += " " + html_lib.unescape(m.group(1))
        # bold ticker groups
        for m in re.finditer(
            r"(?:font-weight:bolder[^>]*>|<strong[^>]*>)\s*([^<]{3,120})\s*<",
            plain,
            re.I,
        ):
            chunk = m.group(1).strip()
            if re.search(r"[A-Z]{2,12}", chunk):
                body += " " + chunk
        # plain delist sentences
        for m in re.finditer(
            r"(?:will be delisting|delisting|delist)\s+([A-Z0-9,\s]{5,200})",
            plain,
            re.I,
        ):
            body += " " + m.group(0)
        bases = extract_delist_bases(title, body)
        # If title still has "and N other" and we got few bases, keep meta scan
        if re.search(r"and\s+\d+\s+other", title, re.I) and len(bases) < 3:
            # broader: all comma-lists near delist
            for m in re.finditer(
                r"delist\w*\s+([A-Z]{2,15}(?:\s*,\s*[A-Z]{2,15}){1,20})",
                plain,
                re.I,
            ):
                bases = extract_delist_bases(title, body, m.group(0))
                if len(bases) >= 3:
                    break
    except Exception as e:
        logger.debug("mexc article enrich %s: %s", url, e)
        bases = extract_delist_bases(title)
    return {"body": (body or "")[:2000], "bases": bases}


def fetch_mexc_announcements(timeout: float = 18.0) -> List[Dict[str, Any]]:
    """Pull MEXC announcement titles; for delists, open article and list ALL tickers."""
    out: List[Dict[str, Any]] = []
    sess = _session()
    links: List[Dict[str, str]] = []
    for list_url in MEXC_LIST_URLS:
        try:
            resp = sess.get(list_url, timeout=timeout)
            if resp.status_code != 200:
                continue
            links.extend(_list_mexc_article_links(resp.text, list_url))
            if links:
                break
        except Exception as e:
            logger.warning("MEXC list fetch failed %s: %s", list_url, e)

    # Fallback: old title scrape if no article links
    if not links:
        for list_url in MEXC_LIST_URLS:
            try:
                resp = sess.get(list_url, timeout=timeout)
                if resp.status_code != 200:
                    continue
                candidates = re.findall(
                    r"(?i)>([^<]*(?:delist|suspend|remov|listing|security|hack)[^<]{0,120})<",
                    resp.text,
                )
                for t in candidates:
                    title = re.sub(r"\s+", " ", t).strip()
                    if len(title) < 12 or len(title) > 220:
                        continue
                    out.append(
                        {
                            "title": title,
                            "url": list_url,
                            "ts": time.time(),
                            "source": "mexc-announcements",
                            "source_trust": "official",
                            "body": "",
                            "bases": extract_delist_bases(title),
                        }
                    )
                if out:
                    break
            except Exception as e:
                logger.warning("MEXC announce scrape failed %s: %s", list_url, e)

    seen_titles = set()
    for link in links:
        title = (link.get("title") or "").strip()
        if not title or len(title) < 8:
            continue
        key = title.lower()
        if key in seen_titles:
            continue
        seen_titles.add(key)
        url = link.get("url") or ""
        is_delist = bool(_DELIST_TITLE.search(title))
        body = ""
        bases: List[str] = extract_delist_bases(title)
        if is_delist and "/announcements/article/" in url:
            enr = _enrich_mexc_article(sess, url, title, timeout=min(15.0, timeout))
            body = enr.get("body") or ""
            if enr.get("bases"):
                bases = enr["bases"]
        # Prefer full ticker list in display title when we expanded "and N other"
        display_title = title
        if bases and re.search(r"and\s+\d+\s+other", title, re.I):
            display_title = f"{title} · full: {', '.join(bases)}"
        elif bases and is_delist and "and" not in title.lower() and len(bases) > 1:
            # title already complete; keep
            pass
        out.append(
            {
                "title": display_title[:500],
                "list_title": title[:500],
                "url": url,
                "ts": time.time(),
                "source": "mexc-announcements",
                "source_trust": "official",
                "body": body,
                "bases": bases,
            }
        )
        if len(out) >= 40:
            break

    return out[:40]


def enrich_news_item_bases(item: Dict[str, Any]) -> List[str]:
    """Bases from item or re-extract from title/body/raw."""
    if item.get("bases"):
        return list(item["bases"])
    raw = item.get("raw") or item.get("raw_json")
    if isinstance(raw, str):
        try:
            import json

            raw = json.loads(raw)
        except Exception:
            raw = None
    if isinstance(raw, dict) and raw.get("bases"):
        return list(raw["bases"])
    return extract_delist_bases(
        item.get("title") or "",
        item.get("body") or "",
        (raw or {}).get("body") if isinstance(raw, dict) else "",
        (raw or {}).get("list_title") if isinstance(raw, dict) else "",
    )
