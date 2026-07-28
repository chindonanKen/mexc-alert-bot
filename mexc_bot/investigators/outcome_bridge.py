"""Bridge learning_outcomes → investigator source expertise."""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from ..learning.store import EventStore
from .store import InvestigatorStore

logger = logging.getLogger(__name__)


class InvestigationOutcomeBridge:
    """
    Periodically: for investigations with event_id, if learning_outcomes
    has a row for horizon, feed bounce/dd into source_expertise.
    """

    def __init__(
        self,
        inv_store: InvestigatorStore,
        event_store: EventStore,
        *,
        horizon_seconds: int = 14400,
        poll_seconds: float = 120.0,
    ):
        self.inv_store = inv_store
        self.event_store = event_store
        self.horizon = int(horizon_seconds)
        self.poll_seconds = max(30.0, float(poll_seconds))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self.run, name="inv-outcome-bridge", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)

    def run(self) -> None:
        logger.info("Investigation outcome bridge started h=%ss", self.horizon)
        while not self._stop.is_set():
            try:
                self._once()
            except Exception as e:
                logger.debug("outcome bridge: %s", e)
            slept = 0.0
            while slept < self.poll_seconds and not self._stop.is_set():
                time.sleep(min(1.0, self.poll_seconds - slept))
                slept += 1.0

    def _once(self) -> None:
        pending = self.inv_store.pending_outcome_links(
            horizon_seconds=self.horizon, limit=50
        )
        for inv in pending:
            eid = inv.get("event_id")
            if not eid:
                continue
            # Pull outcome from learning store
            bounce = dd = None
            try:
                with self.event_store._lock:
                    row = self.event_store._get_conn().execute(
                        """
                        SELECT max_bounce_pct, max_dd_pct FROM learning_outcomes
                        WHERE event_id = ? AND horizon_seconds = ?
                        """,
                        (int(eid), self.horizon),
                    ).fetchone()
                    if row:
                        bounce = row["max_bounce_pct"]
                        dd = row["max_dd_pct"]
            except Exception:
                continue
            if bounce is None and dd is None:
                continue
            self.inv_store.record_investigation_outcome(
                int(inv["id"]),
                event_id=int(eid),
                horizon_seconds=self.horizon,
                max_bounce_pct=float(bounce) if bounce is not None else None,
                max_dd_pct=float(dd) if dd is not None else None,
                verdict=str(inv.get("verdict") or ""),
                evidence=inv.get("evidence") or [],
            )
            logger.info(
                "source learning inv=%s event=%s bounce=%s dd=%s",
                inv["id"],
                eid,
                bounce,
                dd,
            )
