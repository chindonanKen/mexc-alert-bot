"""Background price monitoring engine.

Uses batch price fetching for speed, reliability and timely alerts.
One HTTP request per cycle gets prices for all symbols → instant lookups.

V3: optional futures PriceProvider. Spot path is unchanged when market='spot'.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Dict, Optional

from .config import Settings
from .exchange import PriceProvider
from .heartbeat import touch_heartbeat
from .storage import AlertStore

logger = logging.getLogger(__name__)


class PriceMonitor:
    """
    Efficient background monitor for one-shot price alerts.

    It receives a PriceProvider (anything that can give current prices).
    This decouples the alarm logic from the data source (REST, WebSocket, etc.).

    Design goals (per user request):
    - Set alert → it fires ONCE when the price crosses the target level (either direction since last check) or lands within the tolerance band → delete itself.
    - No direction logic in commands.
    - Clean, fast, timely: cheap price updates + tight loop + good resilience.

    V3: futures_provider is optional. When None, only spot alerts are evaluated
    (futures rows are skipped safely). Crossing/band/remove logic is identical for both markets.
    """

    def __init__(
        self,
        settings: Settings,
        store: AlertStore,
        price_provider: PriceProvider,
        notifier: Callable[..., None],  # user_id, message, optional parse_mode
        futures_provider: Optional[PriceProvider] = None,
        event_store=None,
        target_fire_log=None,
    ):
        self.settings = settings
        self.store = store
        self.price_provider = price_provider
        self.futures_provider = futures_provider
        self.notifier = notifier
        # Optional learning EventStore — log target fires only; never delete via learning path
        self.event_store = event_store
        # Durable hit log for daily report (works even when learning is off)
        self.target_fire_log = target_fire_log
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_poll_ms: int = 0
        self._last_success: float = 0.0  # monotonic time of last good batch
        self._last_prices: dict[tuple[int, int], float] = {}  # (user_id, stable_id) -> last seen price for crossing detection.
        # Keyed by *stable* DB PK (not visual rank) because visual ranks (1-based ORDER BY) shift on any remove
        # (fires or user /r /clear). Using visual would cause last_prices to be looked up under wrong prev for
        # shifted alerts, leading to false "crossed" detections (or lost history).

    def get_health(self) -> Dict[str, float | int | bool]:
        """Lightweight health info for /status. Includes last_prices count for diagnosing crossing state / leaks."""
        import time as _t
        now = _t.monotonic()
        return {
            "last_poll_ms": self._last_poll_ms,
            "seconds_since_last_success": (now - self._last_success) if self._last_success else 999,
            "running": not self._stop_event.is_set(),
            "tracked_last_prices": len(self._last_prices),
            "futures_provider": self.futures_provider is not None,
        }

    def get_user_debug_info(self, user_id: int) -> dict:
        """Returns debug snapshot for a user: current visuals+stables from store, and any _last_prices tracked for them (by stable).
        Use from bot /diag or in shell to inspect rank vs history keying after removes. Keyed-by-stable is the invariant fix.
        """
        try:
            alerts = self.store.get_user_alerts(user_id)
            visual_map = [
                {
                    "visual": a["id"],
                    "stable": a["stable_id"],
                    "symbol": a["symbol"],
                    "target": a["price"],
                    "enabled": a["enabled"],
                    "market": a.get("market", "spot"),
                }
                for a in alerts
            ]
            tracked = {k[1]: v for k, v in self._last_prices.items() if k[0] == user_id}
            return {
                "num_alerts": len(alerts),
                "visuals_and_stables": visual_map,
                "last_prices_by_stable": tracked,
                "note": "If last_prices keys were visuals, shifts from remove would misalign prevs causing spurious mass fires. Now stable PKs.",
            }
        except Exception as e:
            return {"error": str(e)}

    def _check_once(self) -> None:
        """Single efficient pass using batch price fetch (spot + optional futures)."""
        import time as _t
        start = _t.perf_counter()

        spot_prices: Dict[str, float] = self.price_provider.get_all_prices()
        futures_prices: Dict[str, float] = {}
        if self.futures_provider is not None:
            # Only hit futures API if feature is on *and* there is at least one futures alert
            # (or always when provider attached — provider is only attached when flag is on).
            try:
                if self.store.has_any_futures_alerts():
                    futures_prices = self.futures_provider.get_all_prices()
            except Exception as e:
                logger.warning(f"Futures batch failed this cycle: {e}")
                futures_prices = {}

        if not spot_prices and not futures_prices:
            logger.warning("Price batch fetch returned no data this cycle — skipping checks")
            return

        self._last_success = _t.monotonic()

        checked = 0
        fired = 0
        user_ids = self.store.get_all_user_ids()

        for user_id in user_ids:
            alerts = self.store.get_user_alerts(user_id)
            fired_visuals: list[int] = []
            fired_stables: list[int] = []
            for a in alerts:
                if not a.get("enabled"):
                    continue

                symbol = str(a["symbol"]).upper()
                target = float(a["price"])
                market = str(a.get("market") or "spot").lower()

                if market == "futures":
                    if self.futures_provider is None:
                        # Feature off / no provider: never fire futures rows; leave them intact.
                        continue
                    current = futures_prices.get(symbol)
                else:
                    current = spot_prices.get(symbol)

                if current is None:
                    # Symbol might be missing from this response (or wrong market)
                    continue

                checked += 1
                tolerance = self.settings.alert_tolerance_percent
                diff_ratio = abs(current - target) / target if target != 0 else float("inf")
                within_band = diff_ratio <= tolerance

                # Crossing detection: fire if price crossed the target since last poll.
                # This makes "reached/travelled up to the price" much more reliable
                # than only the tiny tolerance band (which is easy to jump over).
                crossed = False
                # CRITICAL: use stable_id (DB PK) for key, NOT a["id"] (visual rank).
                key = (user_id, a["stable_id"])
                prev = self._last_prices.get(key)
                if prev is not None:
                    if (prev - target) * (current - target) <= 0:  # sign change or touched the level
                        crossed = True

                logger.debug(
                    f"ALERT_DECISION user={user_id} visual=#{a['id']} stable={a['stable_id']} "
                    f"market={market} symbol={symbol} target={target} current={current} prev={prev} "
                    f"band={within_band} crossed={crossed} will_fire={within_band or crossed}"
                )

                if within_band or crossed:
                    # HTML — safe with futures symbols that contain underscores (BTC_USDT)
                    import html as _html
                    tag = "F" if market == "futures" else "S"
                    sym_e = _html.escape(symbol)
                    trigger_reason = "band" if within_band else "crossed"
                    msg = (
                        f"🚨 <b>{sym_e}</b> [{tag}]\n"
                        f"Target: ${_html.escape(str(target))}\n"
                        f"<code>{current:.8f}</code>"
                    )
                    reply_markup = None
                    event_id = 0
                    if self.target_fire_log is not None:
                        try:
                            self.target_fire_log.log(
                                user_id,
                                symbol,
                                market,
                                target_price=float(target),
                                fire_price=float(current),
                                reason=trigger_reason,
                                stable_id=int(a["stable_id"]),
                            )
                        except Exception as fe:
                            logger.error("target_fire_log failed: %s", fe)
                    if self.event_store is not None and getattr(
                        self.settings, "feature_learning", False
                    ):
                        try:
                            drop_pct = None
                            if target and target != 0:
                                drop_pct = ((current - target) / target) * 100.0
                            event_id = self.event_store.log_event(
                                user_id,
                                "target",
                                symbol,
                                market,
                                price=float(current),
                                ref_price=float(target),
                                drop_pct=drop_pct,
                                mode=trigger_reason,
                                payload={
                                    "stable_id": a["stable_id"],
                                    "visual_id": a["id"],
                                    "reason": trigger_reason,
                                },
                            )
                            if event_id:
                                try:
                                    from .assistant.ux import fire_action_keyboard

                                    reply_markup = fire_action_keyboard(event_id)
                                    msg += (
                                        "\n\n<i>Tap Took / Skip / Later — no typing</i>"
                                    )
                                except Exception as ke:
                                    logger.error("target fire keyboard failed: %s", ke)
                        except Exception as le:
                            logger.error(
                                "learning log failed before target notify: %s", le
                            )
                    try:
                        if reply_markup is not None:
                            self.notifier(
                                user_id, msg, parse_mode="HTML", reply_markup=reply_markup
                            )
                        else:
                            self.notifier(user_id, msg, parse_mode="HTML")
                        logger.info(
                            f"Alert #{a['id']} (stable={a['stable_id']}) FIRED user={user_id} "
                            f"{market}:{symbol} target={target} current={current} "
                            f"(reason: {trigger_reason}) event_id={event_id or '-'}"
                        )
                        fired += 1
                        fired_visuals.append(a["id"])
                        fired_stables.append(a["stable_id"])
                    except Exception as e:
                        logger.error(f"Failed sending alert #{a['id']}: {e}")
                        # Do not remove if we couldn't notify — will retry next cycle
                        self._last_prices[key] = current  # still update last seen
                        continue

                self._last_prices[key] = current  # update for next cycle's crossing detection

            # After processing all alerts for this user, clean any last_prices for alerts that no longer exist
            current_stables = {a["stable_id"] for a in self.store.get_user_alerts(user_id)}
            keys_to_remove = [k for k in self._last_prices if k[0] == user_id and k[1] not in current_stables]
            for k in keys_to_remove:
                self._last_prices.pop(k, None)

            # Remove all that fired for this user in one go (after snapshot).
            if fired_stables:
                self.store.remove_alerts_by_stable_ids(user_id, fired_stables)
                for fs in fired_stables:
                    self._last_prices.pop((user_id, fs), None)

        elapsed_ms = int((_t.perf_counter() - start) * 1000)
        self._last_poll_ms = elapsed_ms
        if checked or fired:
            logger.info(
                f"Monitor cycle: {checked} alerts checked, {fired} fired, "
                f"spot={len(spot_prices)} fut={len(futures_prices)} in {elapsed_ms}ms"
            )

    def run(self) -> None:
        """Main loop. Fast and resilient."""
        logger.info(
            "Price monitor started (batch mode%s)",
            ", futures enabled" if self.futures_provider else "",
        )
        while not self._stop_event.is_set():
            try:
                self._check_once()
                touch_heartbeat(
                    self.settings.alerts_file_path.parent,
                    monitor=True,
                )
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
