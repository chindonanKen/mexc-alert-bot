"""Always-on decision loop: live feed → engine.on_print → Machine log / fills.

Runs while uvicorn is up. live_orders_allowed stays false — simulated fills only.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from .engine import Engine
from .feeds import DEFAULT_LIVE_NAMES, MexcLiveFeed, Print

log = logging.getLogger("machine.loop")


@dataclass
class DecisionLoop:
    engine: Engine
    feed: MexcLiveFeed
    interval_sec: float = 10.0
    running: bool = False
    last_error: str | None = None
    polls: int = 0
    prints_seen: int = 0
    _stop: asyncio.Event = field(default_factory=asyncio.Event)

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "interval_sec": self.interval_sec,
            "polls": self.polls,
            "prints_seen": self.prints_seen,
            "feed_names": list(self.feed.names),
            "last_error": self.last_error,
            "live_orders_allowed": False,
        }

    def step_once(self) -> list[dict[str, Any]]:
        """One poll → evaluate. Sync helper for tests / scripts."""
        results: list[dict[str, Any]] = []
        try:
            prints = self.feed.poll_once()
        except Exception as e:  # noqa: BLE001 — keep loop alive
            self.last_error = str(e)
            log.warning("feed poll failed: %s", e)
            return results
        self.polls += 1
        self.last_error = None
        for pr in prints:
            self.prints_seen += 1
            results.append(self.engine.on_print(pr))
        return results

    async def run_forever(self) -> None:
        """Background task body. Never places live exchange orders."""
        self.running = True
        self._stop.clear()
        log.info(
            "decision loop on; names=%s interval=%ss live_orders_allowed=false",
            list(self.feed.names),
            self.interval_sec,
        )
        try:
            while not self._stop.is_set():
                await asyncio.to_thread(self.step_once)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.interval_sec)
                except asyncio.TimeoutError:
                    pass
        finally:
            self.running = False
            log.info("decision loop stopped")


def feed_names_from_engine(engine: Engine) -> list[str]:
    """Prefer hung plan names; fall back to SYN/AGI/US."""
    names = [p.name for p in engine.plans.values()]
    # Keep stable order; ensure defaults present when hung
    ordered: list[str] = []
    for n in list(DEFAULT_LIVE_NAMES) + names:
        if n not in ordered:
            ordered.append(n)
    # If we have hung plans, only poll those that look like MEXC symbols
    hung = [p.name for p in engine.plans.values() if p.name.endswith("USDT")]
    return hung if hung else list(DEFAULT_LIVE_NAMES)


def sync_feed_names(loop: DecisionLoop | None, engine: Engine) -> list[str]:
    """Refresh live feed names from hung plans. Always a mutable list."""
    names = list(feed_names_from_engine(engine))
    if loop is not None:
        loop.feed.names = names
    return names


def build_default_loop(engine: Engine, interval_sec: float = 10.0) -> DecisionLoop:
    names = list(feed_names_from_engine(engine))
    feed = MexcLiveFeed(names=names)
    return DecisionLoop(engine=engine, feed=feed, interval_sec=interval_sec)
