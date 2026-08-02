"""Auto engagement bridge: fire → took|skip|partial|late from journal/fills.

Pure inference helpers + EngagementBridge poller. Never touches alerts table.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .store import EventStore

logger = logging.getLogger(__name__)

# Owner default: 1 hour grace before auto skip / final inference
DEFAULT_GRACE_SECONDS = 3600
DEFAULT_MAX_PENDING = 2
# Fills/journal after grace but still "late" window
LATE_WINDOW_SECONDS = 6 * 3600

ACTION_TOOK = "took"
ACTION_SKIP = "skip"
ACTION_PARTIAL = "partial"
ACTION_LATE = "late"


def _norm_sym(symbol: str) -> str:
    return (
        (symbol or "")
        .upper()
        .replace("_", "")
        .replace("STOCK", "")
        .replace("-", "")
        .strip()
    )


def symbols_match(a: str, b: str) -> bool:
    na, nb = _norm_sym(a), _norm_sym(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    # compact vs base: BTCUSDT vs BTC
    if na.endswith("USDT") and na[:-4] == nb:
        return True
    if nb.endswith("USDT") and nb[:-4] == na:
        return True
    return na in nb or nb in na


def infer_engagement(
    event: Dict[str, Any],
    *,
    journal_opens: Sequence[Dict[str, Any]],
    fills: Sequence[Dict[str, Any]],
    now: float,
    grace_seconds: float = DEFAULT_GRACE_SECONDS,
) -> Dict[str, Any]:
    """Infer engagement for one unlabeled event.

    Returns dict:
      action: took|skip|partial|late|None
      confidence: 0..1
      source: auto_journal|auto_fill|auto_skip|unknown
      reason: str
      needs_question: bool
      question: optional str
    """
    ets = float(event.get("ts") or 0)
    age = now - ets
    sym = event.get("symbol") or ""
    market = (event.get("market") or "").lower()
    fire_price = event.get("price")
    try:
        fire_price_f = float(fire_price) if fire_price is not None else None
    except (TypeError, ValueError):
        fire_price_f = None

    # Evidence windows
    window_end = ets + grace_seconds
    late_end = ets + grace_seconds + LATE_WINDOW_SECONDS

    journal_hits: List[Tuple[float, dict]] = []
    for t in journal_opens:
        if not symbols_match(sym, t.get("symbol") or ""):
            continue
        tm = (t.get("market") or "").lower()
        if market and tm and tm != market and tm not in ("", market):
            # allow match if market missing on one side
            if tm and market and tm != market:
                continue
        oa = t.get("opened_at")
        try:
            oa_f = float(oa) if oa is not None else None
        except (TypeError, ValueError):
            oa_f = None
        if oa_f is None:
            continue
        if ets - 60 <= oa_f <= late_end:  # slight pre-fire slack
            journal_hits.append((oa_f, t))

    fill_hits: List[Tuple[float, dict]] = []
    for f in fills:
        if not symbols_match(sym, f.get("symbol") or ""):
            continue
        side = (f.get("side") or "").lower()
        if side and side not in ("buy", "long", "bid"):
            continue
        try:
            fts = float(f.get("ts") or 0)
        except (TypeError, ValueError):
            continue
        if ets - 60 <= fts <= late_end:
            fill_hits.append((fts, f))

    hits_in_grace = [(ts, src, row) for ts, row in journal_hits if ts <= window_end for src in ("auto_journal",)]
    hits_in_grace += [(ts, "auto_fill", row) for ts, row in fill_hits if ts <= window_end]
    hits_late = [(ts, "auto_journal", row) for ts, row in journal_hits if ts > window_end]
    hits_late += [(ts, "auto_fill", row) for ts, row in fill_hits if ts > window_end]

    # Conflict: both none and... only real conflict is ambiguous multi-side; keep simple
    if hits_in_grace:
        # partial heuristic: very small qty vs price move — default took
        best = min(hits_in_grace, key=lambda x: x[0])
        ts, source, row = best
        action = ACTION_TOOK
        conf = 0.92 if source == "auto_fill" else 0.85
        # late-ish inside grace but deep extension: still took
        if fire_price_f and row.get("entry_avg") is not None:
            try:
                entry = float(row["entry_avg"])
                # if entry much higher than fire (chased bounce) → late/fomo candidate
                if entry > fire_price_f * 1.03 and (ts - ets) > grace_seconds * 0.5:
                    action = ACTION_LATE
                    conf = 0.55
                    return {
                        "action": action,
                        "confidence": conf,
                        "source": source,
                        "reason": "entry after bounce / mid-window chase",
                        "needs_question": True,
                        "question": (
                            f"#{event.get('id')} {sym}: position after bounce — "
                            "planned deeper layer or FOMO?"
                        ),
                    }
            except (TypeError, ValueError):
                pass
        qty = row.get("qty")
        if qty is not None:
            try:
                if float(qty) > 0 and float(qty) < 1e-8:
                    action = ACTION_PARTIAL
                    conf = 0.7
            except (TypeError, ValueError):
                pass
        return {
            "action": action,
            "confidence": conf,
            "source": source,
            "reason": f"matched {source} within grace",
            "needs_question": conf < 0.7,
            "question": None,
        }

    if hits_late:
        best = min(hits_late, key=lambda x: x[0])
        return {
            "action": ACTION_LATE,
            "confidence": 0.6,
            "source": best[1],
            "reason": "engagement after grace window",
            "needs_question": True,
            "question": (
                f"#{event.get('id')} {sym}: took after 1h grace — "
                "late add / FOMO / different thesis?"
            ),
        }

    # No evidence
    if age < grace_seconds:
        return {
            "action": None,
            "confidence": 0.0,
            "source": "pending_grace",
            "reason": "still within grace window",
            "needs_question": False,
            "question": None,
        }

    # Past grace, flat → skip (high conf)
    return {
        "action": ACTION_SKIP,
        "confidence": 0.9,
        "source": "auto_skip",
        "reason": "no journal/fill within grace",
        "needs_question": False,
        "question": None,
    }


class EngagementBridge:
    """Background poller: label events after grace; queue pending questions (max 2)."""

    def __init__(
        self,
        event_store: EventStore,
        *,
        grace_seconds: float = DEFAULT_GRACE_SECONDS,
        max_pending: int = DEFAULT_MAX_PENDING,
        poll_seconds: float = 60.0,
        conf_auto_threshold: float = 0.75,
        user_ids: Optional[Sequence[int]] = None,
    ):
        self.event_store = event_store
        self.grace_seconds = float(grace_seconds)
        self.max_pending = int(max_pending)
        self.poll_seconds = max(15.0, float(poll_seconds))
        self.conf_auto_threshold = float(conf_auto_threshold)
        self.user_ids = list(user_ids) if user_ids else None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._labeled = 0
        self._queued = 0
        self._last_cycle_ms = 0

    def get_health(self) -> dict:
        return {
            "running": self._thread is not None and self._thread.is_alive(),
            "labeled": self._labeled,
            "queued": self._queued,
            "last_cycle_ms": self._last_cycle_ms,
            "grace_seconds": self.grace_seconds,
            "max_pending": self.max_pending,
        }

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self.run, name="engagement-bridge", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)

    def run(self) -> None:
        logger.info(
            "Engagement bridge started grace=%ss max_pending=%s",
            self.grace_seconds,
            self.max_pending,
        )
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception as e:
                logger.exception("Engagement bridge error: %s", e)
            slept = 0.0
            while slept < self.poll_seconds and not self._stop.is_set():
                time.sleep(min(1.0, self.poll_seconds - slept))
                slept += 1.0

    def run_once(self, *, now: Optional[float] = None) -> Dict[str, int]:
        """Process unlabeled events. Returns counts for tests."""
        t0 = time.perf_counter()
        wall = float(now if now is not None else time.time())
        labeled = 0
        queued = 0
        skipped_queue = 0

        events = self.event_store.unlabeled_events_for_bridge(
            older_than_ts=None,  # include in-grace (no skip yet)
            limit=80,
            user_ids=self.user_ids,
        )
        # Group journal/fills per user
        by_user: Dict[int, List[dict]] = {}
        for e in events:
            by_user.setdefault(int(e["user_id"]), []).append(e)

        for uid, evs in by_user.items():
            journal = self.event_store.journal_list(uid, open_only=False)
            # include recently closed for took detection
            fills = self.event_store.recent_fills(uid, limit=100)
            for e in evs:
                if e.get("last_action"):
                    continue
                inf = infer_engagement(
                    e,
                    journal_opens=journal,
                    fills=fills,
                    now=wall,
                    grace_seconds=self.grace_seconds,
                )
                action = inf.get("action")
                conf = float(inf.get("confidence") or 0)
                if action and conf >= self.conf_auto_threshold and not inf.get("needs_question"):
                    ok = self.event_store.label_event(
                        int(e["id"]),
                        uid,
                        action=action,
                        notes=f"[{inf.get('source')}] {inf.get('reason')}",
                        source=str(inf.get("source") or "auto"),
                        confidence=conf,
                    )
                    if ok:
                        labeled += 1
                        self._labeled += 1
                    continue
                if action and conf >= self.conf_auto_threshold and action == ACTION_SKIP:
                    ok = self.event_store.label_event(
                        int(e["id"]),
                        uid,
                        action=ACTION_SKIP,
                        notes=f"[{inf.get('source')}] {inf.get('reason')}",
                        source=str(inf.get("source") or "auto_skip"),
                        confidence=conf,
                    )
                    if ok:
                        labeled += 1
                        self._labeled += 1
                    continue
                # Auto-label late/partial at medium conf without question if conf high enough
                if action in (ACTION_TOOK, ACTION_PARTIAL) and conf >= self.conf_auto_threshold:
                    ok = self.event_store.label_event(
                        int(e["id"]),
                        uid,
                        action=action,
                        notes=f"[{inf.get('source')}] {inf.get('reason')}",
                        source=str(inf.get("source") or "auto"),
                        confidence=conf,
                    )
                    if ok:
                        labeled += 1
                        self._labeled += 1
                    continue
                if inf.get("needs_question") and inf.get("question"):
                    qid = self.event_store.enqueue_pending_question(
                        uid,
                        event_id=int(e["id"]),
                        symbol=str(e.get("symbol") or ""),
                        question=str(inf["question"]),
                        kind="engagement",
                        max_open=self.max_pending,
                        payload={"inference": inf},
                    )
                    if qid:
                        queued += 1
                        self._queued += 1
                    else:
                        skipped_queue += 1
                    # Still write tentative late label if we have action
                    if action in (ACTION_LATE, ACTION_PARTIAL) and conf >= 0.5:
                        self.event_store.label_event(
                            int(e["id"]),
                            uid,
                            action=action,
                            notes=f"[tentative] {inf.get('reason')}",
                            source=str(inf.get("source") or "auto"),
                            confidence=conf,
                        )
                        labeled += 1

        self._last_cycle_ms = int((time.perf_counter() - t0) * 1000)
        return {"labeled": labeled, "queued": queued, "skipped_queue": skipped_queue}
