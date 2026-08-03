"""Background outcome poller: bounce / drawdown after learning events.

Never touches the alerts table. Soft-fails on price errors.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Dict, Optional, Sequence, Tuple

from .store import EventStore

logger = logging.getLogger(__name__)

# (market, symbol) -> last known price for max tracking between polls
PriceKey = Tuple[str, str]


class OutcomePoller:
    """
    For each pending (event, horizon), once age >= horizon, record
    max bounce % and max drawdown % vs fire price using intervening price samples.
    """

    def __init__(
        self,
        event_store: EventStore,
        get_price: Callable[[str, str], Optional[float]],
        horizons_seconds: Sequence[int],
        poll_seconds: float = 60.0,
    ):
        self.event_store = event_store
        self.get_price = get_price
        self.horizons = [int(h) for h in horizons_seconds if int(h) > 0]
        self.poll_seconds = max(15.0, float(poll_seconds))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # Track extremes from first sighting until horizon closes
        # event_id -> (fire_price, max_high, min_low)
        self._extremes: Dict[int, Tuple[float, float, float]] = {}
        self._last_cycle_ms = 0
        self._outcomes_written = 0

    def get_health(self) -> dict:
        return {
            "running": self._thread is not None and self._thread.is_alive(),
            "last_cycle_ms": self._last_cycle_ms,
            "outcomes_written": self._outcomes_written,
            "tracking": len(self._extremes),
            "horizons": list(self.horizons),
        }

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self.run, name="outcome-poller", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)

    def run(self) -> None:
        logger.info(
            "Outcome poller started horizons=%s poll=%ss",
            self.horizons,
            self.poll_seconds,
        )
        while not self._stop.is_set():
            try:
                self._check_once()
            except Exception as e:
                logger.exception("Outcome poller error: %s", e)
            slept = 0.0
            while slept < self.poll_seconds and not self._stop.is_set():
                time.sleep(min(1.0, self.poll_seconds - slept))
                slept += 1.0

    def _check_once(self) -> None:
        t0 = time.perf_counter()
        now = time.time()
        pending = self.event_store.pending_outcomes(self.horizons, now=now)
        # Also track prices for events that will need outcomes soon
        # Group by event for extreme updates
        by_event: Dict[int, list] = {}
        for p in pending:
            by_event.setdefault(int(p["event_id"]), []).append(p)

        # Update extremes for any event we care about
        seen_keys: Dict[PriceKey, Optional[float]] = {}
        for eid, items in by_event.items():
            sample = items[0]
            market = sample["market"]
            symbol = sample["symbol"]
            fire_px = float(sample["price"])
            key: PriceKey = (market, symbol)
            if key not in seen_keys:
                try:
                    seen_keys[key] = self.get_price(market, symbol)
                except Exception:
                    seen_keys[key] = None
            px = seen_keys[key]
            if px is None or px <= 0:
                continue
            if eid not in self._extremes:
                self._extremes[eid] = (fire_px, px, px)
            else:
                fp, hi, lo = self._extremes[eid]
                self._extremes[eid] = (fp, max(hi, px), min(lo, px))

            # Write completed horizons
            for item in items:
                h = int(item["horizon_seconds"])
                fp, hi, lo = self._extremes[eid]
                bounce = ((hi - fp) / fp) * 100.0 if fp > 0 else None
                dd = ((lo - fp) / fp) * 100.0 if fp > 0 else None  # negative if lower
                self.event_store.record_outcome(
                    eid,
                    h,
                    max_bounce_pct=bounce,
                    max_dd_pct=dd,
                    last_price=px,
                )
                self._outcomes_written += 1
                # Super-agent: update beliefs from path quality (setup edge)
                try:
                    from .beliefs import BeliefEngine

                    uid = int(sample.get("user_id") or item.get("user_id") or 0)
                    if uid:
                        BeliefEngine(self.event_store).update_from_outcome(
                            uid,
                            eid,
                            max_bounce_pct=bounce,
                            max_dd_pct=dd,
                            horizon_seconds=h,
                        )
                except Exception as be:
                    logger.debug("belief update skipped: %s", be)
                logger.info(
                    "learning.outcome event=%s h=%ss bounce=%.2f%% dd=%.2f%% last=%s",
                    eid,
                    h,
                    bounce or 0.0,
                    dd or 0.0,
                    px,
                )

            # If all horizons for this event are done, drop extremes
            still = self.event_store.pending_outcomes(self.horizons, now=now, limit=500)
            still_ids = {int(x["event_id"]) for x in still}
            if eid not in still_ids:
                self._extremes.pop(eid, None)

        self._last_cycle_ms = int((time.perf_counter() - t0) * 1000)
