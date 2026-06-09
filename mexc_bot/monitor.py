"""Background price monitoring engine."""

import logging
import threading
import time
from typing import Callable

from .config import Settings
from .exchange import MexcClient
from .storage import AlertStore

logger = logging.getLogger(__name__)


class PriceMonitor:
    """
    Continuously polls prices for active alerts and triggers notifications
    when price enters the tolerance band around the target.

    The alert is removed after it fires (one-shot behavior, same as original).
    """

    def __init__(
        self,
        settings: Settings,
        store: AlertStore,
        client: MexcClient,
        notifier: Callable[[int, str], None],  # user_id, message
    ):
        self.settings = settings
        self.store = store
        self.client = client
        self.notifier = notifier
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def _check_once(self) -> None:
        """Single pass over all alerts."""
        user_ids = self.store.get_all_user_ids()
        for user_id in user_ids:
            alerts = self.store.get_user_alerts(user_id)
            for a in alerts:
                if not a.get("enabled"):
                    continue

                symbol = a["symbol"]
                target = float(a["price"])

                current = self.client.get_price(symbol)
                if current is None:
                    continue

                tolerance = self.settings.alert_tolerance_percent
                diff_ratio = abs(current - target) / target if target != 0 else float("inf")

                if diff_ratio <= tolerance:
                    msg = (
                        "🚨 ALERT TRIGGERED!\n\n"
                        f"{symbol} hit target ${target}\n"
                        f"Current: ${current:.8f}\n"
                        f"Diff: {diff_ratio*100:.4f}%"
                    )
                    try:
                        self.notifier(user_id, msg)
                        logger.info(f"Alert #{a['id']} fired for user {user_id}: {symbol} @ {target}")
                    except Exception as e:
                        logger.error(f"Failed to send alert notification: {e}")

                    # One-shot: remove after firing (preserves original behavior)
                    self.store.remove_alert(user_id, a["id"])

    def run(self) -> None:
        """Main loop. Runs until stop() is called."""
        logger.info("Price monitor started")
        while not self._stop_event.is_set():
            try:
                self._check_once()
            except Exception as e:
                logger.exception(f"Unexpected error in monitor loop: {e}")
            # Sleep in small increments so we can respond to stop quickly
            slept = 0.0
            interval = self.settings.price_poll_interval_seconds
            while slept < interval and not self._stop_event.is_set():
                time.sleep(min(0.2, interval - slept))
                slept += 0.2
        logger.info("Price monitor stopped")

    def start(self) -> threading.Thread:
        """Start monitor in a daemon thread. Returns the thread."""
        if self._thread and self._thread.is_alive():
            return self._thread
        self._stop_event.clear()
        self._thread = threading.Thread(target=self.run, name="price-monitor", daemon=True)
        self._thread.start()
        return self._thread

    def stop(self) -> None:
        """Signal the monitor loop to exit."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
