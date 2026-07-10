"""Background downside % mover scanner.

Completely separate from PriceMonitor: never deletes target-price alerts.
Fires Telegram notifications with cooldowns (rules are not one-shot-deleted).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Dict, Optional, Set, Tuple

from ..config import Settings
from ..exchange import PriceProvider
from .history import PriceHistory
from .storage import MoverStore

logger = logging.getLogger(__name__)


class MoverScanner:
    """
    Polls price feeds, builds lookback history, alerts on downside % only.

    - Does NOT touch AlertStore / alerts table
    - Cooldown per (user_id, market, symbol) after fire
    - Watchlist-only (empty watchlist → no fires for that user)
    """

    def __init__(
        self,
        settings: Settings,
        mover_store: MoverStore,
        notifier: Callable[..., None],
        spot_provider: Optional[PriceProvider] = None,
        futures_provider: Optional[PriceProvider] = None,
    ):
        self.settings = settings
        self.mover_store = mover_store
        self.notifier = notifier
        self.spot_provider = spot_provider
        self.futures_provider = futures_provider

        max_age = max(
            float(settings.mover_lookback_seconds) * 1.5,
            float(settings.mover_lookback_seconds) + 120,
            1200.0,
        )
        self.history = PriceHistory(max_age_seconds=max_age)

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_cycle_ms: int = 0
        self._last_success: float = 0.0
        # (user_id, market, symbol) -> monotonic expire time
        self._cooldowns: Dict[Tuple[int, str, str], float] = {}
        self._fires_total: int = 0

    def get_health(self) -> dict:
        now = time.monotonic()
        return {
            "running": not self._stop_event.is_set(),
            "last_cycle_ms": self._last_cycle_ms,
            "seconds_since_last_success": (now - self._last_success) if self._last_success else 999,
            "tracked_series": self.history.tracked_count(),
            "active_cooldowns": len(self._cooldowns),
            "fires_total": self._fires_total,
        }

    def _markets_to_scan(self) -> Set[str]:
        m = (self.settings.mover_markets or "futures").lower()
        if m == "both":
            return {"spot", "futures"}
        if m == "spot":
            return {"spot"}
        return {"futures"}

    def _fetch_prices(self) -> Dict[str, Dict[str, float]]:
        """Return {market: {symbol: price}} for configured markets."""
        out: Dict[str, Dict[str, float]] = {}
        markets = self._markets_to_scan()
        if "spot" in markets and self.spot_provider is not None:
            try:
                out["spot"] = self.spot_provider.get_all_prices() or {}
            except Exception as e:
                logger.warning(f"Mover spot fetch failed: {e}")
                out["spot"] = {}
        if "futures" in markets and self.futures_provider is not None:
            try:
                out["futures"] = self.futures_provider.get_all_prices() or {}
            except Exception as e:
                logger.warning(f"Mover futures fetch failed: {e}")
                out["futures"] = {}
        return out

    def _record_all(self, prices_by_market: Dict[str, Dict[str, float]], now: float) -> None:
        for market, prices in prices_by_market.items():
            for sym, px in prices.items():
                self.history.record(market, sym, px, ts=now)

    def _on_cooldown(self, user_id: int, market: str, symbol: str) -> bool:
        key = (user_id, market, symbol)
        exp = self._cooldowns.get(key)
        if exp is None:
            return False
        if time.monotonic() >= exp:
            self._cooldowns.pop(key, None)
            return False
        return True

    def _set_cooldown(self, user_id: int, market: str, symbol: str) -> None:
        self._cooldowns[(user_id, market, symbol)] = (
            time.monotonic() + float(self.settings.mover_cooldown_seconds)
        )

    def _check_once(self) -> None:
        t0 = time.perf_counter()
        now = time.time()
        prices_by_market = self._fetch_prices()
        if not any(prices_by_market.values()):
            logger.warning("Mover scanner: no prices this cycle")
            return

        self._record_all(prices_by_market, now)
        self._last_success = time.monotonic()

        enabled_users = self.mover_store.get_enabled_users()
        fired = 0

        for user_id in enabled_users:
            settings = self.mover_store.get_settings(
                user_id,
                self.settings.mover_threshold_percent,
                self.settings.mover_lookback_seconds,
            )
            if not settings["enabled"]:
                continue

            threshold_frac = float(settings["threshold_percent"]) / 100.0
            lookback = float(settings["lookback_seconds"])
            watchlist = self.mover_store.get_watchlist(user_id)
            if not watchlist:
                continue

            for item in watchlist:
                market = str(item.get("market", "futures")).lower()
                symbol = str(item["symbol"]).upper()
                if market not in prices_by_market:
                    continue
                if symbol not in prices_by_market[market]:
                    continue

                change = self.history.pct_change_over(market, symbol, lookback, now=now)
                if change is None:
                    continue  # not enough history yet

                # Downside only: change must be negative and magnitude >= threshold
                if change > -threshold_frac:
                    continue

                if self._on_cooldown(user_id, market, symbol):
                    continue

                latest = self.history.latest(market, symbol)
                then_price = self.history.price_at_or_before(market, symbol, now - lookback)
                if latest is None or then_price is None:
                    continue
                price_now = latest[1]
                pct = change * 100.0
                minutes = int(lookback // 60)
                tag = "F" if market == "futures" else "S"
                msg = (
                    f"📉 *MOVER* [{tag}]\n"
                    f"*{symbol}*  {pct:.1f}% in {minutes}m\n"
                    f"Was `${then_price:.8g}` → now `${price_now:.8g}`"
                )
                try:
                    self.notifier(user_id, msg, parse_mode="Markdown")
                    self._set_cooldown(user_id, market, symbol)
                    self._fires_total += 1
                    fired += 1
                    logger.info(
                        f"MOVER FIRED user={user_id} {market}:{symbol} "
                        f"change={pct:.2f}% lookback={lookback}s"
                    )
                except Exception as e:
                    logger.error(f"Mover notify failed user={user_id} {symbol}: {e}")

        # Prune expired cooldowns occasionally
        mono = time.monotonic()
        expired = [k for k, exp in self._cooldowns.items() if mono >= exp]
        for k in expired:
            self._cooldowns.pop(k, None)

        self._last_cycle_ms = int((time.perf_counter() - t0) * 1000)
        if fired:
            logger.info(f"Mover cycle: {fired} fires, {self._last_cycle_ms}ms")

    def run(self) -> None:
        logger.info(
            "Mover scanner started (markets=%s lookback=%ss threshold=%s%% poll=%ss cooldown=%ss)",
            self.settings.mover_markets,
            self.settings.mover_lookback_seconds,
            self.settings.mover_threshold_percent,
            self.settings.mover_poll_seconds,
            self.settings.mover_cooldown_seconds,
        )
        while not self._stop_event.is_set():
            try:
                self._check_once()
            except Exception as e:
                logger.exception(f"Unexpected error in mover scanner: {e}")
            slept = 0.0
            interval = max(5.0, float(self.settings.mover_poll_seconds))
            while slept < interval and not self._stop_event.is_set():
                time.sleep(min(0.25, interval - slept))
                slept += 0.25
        logger.info("Mover scanner stopped")

    def start(self) -> threading.Thread:
        if self._thread and self._thread.is_alive():
            return self._thread
        self._stop_event.clear()
        self._thread = threading.Thread(target=self.run, name="mover-scanner", daemon=True)
        self._thread.start()
        return self._thread

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=8)
