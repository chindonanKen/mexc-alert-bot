"""Background fatal-news poller.

Soft-fail: never blocks movers/targets. Pushes only confirmable fatal-class hits
matched to watchlist / open journal bases when possible.
"""

from __future__ import annotations

import hashlib
import html as _html
import logging
import threading
import time
from typing import Callable, Optional, Set

from .classify import evaluate_headline
from .sources import fetch_mexc_announcements, fetch_rekt_rss
from .store import NewsStore

logger = logging.getLogger(__name__)


class NewsWatcher:
    def __init__(
        self,
        news_store: NewsStore,
        notifier: Callable[..., None],
        get_watch_bases: Callable[[], Set[str]],
        *,
        poll_seconds: float = 90.0,
        push_unconfirmed: bool = False,
        get_notify_user_ids: Optional[Callable[[], list]] = None,
    ):
        self.news_store = news_store
        self.notifier = notifier
        self.get_watch_bases = get_watch_bases
        self.poll_seconds = max(30.0, float(poll_seconds))
        self.push_unconfirmed = push_unconfirmed
        self.get_notify_user_ids = get_notify_user_ids or (lambda: [])
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_cycle_ms = 0
        self._items_seen = 0
        self._pushes = 0

    def get_health(self) -> dict:
        return {
            "running": self._thread is not None and self._thread.is_alive(),
            "last_cycle_ms": self._last_cycle_ms,
            "items_seen": self._items_seen,
            "pushes": self._pushes,
        }

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self.run, name="news-watcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)

    def run(self) -> None:
        logger.info("News watcher started poll=%ss", self.poll_seconds)
        while not self._stop.is_set():
            try:
                self._check_once()
            except Exception as e:
                logger.exception("News watcher error: %s", e)
            slept = 0.0
            while slept < self.poll_seconds and not self._stop.is_set():
                time.sleep(min(1.0, self.poll_seconds - slept))
                slept += 1.0

    def _check_once(self) -> None:
        t0 = time.perf_counter()
        items = []
        try:
            items.extend(fetch_rekt_rss())
        except Exception as e:
            logger.warning("rekt source error: %s", e)
        try:
            items.extend(fetch_mexc_announcements())
        except Exception as e:
            logger.warning("mexc announce source error: %s", e)

        bases = set()
        try:
            bases = set(self.get_watch_bases() or set())
        except Exception:
            bases = set()

        for item in items:
            self._items_seen += 1
            title = item.get("title") or ""
            body = item.get("body") or ""
            trust = item.get("source_trust") or "aggregate"
            all_bases = list(item.get("bases") or [])
            if not all_bases:
                from .tickers import extract_delist_bases

                all_bases = extract_delist_bases(title, body)
            decision = evaluate_headline(
                title,
                body=body,
                source_trust=trust,
                symbol=item.get("symbol"),
                item_bases=all_bases,
                book_bases=bases,
                push_unconfirmed=self.push_unconfirmed,
            )
            cls = decision.get("cls")
            severity = decision.get("severity")
            if not cls:
                continue
            fp = hashlib.sha256(
                f"{item.get('source')}|{title}|{cls}".encode()
            ).hexdigest()[:40]
            if self.news_store.has_fingerprint(fp):
                continue

            symbol = (
                ",".join(all_bases)
                if all_bases
                else (item.get("symbol") or None)
            )
            raw_item = dict(item)
            raw_item["bases"] = all_bases

            nid = self.news_store.insert(
                symbol=symbol,
                class_=cls,
                severity=severity,
                title=title[:500],
                url=item.get("url"),
                source=item.get("source") or "unknown",
                source_trust=trust,
                ts=item.get("ts"),
                raw=raw_item,
                fingerprint=fp,
            )
            if not nid:
                continue

            do_push = bool(decision.get("alarm"))
            if not do_push:
                logger.info(
                    "news stored silent id=%s class=%s sev=%s title=%s bases=%s",
                    nid,
                    cls,
                    severity,
                    title[:80],
                    all_bases,
                )
                continue

            users = []
            try:
                users = list(self.get_notify_user_ids() or [])
            except Exception:
                users = []
            if not users:
                logger.info("news fatal but no notify users configured id=%s", nid)
                continue

            tickers = ", ".join(all_bases) if all_bases else (symbol or "—")
            msg = (
                f"⚠️ <b>DEVASTATING NEWS</b> · {_html.escape(cls)}\n"
                f"Tickers: <b>{_html.escape(tickers)}</b>\n"
                f"source: {_html.escape(str(item.get('source')))}\n"
                f"{_html.escape(title[:320])}"
            )
            for uid in users:
                try:
                    self.notifier(uid, msg, parse_mode="HTML")
                    self._pushes += 1
                except Exception as e:
                    logger.error("news notify user=%s failed: %s", uid, e)

        self._last_cycle_ms = int((time.perf_counter() - t0) * 1000)
