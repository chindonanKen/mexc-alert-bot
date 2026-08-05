"""Isolated-dump specialist: delist/hack/scam evidence → Telegram follow-up.

Never blocks the mover fire path. Learns source weights over time.
"""

from __future__ import annotations

import html as _html
import logging
import time
from typing import Callable, List, Optional, Set

from ..news.classify import classify_headline, extract_symbol_hints
from ..news.sources import fetch_rekt_rss
from .queue import InvestigationJob, InvestigationQueue, InvestigationWorker
from .radar import DelistRadar
from .store import InvestigatorStore
from .triggers import IsolatedDumpCriteria, should_investigate_isolated

logger = logging.getLogger(__name__)


def _base_from_symbol(symbol: str) -> str:
    s = (symbol or "").upper().replace("-", "_")
    for suf in ("_USDT", "USDT", "_USD", "USD"):
        if s.endswith(suf):
            s = s[: -len(suf)]
            break
    return s.replace("_", "")


class IsolatedDumpAgent:
    def __init__(
        self,
        store: InvestigatorStore,
        notifier: Callable[..., None],
        *,
        radar: Optional[DelistRadar] = None,
        criteria: Optional[IsolatedDumpCriteria] = None,
        cooldown_seconds: float = 900.0,
        always_report: bool = True,
        notify_none: bool = True,
        learning_outcome_horizon: int = 14400,
        get_price: Optional[Callable[[str, str], Optional[float]]] = None,
    ):
        self.store = store
        self.notifier = notifier
        self.radar = radar
        self.criteria = criteria or IsolatedDumpCriteria()
        self.cooldown_seconds = max(60.0, float(cooldown_seconds))
        self.always_report = always_report
        self.notify_none = notify_none
        self.learning_outcome_horizon = int(learning_outcome_horizon)
        self.get_price = get_price
        self.queue = InvestigationQueue(maxsize=300)
        self.worker = InvestigationWorker(self.queue, self._process_job)
        self._cooldown: dict = {}  # (user_id, market, symbol) -> mono
        self._outcome_thread_stop = None
        self._outcome_thread = None

    def get_health(self) -> dict:
        h = self.worker.get_health()
        h["cooldown_keys"] = len(self._cooldown)
        return h

    def start(self) -> None:
        self.worker.start()
        # Outcome learning loop (soft)
        import threading

        self._outcome_thread_stop = threading.Event()
        self._outcome_thread = threading.Thread(
            target=self._outcome_loop, name="inv-outcome-learn", daemon=True
        )
        self._outcome_thread.start()

    def stop(self) -> None:
        self.worker.stop()
        if self._outcome_thread_stop:
            self._outcome_thread_stop.set()
        if self._outcome_thread and self._outcome_thread.is_alive():
            self._outcome_thread.join(timeout=3.0)

    def maybe_enqueue(
        self,
        *,
        user_id: int,
        symbol: str,
        market: str,
        drop_pct: float,
        user_threshold_pct: float,
        velocity_band: Optional[str] = None,
        heat_breadth: Optional[int] = None,
        watchlist_count: Optional[int] = None,
        event_id: Optional[int] = None,
        price: Optional[float] = None,
    ) -> bool:
        """
        Called from mover path AFTER notify. Never blocks.
        Returns True if job enqueued.
        """
        try:
            if not should_investigate_isolated(
                drop_pct=drop_pct,
                user_threshold_pct=user_threshold_pct,
                velocity_band=velocity_band,
                heat_dumping_count=heat_breadth,
                watchlist_count=watchlist_count,
                criteria=self.criteria,
            ):
                return False
            key = (user_id, market, symbol)
            now = time.monotonic()
            last = self._cooldown.get(key)
            if last is not None and (now - last) < self.cooldown_seconds:
                logger.debug("isolated agent cooldown skip %s", key)
                return False
            job = InvestigationJob(
                user_id=user_id,
                symbol=symbol,
                market=market,
                drop_pct=float(drop_pct),
                velocity_band=velocity_band,
                heat_breadth=heat_breadth,
                watchlist_count=watchlist_count,
                event_id=event_id,
                user_threshold_pct=user_threshold_pct,
                price=price,
            )
            ok = self.queue.try_put(job)
            if ok:
                self._cooldown[key] = now
            return ok
        except Exception as e:
            logger.error("maybe_enqueue failed: %s", e)
            return False

    def _process_job(self, job: InvestigationJob) -> None:
        base = _base_from_symbol(job.symbol)
        evidence: List[dict] = []

        # 1) Cache lookup multi-CEX delists — include ALL tickers on same notice
        seen_titles = set()
        for row in self.store.find_delists_for_base(base, within_seconds=21 * 86400):
            title = row.get("title") or ""
            exchange = row.get("exchange") or ""
            tkey = f"{exchange}|{title}"
            if tkey in seen_titles:
                continue
            seen_titles.add(tkey)
            w = self.store.get_source_weight(exchange, row["kind"])
            all_bases = self.store.bases_for_delist_title(exchange, title)
            if not all_bases and row.get("base"):
                all_bases = [str(row["base"]).upper()]
            evidence.append(
                {
                    "source": exchange,
                    "kind": row["kind"],
                    "title": title,
                    "url": row.get("url"),
                    "weight": w,
                    "tier": "cex_delist",
                    "ts": row.get("ts"),
                    "base": row.get("base"),
                    "bases": all_bases,
                    "bases_text": ", ".join(all_bases) if all_bases else "",
                }
            )

        # 2) Optional quick rekt pull (soft) — match base in title
        try:
            for item in fetch_rekt_rss()[:25]:
                title = item.get("title") or ""
                hints = extract_symbol_hints(title, {base})
                classified = classify_headline(
                    title, body=item.get("body") or "", source_trust="rekt"
                )
                if not classified:
                    continue
                if base not in hints and base.lower() not in title.lower():
                    continue
                cls, _sev = classified
                w = self.store.get_source_weight("rekt", cls.lower())
                evidence.append(
                    {
                        "source": "rekt",
                        "kind": cls.lower(),
                        "title": title,
                        "url": item.get("url"),
                        "weight": w,
                        "tier": "hack",
                        "ts": item.get("ts"),
                    }
                )
        except Exception as e:
            logger.debug("rekt on-demand failed: %s", e)

        # Sort by learned weight
        evidence.sort(key=lambda e: float(e.get("weight") or 1.0), reverse=True)
        evidence = evidence[:8]

        if evidence:
            # Confidence from best weighted hit
            top_w = float(evidence[0].get("weight") or 1.0)
            has_cex = any(e.get("tier") == "cex_delist" for e in evidence)
            has_hack = any(e.get("tier") == "hack" for e in evidence)
            if has_cex and top_w >= 1.0:
                verdict, conf = "NEWS_RELATED", min(0.95, 0.7 + 0.15 * top_w)
            elif has_cex or has_hack:
                verdict, conf = "LIKELY_NEWS", min(0.85, 0.55 + 0.15 * top_w)
            else:
                verdict, conf = "POSSIBLE_NEWS", 0.4
        else:
            verdict, conf = "NO_NEWS_FOUND", 0.2

        iid = self.store.save_investigation(
            user_id=job.user_id,
            event_id=job.event_id,
            symbol=job.symbol,
            market=job.market,
            drop_pct=job.drop_pct,
            velocity_band=job.velocity_band,
            heat_breadth=job.heat_breadth,
            verdict=verdict,
            confidence=conf,
            evidence=evidence,
        )
        logger.info(
            "investigation id=%s %s:%s verdict=%s conf=%.2f evidence=%s",
            iid,
            job.market,
            job.symbol,
            verdict,
            conf,
            len(evidence),
        )

        if verdict == "NO_NEWS_FOUND" and not self.notify_none:
            return
        if not self.always_report and verdict == "NO_NEWS_FOUND":
            return

        self._notify(job, verdict, conf, evidence, iid)

    def _notify(
        self,
        job: InvestigationJob,
        verdict: str,
        conf: float,
        evidence: List[dict],
        iid: int,
    ) -> None:
        tag = "F" if job.market == "futures" else "S"
        band = job.velocity_band or "—"
        heat = (
            str(job.heat_breadth)
            if job.heat_breadth is not None
            else "?"
        )
        lines = [
            f"🔍 <b>ISOLATED DUMP CHECK</b> [{tag}]",
            f"<b>{_html.escape(job.symbol)}</b>  {job.drop_pct:.1f}% · { _html.escape(band) }",
            f"Heat breadth: { _html.escape(heat) } dumping (isolated filter)",
            f"Verdict: <b>{_html.escape(verdict)}</b> · conf {conf:.0%}",
        ]
        if evidence:
            lines.append("Evidence:")
            for ev in evidence[:5]:
                src = _html.escape(str(ev.get("source")))
                kind = _html.escape(str(ev.get("kind")))
                title = _html.escape(str(ev.get("title") or "")[:160])
                w = float(ev.get("weight") or 1.0)
                bases = ev.get("bases_text") or ev.get("bases") or ""
                if isinstance(bases, list):
                    bases = ", ".join(str(b) for b in bases)
                bases = _html.escape(str(bases)[:200]) if bases else ""
                lines.append(f"  · [{src}/{kind} w={w:.2f}] {title}")
                if bases:
                    lines.append(f"    Tickers: <b>{bases}</b>")
            if verdict in ("NEWS_RELATED", "LIKELY_NEWS"):
                lines.append(
                    "<i>Strategy: likely idiosyncratic (Rule 6). "
                    "Prefer no-trade / micro — not clean market panic AD.</i>"
                )
        else:
            lines.append("No CEX delist / rekt hit in cache for this base.")
            lines.append(
                "<i>No fatal news found — may still be cascade/liquidity. "
                "Not a buy signal.</i>"
            )
        lines.append(f"<i>inv #{iid}" + (f" · event #{job.event_id}" if job.event_id else "") + "</i>")
        msg = "\n".join(lines)
        try:
            self.notifier(job.user_id, msg, parse_mode="HTML")
        except Exception as e:
            logger.error("investigation notify failed: %s", e)

    def _outcome_loop(self) -> None:
        """Link investigation evidence to later price path (cause → effect learning)."""
        assert self._outcome_thread_stop is not None
        while not self._outcome_thread_stop.is_set():
            try:
                self._score_pending_outcomes()
            except Exception as e:
                logger.debug("outcome learn: %s", e)
            for _ in range(30):
                if self._outcome_thread_stop.is_set():
                    break
                time.sleep(1.0)

    def _score_pending_outcomes(self) -> None:
        pending = self.store.pending_outcome_links(
            horizon_seconds=self.learning_outcome_horizon, limit=40
        )
        if not pending or not self.get_price:
            return
        for inv in pending:
            market = inv.get("market") or "futures"
            symbol = inv.get("symbol") or ""
            try:
                px = self.get_price(market, symbol)
            except Exception:
                px = None
            # Without full path history, use simple proxy from fire price in evidence path
            # Prefer learning_outcomes table if event_id set — read via optional callback later
            max_bounce = None
            max_dd = None
            # Soft: if we have price now and drop_pct at fire, approximate
            fire_drop = inv.get("drop_pct")
            if px and inv.get("drop_pct") is not None:
                # Without fire price stored on inv, use bounce proxy 0
                max_bounce = 0.0
                max_dd = 0.0
            evidence = inv.get("evidence") or []
            self.store.record_investigation_outcome(
                int(inv["id"]),
                event_id=inv.get("event_id"),
                horizon_seconds=self.learning_outcome_horizon,
                max_bounce_pct=max_bounce,
                max_dd_pct=max_dd,
                verdict=str(inv.get("verdict") or ""),
                evidence=evidence,
            )
