"""Application entrypoint. Wires everything together and starts the bot + monitor.

V3/V4 features are opt-in via env flags defaulting to OFF so production V1
spot path is preserved until explicitly enabled.
"""

from __future__ import annotations

import logging
import os
import signal
import sys

from .bot import create_bot
from .config import load_settings
from .exchange import MexcClient, MexcFuturesClient, PriceProvider
from .monitor import PriceMonitor
from .storage import AlertStore

_log_level_name = os.getenv("LOG_LEVEL", "INFO").upper()
_log_level = getattr(logging, _log_level_name, logging.INFO)
logging.basicConfig(
    level=_log_level,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)
if _log_level <= logging.DEBUG:
    logger.info(
        "Logging level set to DEBUG (or lower) via LOG_LEVEL; per-alert decisions "
        "(prev/current/target/band/crossed) will be logged in monitor _check_once "
        "for easy diagnosis of fires."
    )


def main() -> None:
    logger.info("Loading settings...")
    settings = load_settings()
    logger.info(
        "Feature flags: futures_alerts=%s mover_scanner=%s learning=%s "
        "news=%s voice=%s mexc_private=%s isolated_agent=%s",
        settings.feature_futures_alerts,
        settings.feature_mover_scanner,
        settings.feature_learning,
        settings.feature_news_monitor,
        settings.feature_voice,
        settings.feature_mexc_private_read,
        settings.feature_isolated_dump_agent,
    )

    logger.info(f"Using alerts file: {settings.alerts_file_path}")
    store = AlertStore(settings.alerts_file_path)

    price_provider: PriceProvider = MexcClient(base_url=settings.mexc_api_base)

    futures_provider: PriceProvider | None = None
    if (
        settings.feature_futures_alerts
        or settings.feature_mover_scanner
        or settings.feature_learning
    ):
        futures_provider = MexcFuturesClient(base_url=settings.mexc_futures_api_base)
        logger.info("Futures price client ready (%s)", settings.mexc_futures_api_base)
        # Desk used to store bare bases (AXTI, ZHIPU) that never match the book
        if hasattr(futures_provider, "resolve_symbol"):
            try:
                n_fix = store.repair_futures_alert_symbols(
                    futures_provider.resolve_symbol  # type: ignore[attr-defined]
                )
                if n_fix:
                    logger.info("Repaired %s futures target symbol(s) for book match", n_fix)
            except Exception as e:
                logger.warning("Futures alert symbol repair skipped: %s", e)

    event_store = None
    outcome_poller = None
    engagement_bridge = None
    fill_sync = None
    if settings.feature_learning:
        from .learning import EventStore, OutcomePoller

        event_store = EventStore(settings.alerts_file_path)
        logger.info("Learning EventStore ready (same DB file, separate tables)")

        def _learning_get_price(market: str, symbol: str):
            try:
                if market == "futures" and futures_provider is not None:
                    return futures_provider.get_price(symbol)
                return price_provider.get_price(symbol)
            except Exception:
                return None

        outcome_poller = OutcomePoller(
            event_store,
            get_price=_learning_get_price,
            horizons_seconds=settings.learning_outcome_horizons_seconds,
            poll_seconds=settings.learning_outcome_poll_seconds,
        )
        if settings.learning_auto_from_positions:
            from .learning import EngagementBridge

            uid_hint = settings.mexc_private_telegram_user_id
            engagement_bridge = EngagementBridge(
                event_store,
                grace_seconds=settings.learning_grace_seconds,
                max_pending=settings.learning_max_pending_questions,
                poll_seconds=settings.learning_engagement_poll_seconds,
                user_ids=[int(uid_hint)] if uid_hint else None,
            )
            logger.info(
                "Engagement bridge ready grace=%ss max_pending=%s",
                settings.learning_grace_seconds,
                settings.learning_max_pending_questions,
            )

    mover_store = None
    mover_scanner = None
    if settings.feature_mover_scanner:
        from .movers import MoverScanner, MoverStore

        mover_store = MoverStore(settings.alerts_file_path)
        logger.info("Mover store ready (tables in same DB file, separate from alerts)")

    news_store = None
    news_watcher = None
    if settings.feature_news_monitor:
        from .news import NewsWatcher
        from .news.store import NewsStore

        news_store = NewsStore(settings.alerts_file_path)
        logger.info("News store ready")

    tg_bot = create_bot(
        settings,
        store,
        price_provider=price_provider,
        monitor=None,
        futures_provider=futures_provider,
        mover_store=mover_store,
        mover_scanner=None,
        event_store=event_store,
        news_store=news_store,
    )

    def send_telegram_notification(
        user_id: int,
        text: str,
        parse_mode: str | None = None,
        reply_markup=None,
    ) -> None:
        try:
            tg_bot.send_message(
                user_id,
                text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
        except Exception as e:
            logger.error(f"Failed to send Telegram message to {user_id}: {e}")

    from .reports.fire_log import TargetFireLog

    target_fire_log = TargetFireLog(settings.alerts_file_path)

    monitor = PriceMonitor(
        settings=settings,
        store=store,
        price_provider=price_provider,
        notifier=send_telegram_notification,
        futures_provider=futures_provider if settings.feature_futures_alerts else None,
        event_store=event_store,
        target_fire_log=target_fire_log,
    )

    tg_bot._monitor_ref = monitor  # type: ignore[attr-defined]

    # Daily 6 AM target report (hits + near-misses) — in-process scheduler
    import os as _os

    if _os.getenv("FEATURE_DAILY_TARGET_REPORT", "true").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        try:
            from .reports.daily_targets import start_daily_report_thread

            start_daily_report_thread(settings, monitor._stop_event)
        except Exception as e:
            logger.warning("Daily target report scheduler not started: %s", e)

    isolated_agent = None
    delist_radar = None
    inv_bridge = None

    if settings.feature_mover_scanner and mover_store is not None:
        from .movers import MoverScanner

        mover_scanner = MoverScanner(
            settings=settings,
            mover_store=mover_store,
            notifier=send_telegram_notification,
            spot_provider=price_provider,
            futures_provider=futures_provider,
            event_store=event_store,
        )
        tg_bot._mover_scanner_ref = mover_scanner  # type: ignore[attr-defined]

    if settings.feature_isolated_dump_agent:
        from .investigators import IsolatedDumpAgent, DelistRadar, InvestigatorStore
        from .investigators.outcome_bridge import InvestigationOutcomeBridge
        from .investigators.triggers import IsolatedDumpCriteria

        inv_store = InvestigatorStore(settings.alerts_file_path)
        delist_radar = DelistRadar(
            inv_store, poll_seconds=settings.delist_radar_poll_seconds
        )
        criteria = IsolatedDumpCriteria(
            min_drop_pct=settings.isolated_min_drop_pct,
            threshold_multiplier=settings.isolated_threshold_multiplier,
            max_heat_breadth=settings.isolated_max_heat_breadth,
            require_fast_or_panic=settings.isolated_require_fast_or_panic,
            allow_grind=False,
        )

        def _inv_price(market: str, symbol: str):
            try:
                if market == "futures" and futures_provider is not None:
                    return futures_provider.get_price(symbol)
                return price_provider.get_price(symbol)
            except Exception:
                return None

        isolated_agent = IsolatedDumpAgent(
            inv_store,
            notifier=send_telegram_notification,
            radar=delist_radar,
            criteria=criteria,
            cooldown_seconds=settings.isolated_cooldown_seconds,
            notify_none=settings.isolated_notify_none,
            learning_outcome_horizon=settings.isolated_outcome_horizon_seconds,
            get_price=_inv_price,
        )
        if mover_scanner is not None:
            mover_scanner.isolated_agent = isolated_agent
        tg_bot._isolated_agent_ref = isolated_agent  # type: ignore[attr-defined]
        tg_bot._investigator_store_ref = inv_store  # type: ignore[attr-defined]
        logger.info(
            "Isolated dump agent ready (min_drop=%s%% mult=%s max_heat=%s outcome_h=%ss)",
            settings.isolated_min_drop_pct,
            settings.isolated_threshold_multiplier,
            settings.isolated_max_heat_breadth,
            settings.isolated_outcome_horizon_seconds,
        )
        if event_store is not None:
            inv_bridge = InvestigationOutcomeBridge(
                inv_store,
                event_store,
                horizon_seconds=settings.isolated_outcome_horizon_seconds,
                poll_seconds=120,
            )

    if settings.feature_news_monitor and news_store is not None:
        from .news import NewsWatcher

        def _watch_bases():
            from .news.book import normalize_news_base

            bases = set()

            def _add(raw: str) -> None:
                b = normalize_news_base(str(raw or ""))
                if len(b) >= 2:
                    bases.add(b)

            if mover_store is not None:
                try:
                    # watch book names across users
                    for uid in store.get_all_user_ids():
                        for row in mover_store.get_watchlist(uid):
                            _add(row.get("symbol") or "")
                except Exception:
                    pass
            try:
                for uid in store.get_all_user_ids():
                    for a in store.get_user_alerts(uid):
                        _add(a.get("symbol") or "")
            except Exception:
                pass
            if event_store is not None:
                try:
                    for uid in store.get_all_user_ids():
                        for e in event_store.recent_events(uid, limit=30):
                            _add(e.get("symbol") or "")
                        for t in event_store.journal_list(uid, open_only=True):
                            _add(t.get("symbol") or "")
                except Exception:
                    pass
            return bases

        def _news_users():
            try:
                return list(store.get_all_user_ids())
            except Exception:
                return []

        news_watcher = NewsWatcher(
            news_store,
            notifier=send_telegram_notification,
            get_watch_bases=_watch_bases,
            poll_seconds=settings.news_poll_seconds,
            push_unconfirmed=settings.news_push_unconfirmed,
            get_notify_user_ids=_news_users,
        )

    if (
        settings.feature_mexc_private_read
        and settings.feature_learning
        and event_store is not None
        and settings.mexc_api_key
        and settings.mexc_api_secret
        and settings.mexc_private_telegram_user_id
    ):
        from .exchange_private import (
            MexcPrivateFuturesClient,
            MexcPrivateSpotClient,
            normalize_futures_symbol,
        )
        from .learning.fills import FillSyncPoller

        spot_base = settings.mexc_api_base.replace("/api/v3", "")
        if "/api/v3" in settings.mexc_api_base:
            spot_base = (
                settings.mexc_api_base.split("/api/v3")[0] or "https://api.mexc.com"
            )
        priv = MexcPrivateSpotClient(
            settings.mexc_api_key,
            settings.mexc_api_secret,
            base_url=spot_base,
        )
        fut_base = "https://contract.mexc.com"
        if getattr(settings, "mexc_futures_api_base", None):
            fb = settings.mexc_futures_api_base
            fut_base = fb.split("/api/")[0] if "/api/" in fb else fb.rstrip("/")
        fut_priv = MexcPrivateFuturesClient(
            settings.mexc_api_key,
            settings.mexc_api_secret,
            base_url=fut_base or "https://contract.mexc.com",
        )
        uid = int(settings.mexc_private_telegram_user_id)

        def _fill_syms():
            try:
                return set(event_store.symbols_for_fill_sync(uid))
            except Exception:
                return set()

        def _fut_syms():
            try:
                out = set(event_store.futures_symbols_for_fill_sync(uid))
            except Exception:
                out = set()
            try:
                if mover_store is not None:
                    for row in mover_store.get_watchlist(uid):
                        if (row.get("market") or "").lower() != "futures":
                            continue
                        s = normalize_futures_symbol(str(row.get("symbol") or ""))
                        if s:
                            out.add(s)
            except Exception:
                pass
            try:
                for a in store.get_user_alerts(uid):
                    if (a.get("market") or "").lower() != "futures":
                        continue
                    s = normalize_futures_symbol(str(a.get("symbol") or ""))
                    if s:
                        out.add(s)
            except Exception:
                pass
            out.discard("")
            return out

        # Cleanup auto journal junk that mixed with fill-based entities
        try:
            n = event_store.purge_auto_journal_trades(uid)
            if n:
                logger.info("Purged %s auto journal_trades for user %s", n, uid)
        except Exception as e:
            logger.debug("purge auto journal: %s", e)

        fill_sync = FillSyncPoller(
            event_store,
            priv,
            uid,
            get_symbols=_fill_syms,
            poll_seconds=settings.mexc_fill_sync_poll_seconds,
            notifier=send_telegram_notification,
            notify_on_new=settings.mexc_fill_notify,
            futures_client=fut_priv,
            get_futures_symbols=_fut_syms,
            write_auto_journal=False,
        )
        logger.info(
            "MEXC private fill sync configured for user_id=%s (spot+futures)", uid
        )
    elif settings.feature_mexc_private_read:
        logger.warning(
            "FEATURE_MEXC_PRIVATE_READ on but missing keys, learning, or "
            "MEXC_PRIVATE_TELEGRAM_USER_ID — fill sync not started"
        )

    # Start background workers
    monitor.start()
    logger.info("Price monitor thread started")
    if mover_scanner is not None:
        mover_scanner.start()
        logger.info("Mover scanner thread started")
    if outcome_poller is not None:
        outcome_poller.start()
        logger.info("Learning outcome poller started")
    if engagement_bridge is not None:
        engagement_bridge.start()
        logger.info("Engagement bridge started")
    if news_watcher is not None:
        news_watcher.start()
        logger.info("News watcher started")
    if fill_sync is not None:
        fill_sync.start()
        logger.info("Fill sync poller started")
    if delist_radar is not None:
        delist_radar.start()
        logger.info("Delist radar started")
    if isolated_agent is not None:
        isolated_agent.start()
        logger.info("Isolated dump agent worker started")
    if inv_bridge is not None:
        inv_bridge.start()
        logger.info("Investigation outcome bridge started")

    def _shutdown(signum=None, frame=None):
        logger.info("Shutdown signal received. Stopping workers...")
        monitor.stop()
        if mover_scanner is not None:
            mover_scanner.stop()
        if outcome_poller is not None:
            outcome_poller.stop()
        if news_watcher is not None:
            news_watcher.stop()
        if fill_sync is not None:
            fill_sync.stop()
        if delist_radar is not None:
            delist_radar.stop()
        if isolated_agent is not None:
            isolated_agent.stop()
        if inv_bridge is not None:
            inv_bridge.stop()
        try:
            price_provider.close()
        except Exception:
            pass
        if futures_provider is not None:
            try:
                futures_provider.close()
            except Exception:
                pass
        logger.info("Goodbye.")
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    logger.info("Starting Telegram bot polling...")
    try:
        from .heartbeat import touch_heartbeat

        touch_heartbeat(settings.alerts_file_path.parent, polling=True)
        tg_bot.polling(non_stop=True, skip_pending=True)
    except KeyboardInterrupt:
        _shutdown()
    finally:
        monitor.stop()
        if mover_scanner is not None:
            mover_scanner.stop()
        if outcome_poller is not None:
            outcome_poller.stop()
        if news_watcher is not None:
            news_watcher.stop()
        if fill_sync is not None:
            fill_sync.stop()
        if delist_radar is not None:
            delist_radar.stop()
        if isolated_agent is not None:
            isolated_agent.stop()
        if inv_bridge is not None:
            inv_bridge.stop()


if __name__ == "__main__":
    main()
