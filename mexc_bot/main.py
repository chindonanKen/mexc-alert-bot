"""Application entrypoint. Wires everything together and starts the bot + monitor."""

import logging
import signal
import sys
from pathlib import Path

from .bot import create_bot
from .config import load_settings
from .exchange import MexcClient
from .monitor import PriceMonitor
from .storage import AlertStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    logger.info("Loading settings...")
    settings = load_settings()

    logger.info(f"Using alerts file: {settings.alerts_file_path}")
    store = AlertStore(settings.alerts_file_path)

    client = MexcClient(base_url=settings.mexc_api_base)

    # Create bot first (with client for /price). We'll attach monitor after for health info in /status.
    tg_bot = create_bot(settings, store, client=client, monitor=None)

    # Now we can safely define the notifier (tg_bot exists)
    def send_telegram_notification(user_id: int, text: str) -> None:
        try:
            tg_bot.send_message(user_id, text)
        except Exception as e:
            logger.error(f"Failed to send Telegram message to {user_id}: {e}")

    monitor = PriceMonitor(
        settings=settings,
        store=store,
        client=client,
        notifier=send_telegram_notification,
    )

    # Attach for /status health (the status handler checks for it)
    tg_bot._monitor_ref = monitor  # type: ignore[attr-defined]

    # Start price monitor in background
    monitor.start()
    logger.info("Price monitor thread started")

    # Graceful shutdown handling
    def _shutdown(signum=None, frame=None):
        logger.info("Shutdown signal received. Stopping monitor...")
        monitor.stop()
        try:
            client.close()
        except Exception:
            pass
        logger.info("Goodbye.")
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    logger.info("Starting Telegram bot polling...")
    # This blocks forever (pyTelegramBotAPI polling)
    try:
        tg_bot.polling(non_stop=True, skip_pending=True)
    except KeyboardInterrupt:
        _shutdown()
    finally:
        monitor.stop()


if __name__ == "__main__":
    main()
