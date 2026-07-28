"""Non-blocking investigation job queue.

Fire path only puts jobs; worker thread processes them.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class InvestigationJob:
    user_id: int
    symbol: str
    market: str
    drop_pct: float
    velocity_band: Optional[str] = None
    heat_breadth: Optional[int] = None
    watchlist_count: Optional[int] = None
    event_id: Optional[int] = None
    user_threshold_pct: float = 5.0
    price: Optional[float] = None
    enqueued_at: float = field(default_factory=time.time)
    meta: Dict[str, Any] = field(default_factory=dict)


class InvestigationQueue:
    def __init__(self, maxsize: int = 200):
        self._q: queue.Queue = queue.Queue(maxsize=maxsize)
        self._dropped = 0
        self._enqueued = 0

    def try_put(self, job: InvestigationJob) -> bool:
        """Never blocks the caller. Drops if full."""
        try:
            self._q.put_nowait(job)
            self._enqueued += 1
            return True
        except queue.Full:
            self._dropped += 1
            logger.warning(
                "investigation queue full — dropped job %s (dropped=%s)",
                job.symbol,
                self._dropped,
            )
            return False

    def get(self, timeout: float = 1.0) -> Optional[InvestigationJob]:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def task_done(self) -> None:
        try:
            self._q.task_done()
        except Exception:
            pass

    def stats(self) -> dict:
        return {
            "qsize": self._q.qsize(),
            "enqueued": self._enqueued,
            "dropped": self._dropped,
        }


class InvestigationWorker:
    """Single worker thread — soft-fail all processing."""

    def __init__(
        self,
        q: InvestigationQueue,
        process_fn: Callable[[InvestigationJob], None],
        *,
        name: str = "isolated-dump-worker",
    ):
        self.q = q
        self.process_fn = process_fn
        self.name = name
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._processed = 0
        self._errors = 0

    def get_health(self) -> dict:
        return {
            "running": self._thread is not None and self._thread.is_alive(),
            "processed": self._processed,
            "errors": self._errors,
            **self.q.stats(),
        }

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self.run, name=self.name, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)

    def run(self) -> None:
        logger.info("%s started", self.name)
        while not self._stop.is_set():
            job = self.q.get(timeout=0.5)
            if job is None:
                continue
            try:
                self.process_fn(job)
                self._processed += 1
            except Exception as e:
                self._errors += 1
                logger.exception("investigation job failed %s: %s", job.symbol, e)
            finally:
                self.q.task_done()
