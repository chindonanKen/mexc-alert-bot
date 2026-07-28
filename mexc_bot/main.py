"""Application entrypoint. Wires everything together and starts the bot + monitor.

V3 features (futures target alerts, downside movers) are opt-in via env flags
defaulting to OFF so production V1 behavior is preserved until you enable them.
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
        "Feature flags: futures_alerts=%s mover_scanner=%s learning=%s",
        settings.feature_futures_alerts,
        settings.feature_mover_scanner,
        settings.feature_learning,
    )

    logger.info(f"Using alerts file: {settings.alerts_file_path}")
    store = AlertStore(settings.alerts_file_path)

    price_provider: PriceProvider = MexcClient(base_url=settings.mexc_api_base)

    futures_provider: PriceProvider | None = None
    if settings.feature_futures_alerts or settings.feature_mover_scanner:
        # Always attach futures client when either V3 feature is on so mixed
        # mover watchlists (spot + futures) and /p f /af work without env surprises.
        futures_provider = MexcFuturesClient(base_url=settings.mexc_futures_api_base)
        logger.info("Futures price client ready (%s)", settings.mexc_futures_api_base)

    event_store = None
    outcome_poller = None
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

    mover_store = None
    mover_scanner = None
    if settings.feature_mover_scanner:
        from .movers import MoverScanner, MoverStore

        mover_store = MoverStore(settings.alerts_file_path)
        logger.info("Mover store ready (tables in same DB file, separate from alerts)")

    # Create bot first (with price_provider for /price). Attach monitor after.
    # Pass futures client whenever it exists so /mw add f TSLA can resolve stock
    # perps even if FEATURE_FUTURES_ALERTS is off (movers-only). /af still gated
    # by the feature flag inside bot handlers.
    tg_bot = create_bot(
        settings,
        store,
        price_provider=price_provider,
        monitor=None,
        futures_provider=futures_provider,
        mover_store=mover_store,
        mover_scanner=None,
        event_store=event_store,
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

    monitor = PriceMonitor(
        settings=settings,
        store=store,
        price_provider=price_provider,
        notifier=send_telegram_notification,
        futures_provider=futures_provider if settings.feature_futures_alerts else None,
        event_store=event_store,
    )

    tg_bot._monitor_ref = monitor  # type: ignore[attr-defined]

    if settings.feature_mover_scanner and mover_store is not None:
        from .movers import MoverScanner

        mover_scanner = MoverScanner(
            settings=settings,
            mover_store=mover_store,
            notifier=send_telegram_notification,
            # Always attach both books — scanner only fetches markets present on watchlists.
            spot_provider=price_provider,
            futures_provider=futures_provider,
            event_store=event_store,
        )
        tg_bot._mover_scanner_ref = mover_scanner  # type: ignore[attr-defined]

    # Start background workers
    monitor.start()
    logger.info("Price monitor thread started")
    if mover_scanner is not None:
        mover_scanner.start()
        logger.info("Mover scanner thread started")
    if outcome_poller is not None:
        outcome_poller.start()
        logger.info("Learning outcome poller started")

    def _shutdown(signum=None, frame=None):
        logger.info("Shutdown signal received. Stopping workers...")
        monitor.stop()
        if mover_scanner is not None:
            mover_scanner.stop()
        if outcome_poller is not None:
            outcome_poller.stop()
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
        tg_bot.polling(non_stop=True, skip_pending=True)
    except KeyboardInterrupt:
        _shutdown()
    finally:
        monitor.stop()
        if mover_scanner is not None:
            mover_scanner.stop()
        if outcome_poller is not None:
            outcome_poller.stop()


if __name__ == "__main__":
    main()
