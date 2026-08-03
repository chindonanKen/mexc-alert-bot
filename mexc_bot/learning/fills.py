"""Poll MEXC private myTrades → journal_fills (read-only). Soft-fail."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, List, Optional, Set

from ..exchange_private import MexcPrivateSpotClient, trade_to_fill_row
from .store import EventStore

logger = logging.getLogger(__name__)


class FillSyncPoller:
    def __init__(
        self,
        event_store: EventStore,
        private_client: MexcPrivateSpotClient,
        user_id: int,
        get_symbols: Callable[[], Set[str]],
        *,
        poll_seconds: float = 120.0,
        notifier: Optional[Callable[..., None]] = None,
        notify_on_new: bool = False,
    ):
        self.event_store = event_store
        self.client = private_client
        self.user_id = int(user_id)
        self.get_symbols = get_symbols
        self.poll_seconds = max(30.0, float(poll_seconds))
        self.notifier = notifier
        self.notify_on_new = notify_on_new
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._fills_new = 0
        self._last_cycle_ms = 0

    def get_health(self) -> dict:
        return {
            "running": self._thread is not None and self._thread.is_alive(),
            "fills_new": self._fills_new,
            "last_cycle_ms": self._last_cycle_ms,
            "user_id": self.user_id,
        }

    def start(self) -> None:
        if not self.client.configured or self.user_id <= 0:
            logger.warning("FillSyncPoller not started (missing keys or user_id)")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self.run, name="fill-sync", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)

    def run(self) -> None:
        logger.info(
            "Fill sync started user=%s poll=%ss", self.user_id, self.poll_seconds
        )
        while not self._stop.is_set():
            try:
                self._check_once()
            except Exception as e:
                logger.exception("Fill sync error: %s", e)
            slept = 0.0
            while slept < self.poll_seconds and not self._stop.is_set():
                time.sleep(min(1.0, self.poll_seconds - slept))
                slept += 1.0

    def _check_once(self) -> None:
        t0 = time.perf_counter()
        try:
            symbols = set(self.get_symbols() or set())
        except Exception:
            symbols = set()
        # Always try common majors if empty (still read-only)
        if not symbols:
            symbols = {"BTCUSDT", "ETHUSDT"}
        new_rows: List[dict] = []
        for sym in list(symbols)[:40]:
            # Pull max history available per request (MEXC cap 100)
            trades = self.client.get_my_trades(sym, limit=100)
            for tr in trades:
                row = trade_to_fill_row(tr, self.user_id)
                if not row:
                    continue
                inserted = self.event_store.insert_fill(**row)
                if inserted:
                    new_rows.append(row)
                    self._fills_new += 1
                    # Keep journal trade open/close heuristic
                    try:
                        self.event_store.upsert_journal_from_fill(row)
                    except Exception as e:
                        logger.debug("journal upsert from fill: %s", e)

        if new_rows and self.notify_on_new and self.notifier:
            try:
                lines = [f"MEXC fills synced: {len(new_rows)} new"]
                for r in new_rows[:5]:
                    lines.append(
                        f"  {r['side'].upper()} {r['symbol']} qty={r['qty']} @ {r['price']}"
                    )
                self.notifier(self.user_id, "\n".join(lines), parse_mode=None)
            except Exception as e:
                logger.warning("fill notify failed: %s", e)

        self._last_cycle_ms = int((time.perf_counter() - t0) * 1000)
