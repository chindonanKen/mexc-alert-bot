"""Background downside % mover scanner.

Completely separate from PriceMonitor: never deletes target-price alerts.

Detection model (snappy, no candle close):
  1) First alert: rolling **high → now** drawdown over the lookback window
     reaches the user threshold.
  2) Cascade re-arm: after a fire, set anchor = fire price. Fire again when
     price drops another full threshold **from that anchor** (not a long mute).
  3) Recovery: if price bounces enough above the anchor, clear it and return
     to peak-drawdown mode for the next wave.
  4) MOVER_COOLDOWN_SECONDS is only a short **min-gap** anti-spam between fires
     for the same (user, market, symbol) — not a 30-minute silence.
"""

from __future__ import annotations

import html as _html
import logging
import threading
import time
from typing import Callable, Dict, Optional, Set, Tuple

from ..config import Settings
from ..exchange import PriceProvider
from .history import PriceHistory
from .storage import MoverStore

logger = logging.getLogger(__name__)

# Hard floor for poll interval (seconds). Env may set lower; we clamp here.
_MIN_POLL_SECONDS = 2.0

Key = Tuple[int, str, str]  # user_id, market, symbol


class MoverScanner:
    """
    Polls price feeds, builds lookback history, alerts on downside % only.

    - Does NOT touch AlertStore / alerts table
    - Step-down re-arm from last fire price (cascade dumps keep alerting)
    - Short min-gap only (settings.mover_cooldown_seconds)
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
        # After fire: next alert needs another full threshold drop from this price
        self._anchors: Dict[Key, float] = {}
        # Min-gap: last fire monotonic time per key
        self._last_fire_mono: Dict[Key, float] = {}
        self._fires_total: int = 0
        self._missing_symbol_logs: int = 0

    def get_health(self) -> dict:
        now = time.monotonic()
        return {
            "running": not self._stop_event.is_set(),
            "last_cycle_ms": self._last_cycle_ms,
            "seconds_since_last_success": (now - self._last_success) if self._last_success else 999,
            "tracked_series": self.history.tracked_count(),
            "active_anchors": len(self._anchors),
            "fires_total": self._fires_total,
            "poll_seconds": max(_MIN_POLL_SECONDS, float(self.settings.mover_poll_seconds)),
            "min_gap_seconds": float(self.settings.mover_cooldown_seconds),
        }

    def _markets_to_scan(self) -> Set[str]:
        """Markets needed by enabled users' watchlists (spot and/or futures can mix).

        Falls back to MOVER_MARKETS config when nobody is enabled / lists are empty.
        """
        needed: Set[str] = set()
        try:
            for uid in self.mover_store.get_enabled_users():
                for it in self.mover_store.get_watchlist(uid):
                    m = str(it.get("market") or "futures").lower()
                    if m in ("spot", "futures"):
                        needed.add(m)
        except Exception as e:
            logger.warning(f"Could not read watchlist markets: {e}")

        if needed:
            return needed

        m = (self.settings.mover_markets or "both").lower()
        if m == "spot":
            return {"spot"}
        if m == "futures":
            return {"futures"}
        return {"spot", "futures"}

    def _fetch_prices(self) -> Dict[str, Dict[str, float]]:
        """Return {market: {symbol: price}} for markets actually on watchlists."""
        out: Dict[str, Dict[str, float]] = {}
        markets = self._markets_to_scan()
        if "spot" in markets and self.spot_provider is not None:
            try:
                out["spot"] = self.spot_provider.get_all_prices() or {}
            except Exception as e:
                logger.warning(f"Mover spot fetch failed: {e}")
                out["spot"] = {}
        elif "spot" in markets and self.spot_provider is None:
            logger.warning("Watchlist has spot symbols but spot_provider is not attached")
        if "futures" in markets and self.futures_provider is not None:
            try:
                out["futures"] = self.futures_provider.get_all_prices() or {}
            except Exception as e:
                logger.warning(f"Mover futures fetch failed: {e}")
                out["futures"] = {}
        elif "futures" in markets and self.futures_provider is None:
            logger.warning("Watchlist has futures symbols but futures_provider is not attached")
        return out

    def _record_all(self, prices_by_market: Dict[str, Dict[str, float]], now: float) -> None:
        for market, prices in prices_by_market.items():
            for sym, px in prices.items():
                self.history.record(market, sym, px, ts=now)

    def _min_gap_blocks(self, key: Key) -> bool:
        """True if we fired too recently for this key (anti-spam only)."""
        last = self._last_fire_mono.get(key)
        if last is None:
            return False
        gap = max(0.0, float(self.settings.mover_cooldown_seconds))
        if gap <= 0:
            return False
        return (time.monotonic() - last) < gap

    def _format_lookback(self, lookback: float) -> str:
        if lookback < 60:
            return f"{int(lookback)}s"
        minutes = lookback / 60.0
        if abs(minutes - round(minutes)) < 1e-6:
            return f"{int(round(minutes))}m"
        return f"{minutes:.1f}m"

    def _recovery_frac(self) -> float:
        # Optional setting; default 3% bounce clears cascade anchor
        raw = getattr(self.settings, "mover_recovery_percent", 3.0)
        try:
            return max(0.0, float(raw)) / 100.0
        except (TypeError, ValueError):
            return 0.03

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
        recovery_frac = self._recovery_frac()

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
                book = prices_by_market[market]
                if symbol not in book:
                    self._missing_symbol_logs += 1
                    if self._missing_symbol_logs <= 20 or self._missing_symbol_logs % 50 == 0:
                        logger.warning(
                            "Mover: %s:%s on watchlist but not in live book "
                            "(check spot vs futures / symbol id)",
                            market,
                            symbol,
                        )
                    continue

                price_now = float(book[symbol])
                if price_now <= 0:
                    continue

                key: Key = (user_id, market, symbol)
                anchor = self._anchors.get(key)

                # Dead-cat bounce: clear cascade state so next dump uses peak mode again.
                # Skip fire evaluation this cycle — bounce can still look like −threshold
                # vs an old window high (e.g. 100→90 fire, bounce to 93 clears, but
                # 100→93 is still −7% peak drawdown).
                if anchor is not None and recovery_frac > 0:
                    if price_now >= anchor * (1.0 + recovery_frac):
                        logger.debug(
                            "Mover anchor cleared (recovery) %s:%s anchor=%s now=%s",
                            market,
                            symbol,
                            anchor,
                            price_now,
                        )
                        self._anchors.pop(key, None)
                        continue

                fire_mode: Optional[str] = None  # "peak" | "step"
                pct: float = 0.0
                ref_price: float = 0.0

                if anchor is None:
                    # First / re-armed wave: high within lookback → now
                    dd = self.history.peak_drawdown(market, symbol, lookback, now=now)
                    if dd is None:
                        continue
                    change, peak_price, hist_now = dd
                    price_now = float(hist_now)
                    if change > -threshold_frac:
                        continue
                    fire_mode = "peak"
                    pct = change * 100.0
                    ref_price = float(peak_price)
                else:
                    # Cascade: another full threshold step below last fire price
                    step_change = (price_now - anchor) / anchor
                    if step_change > -threshold_frac:
                        continue
                    fire_mode = "step"
                    pct = step_change * 100.0
                    ref_price = float(anchor)

                if self._min_gap_blocks(key):
                    continue

                window = self._format_lookback(lookback)
                tag = "F" if market == "futures" else "S"
                sym_e = _html.escape(symbol)
                if fire_mode == "peak":
                    line2 = f"<b>{sym_e}</b>  {pct:.1f}% within {window}"
                    line3 = (
                        f"High <code>{ref_price:.8g}</code> → now <code>{price_now:.8g}</code>"
                    )
                else:
                    line2 = f"<b>{sym_e}</b>  {pct:.1f}% step from last alert"
                    line3 = (
                        f"Last <code>{ref_price:.8g}</code> → now <code>{price_now:.8g}</code>"
                    )
                msg = f"📉 <b>MOVER</b> [{tag}]\n{line2}\n{line3}"

                try:
                    self.notifier(user_id, msg, parse_mode="HTML")
                    self._anchors[key] = price_now
                    self._last_fire_mono[key] = time.monotonic()
                    self._fires_total += 1
                    fired += 1
                    logger.info(
                        f"MOVER FIRED user={user_id} {market}:{symbol} mode={fire_mode} "
                        f"pct={pct:.2f}% ref={ref_price} now={price_now} lookback={lookback}s"
                    )
                except Exception as e:
                    logger.error(f"Mover notify failed user={user_id} {symbol}: {e}")

        self._last_cycle_ms = int((time.perf_counter() - t0) * 1000)
        if fired:
            logger.info(f"Mover cycle: {fired} fires, {self._last_cycle_ms}ms")

    def run(self) -> None:
        poll = max(_MIN_POLL_SECONDS, float(self.settings.mover_poll_seconds))
        logger.info(
            "Mover scanner started (markets=%s lookback=%ss threshold=%s%% poll=%ss "
            "min_gap=%ss recovery=%s%% mode=peak+step_rearm)",
            self.settings.mover_markets,
            self.settings.mover_lookback_seconds,
            self.settings.mover_threshold_percent,
            poll,
            self.settings.mover_cooldown_seconds,
            getattr(self.settings, "mover_recovery_percent", 3.0),
        )
        while not self._stop_event.is_set():
            try:
                self._check_once()
            except Exception as e:
                logger.exception(f"Unexpected error in mover scanner: {e}")
            slept = 0.0
            interval = max(_MIN_POLL_SECONDS, float(self.settings.mover_poll_seconds))
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
