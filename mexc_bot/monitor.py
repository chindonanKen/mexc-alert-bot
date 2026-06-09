"""Background price monitoring engine.

Uses batch price fetching for speed, reliability and timely alerts.
One HTTP request per cycle gets prices for all symbols → instant lookups.
"""

import logging
import threading
import time
from typing import Callable, Dict

from .config import Settings
from .exchange import MexcClient
from .storage import AlertStore

logger = logging.getLogger(__name__)


class PriceMonitor:
    """
    Efficient background monitor for one-shot price alerts.

    Design goals (per user request):
    - Set alert → it fires ONCE when price is hit (within tolerance band) → delete itself.
    - No direction logic for the basic system.
    - Clean, fast, timely: batch API calls + tight loop + good resilience.
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
        self._last_poll_ms: int = 0
        self._last_success: float = 0.0  # monotonic time of last good batch

    def get_health(self) -> Dict[str, float | int | bool]:
        """Lightweight health info for /status."""
        import time as _t
        now = _t.monotonic()
        return {
            "last_poll_ms": self._last_poll_ms,
            "seconds_since_last_success": (now - self._last_success) if self._last_success else 999,
            "running": not self._stop_event.is_set(),
        }

    def _check_once(self) -> None:
        """Single efficient pass using one batch price fetch."""
        import time as _t
        start = _t.perf_counter()

        prices: Dict[str, float] = self.client.get_all_prices()
        if not prices:
            logger.warning("Price batch fetch returned no data this cycle — skipping checks")
            return

        self._last_success = _t.monotonic()

        checked = 0
        fired = 0
        user_ids = self.store.get_all_user_ids()

        for user_id in user_ids:
            alerts = self.store.get_user_alerts(user_id)
            for a in alerts:
                if not a.get("enabled"):
                    continue

                symbol = str(a["symbol"]).upper()
                target = float(a["price"])
                current = prices.get(symbol)

                if current is None:
                    # Symbol might be futures-only or not in this response
                    continue

                checked += 1
                tolerance = self.settings.alert_tolerance_percent
                diff_ratio = abs(current - target) / target if target != 0 else float("inf")

                if diff_ratio <= tolerance:
                    # Keep it extremely minimal and scannable — just symbol + price, loud and clear
                    msg = f"🚨 *{symbol}*\n`${current:.8f}`"
                    try:
                        self.notifier(user_id, msg, parse_mode="Markdown")
                        logger.info(f"Alert #{a['id']} FIRED user={user_id} {symbol} target={target} current={current}")
                        fired += 1
                    except Exception as e:
                        logger.error(f"Failed sending alert #{a['id']}: {e}")
                        # Do not remove if we couldn't notify — will retry next cycle
                        continue

                    # One-shot: remove only after successful notification
                    self.store.remove_alert(user_id, a["id"])

        elapsed_ms = int((_t.perf_counter() - start) * 1000)
        self._last_poll_ms = elapsed_ms
        if checked or fired:
            logger.info(f"Monitor cycle: {checked} alerts checked, {fired} fired, batch in {elapsed_ms}ms")

    def run(self) -> None:
        """Main loop. Fast and resilient."""
        logger.info("Price monitor started (batch mode)")
        while not self._stop_event.is_set():
            try:
                self._check_once()
            except Exception as e:
                logger.exception(f"Unexpected error in monitor loop: {e}")
            # Responsive sleep
            slept = 0.0
            interval = max(0.5, float(self.settings.price_poll_interval_seconds))
            while slept < interval and not self._stop_event.is_set():
                time.sleep(min(0.15, interval - slept))
                slept += 0.15
        logger.info("Price monitor stopped")

    def start(self) -> threading.Thread:
        if self._thread and self._thread.is_alive():
            return self._thread
        self._stop_event.clear()
        self._thread = threading.Thread(target=self.run, name="price-monitor", daemon=True)
        self._thread.start()
        return self._thread

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=6)
