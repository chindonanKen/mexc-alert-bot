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

Enrichments (optional, never block core fire):
  - Velocity / panic band
  - Volume line (when available)
  - Red-candle streaks (kline API, flag-gated)
  - Auto panic heat board when watchlist breadth dumps (no /mw required)
"""

from __future__ import annotations

import html as _html
import logging
import threading
import time
from typing import Callable, Dict, Optional, Set, Tuple

from ..config import Settings
from ..exchange import PriceProvider
from .heat import (
    board_fingerprint,
    format_heat_board_html,
    heat_snapshot,
    is_widespread_panic,
)
from .history import PriceHistory
from .klines import KlineClient, format_reds_line
from .storage import MoverStore
from .velocity import format_velocity_line, score_dump

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
    - Auto panic board when many watchlist names dump together
    """

    def __init__(
        self,
        settings: Settings,
        mover_store: MoverStore,
        notifier: Callable[..., None],
        spot_provider: Optional[PriceProvider] = None,
        futures_provider: Optional[PriceProvider] = None,
        event_store=None,
    ):
        self.settings = settings
        self.mover_store = mover_store
        self.notifier = notifier
        self.spot_provider = spot_provider
        self.futures_provider = futures_provider
        # Optional learning EventStore — log fires only; never touch alerts
        self.event_store = event_store

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
        # Wall-clock last fire (for step-mode velocity, not mono clock)
        self._last_fire_wall: Dict[Key, float] = {}
        self._fires_total: int = 0
        self._missing_symbol_logs: int = 0

        # Auto heat board anti-spam (per user)
        self._last_heat_mono: Dict[int, float] = {}
        self._last_heat_fp: Dict[int, tuple] = {}
        self._heat_boards_total: int = 0

        # Optional volume cache from last futures batch: symbol -> amount24-like
        self._volume_by_market: Dict[str, Dict[str, float]] = {}

        self._kline_client: Optional[KlineClient] = None
        if getattr(settings, "mover_enrich_klines", False):
            self._kline_client = KlineClient(
                spot_base=getattr(settings, "mexc_api_base", "https://api.mexc.com/api/v3"),
                futures_base=getattr(
                    settings, "mexc_futures_api_base", "https://contract.mexc.com/api/v1"
                ),
            )

    def get_health(self) -> dict:
        now = time.monotonic()
        return {
            "running": not self._stop_event.is_set(),
            "last_cycle_ms": self._last_cycle_ms,
            "seconds_since_last_success": (now - self._last_success) if self._last_success else 999,
            "tracked_series": self.history.tracked_count(),
            "active_anchors": len(self._anchors),
            "fires_total": self._fires_total,
            "heat_boards_total": self._heat_boards_total,
            "poll_seconds": max(_MIN_POLL_SECONDS, float(self.settings.mover_poll_seconds)),
            "min_gap_seconds": float(self.settings.mover_cooldown_seconds),
            "heat_auto": bool(getattr(self.settings, "mover_heat_auto", True)),
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
                # Optional volume map if provider exposes it
                getter = getattr(self.futures_provider, "get_all_volumes", None)
                if callable(getter) and getattr(self.settings, "mover_enrich_volume", True):
                    try:
                        self._volume_by_market["futures"] = getter() or {}
                    except Exception:
                        pass
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
        raw = getattr(self.settings, "mover_recovery_percent", 3.0)
        try:
            return max(0.0, float(raw)) / 100.0
        except (TypeError, ValueError):
            return 0.03

    def _velocity_thresholds(self) -> Tuple[float, float]:
        panic = float(getattr(self.settings, "mover_velocity_panic", 2.0))
        fast = float(getattr(self.settings, "mover_velocity_fast", 0.8))
        return panic, fast

    def _breadth_pct_for_user(self, user_threshold_percent: float) -> float:
        raw = getattr(self.settings, "mover_heat_breadth_pct", None)
        if raw is not None and str(raw).strip() != "":
            try:
                return abs(float(raw))
            except (TypeError, ValueError):
                pass
        # Default: slightly earlier than full MOVER fire (60% of user threshold)
        return max(0.5, abs(float(user_threshold_percent)) * 0.6)

    def _volume_line(self, market: str, symbol: str) -> str:
        if not getattr(self.settings, "mover_enrich_volume", True):
            return ""
        book = self._volume_by_market.get(market) or {}
        vol = book.get(symbol.upper())
        if vol is None or vol <= 0:
            return ""
        # Heuristic format
        if vol >= 1_000_000:
            s = f"{vol / 1_000_000:.1f}M"
        elif vol >= 1_000:
            s = f"{vol / 1_000:.1f}K"
        else:
            s = f"{vol:.4g}"
        # Avoid full-exchange terciles (alts always look "low"); raw turnover only.
        return f"Vol 24h: {s}"

    def _reds_line(self, market: str, symbol: str) -> str:
        if not getattr(self.settings, "mover_enrich_klines", False):
            return ""
        if self._kline_client is None:
            return ""
        try:
            counts = self._kline_client.consecutive_reds(market, symbol)
            return format_reds_line(counts)
        except Exception as e:
            logger.debug("Reds enrich failed %s:%s: %s", market, symbol, e)
            return ""

    def _maybe_send_heat_board(
        self,
        user_id: int,
        lookback: float,
        threshold_percent: float,
        watchlist: list,
        now: float,
    ) -> None:
        if not getattr(self.settings, "mover_heat_auto", True):
            return
        if not watchlist:
            return

        breadth_min = int(getattr(self.settings, "mover_heat_breadth_min", 3))
        top_n = int(getattr(self.settings, "mover_heat_top_n", 5))
        min_gap = float(getattr(self.settings, "mover_heat_min_gap_seconds", 45))
        refresh = float(getattr(self.settings, "mover_heat_refresh_seconds", 90))
        panic_v, fast_v = self._velocity_thresholds()
        breadth_pct = self._breadth_pct_for_user(threshold_percent)

        board = heat_snapshot(
            self.history,
            watchlist,
            lookback,
            now=now,
            panic_per_min=panic_v,
            fast_per_min=fast_v,
            breadth_pct=breadth_pct,
        )
        if not is_widespread_panic(board, breadth_min):
            return

        fp = board_fingerprint(board.ranked, top_n)
        mono = time.monotonic()
        last_m = self._last_heat_mono.get(user_id)
        last_fp = self._last_heat_fp.get(user_id)

        # Anti-spam: first board free; later only if min-gap passed and
        # (fingerprint changed + refresh elapsed) OR leader changed after min-gap.
        allow = False
        if last_m is None:
            allow = True
        else:
            elapsed = mono - last_m
            if elapsed >= min_gap and last_fp != fp:
                # Leader identity only (market, symbol) — ignore 0.1% dd noise
                def _leader_id(x):
                    if not x:
                        return None
                    row = x[0]
                    return (row[0], row[1]) if len(row) >= 2 else row

                leader_changed = _leader_id(last_fp) != _leader_id(fp)
                if elapsed >= refresh or leader_changed:
                    allow = True

        if not allow:
            return

        msg = format_heat_board_html(board, top_n=top_n)
        try:
            self.notifier(user_id, msg, parse_mode="HTML")
            self._last_heat_mono[user_id] = mono
            self._last_heat_fp[user_id] = fp
            self._heat_boards_total += 1
            logger.info(
                "HEAT BOARD user=%s dumping=%s top=%s",
                user_id,
                board.breadth_frac,
                fp[:3] if fp else (),
            )
        except Exception as e:
            logger.error(f"Heat board notify failed user={user_id}: {e}")

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
        panic_v, fast_v = self._velocity_thresholds()
        enrich_velocity = getattr(self.settings, "mover_enrich_velocity", True)

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

            # Auto panic board FIRST (triage before individual fires)
            try:
                self._maybe_send_heat_board(
                    user_id, lookback, float(settings["threshold_percent"]), watchlist, now
                )
            except Exception as e:
                logger.warning(f"Heat board error user={user_id}: {e}")

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
                peak_ts: Optional[float] = None
                peak_price_for_vel: Optional[float] = None

                if anchor is None:
                    dd = self.history.peak_drawdown(market, symbol, lookback, now=now)
                    if dd is None:
                        continue
                    change, peak_price, hist_now, p_ts = dd
                    price_now = float(hist_now)
                    if change > -threshold_frac:
                        continue
                    fire_mode = "peak"
                    pct = change * 100.0
                    ref_price = float(peak_price)
                    peak_ts = float(p_ts)
                    peak_price_for_vel = float(peak_price)
                else:
                    step_change = (price_now - anchor) / anchor
                    if step_change > -threshold_frac:
                        continue
                    fire_mode = "step"
                    pct = step_change * 100.0
                    ref_price = float(anchor)
                    # Velocity from last fire wall time → now (real cascade pace)
                    peak_price_for_vel = float(anchor)
                    peak_ts = self._last_fire_wall.get(key)
                    if peak_ts is None:
                        peak_ts = now - 60.0  # fallback: mild grind-ish window

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
                extra_lines = []
                vel_band: Optional[str] = None
                if enrich_velocity and peak_ts is not None and peak_price_for_vel is not None:
                    vel, mins, band = score_dump(
                        peak_ts,
                        peak_price_for_vel,
                        now,
                        price_now,
                        panic_per_min=panic_v,
                        fast_per_min=fast_v,
                    )
                    vel_band = band
                    vline = format_velocity_line(vel, mins, band)
                    if vline:
                        extra_lines.append(_html.escape(vline))
                vline2 = self._volume_line(market, symbol)
                if vline2:
                    extra_lines.append(_html.escape(vline2))
                rline = self._reds_line(market, symbol)
                if rline:
                    extra_lines.append(_html.escape(rline))

                msg = f"📉 <b>MOVER</b> [{tag}]\n{line2}\n{line3}"
                if extra_lines:
                    msg += "\n" + "\n".join(extra_lines)

                try:
                    self.notifier(user_id, msg, parse_mode="HTML")
                    self._anchors[key] = price_now
                    self._last_fire_mono[key] = time.monotonic()
                    self._last_fire_wall[key] = now
                    self._fires_total += 1
                    fired += 1
                    logger.info(
                        f"MOVER FIRED user={user_id} {market}:{symbol} mode={fire_mode} "
                        f"pct={pct:.2f}% ref={ref_price} now={price_now} lookback={lookback}s"
                    )
                    # Learning log (soft-fail inside EventStore; never affects fire)
                    if self.event_store is not None and getattr(
                        self.settings, "feature_learning", False
                    ):
                        try:
                            src = (
                                "mover_peak"
                                if fire_mode == "peak"
                                else "mover_step"
                            )
                            self.event_store.log_event(
                                user_id,
                                src,
                                symbol,
                                market,
                                ts=now,
                                price=float(price_now),
                                ref_price=float(ref_price),
                                drop_pct=float(pct),
                                velocity_band=vel_band,
                                mode=fire_mode,
                                payload={"lookback_seconds": lookback},
                            )
                        except Exception as le:
                            logger.error(
                                "learning log failed after mover fire: %s", le
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
            "min_gap=%ss recovery=%s%% heat_auto=%s velocity=%s volume=%s klines=%s)",
            self.settings.mover_markets,
            self.settings.mover_lookback_seconds,
            self.settings.mover_threshold_percent,
            poll,
            self.settings.mover_cooldown_seconds,
            getattr(self.settings, "mover_recovery_percent", 3.0),
            getattr(self.settings, "mover_heat_auto", True),
            getattr(self.settings, "mover_enrich_velocity", True),
            getattr(self.settings, "mover_enrich_volume", True),
            getattr(self.settings, "mover_enrich_klines", False),
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
        if self._kline_client is not None:
            try:
                self._kline_client.close()
            except Exception:
                pass
