"""Telegram bot interface and command handlers."""

import logging
from typing import Optional

import telebot

from .config import Settings
from .storage import AlertStore

logger = logging.getLogger(__name__)


def create_bot(settings: Settings, store: AlertStore) -> telebot.TeleBot:
    """Create and configure the Telegram bot with all handlers."""

    bot = telebot.TeleBot(settings.telegram_bot_token, parse_mode=None)

    def _reply(message, text: str):
        try:
            bot.reply_to(message, text)
        except Exception as e:
            logger.warning(f"Failed to reply to user {message.from_user.id}: {e}")

    @bot.message_handler(commands=["start", "help"])
    def cmd_start(message):
        text = (
            "MEXC Alert Bot\n\n"
            "Commands:\n"
            "/addalert SYMBOL PRICE   — e.g. /addalert BTCUSDT 65000\n"
            "/listalerts              — show your alerts\n"
            "/togglealert ID          — enable/disable an alert\n"
            "/removealert ID          — delete an alert\n"
            "/status                  — quick stats\n\n"
            "Alerts are one-shot: they fire once when price is within tolerance and are then removed."
        )
        _reply(message, text)

    @bot.message_handler(commands=["addalert"])
    def cmd_addalert(message):
        user_id = message.from_user.id
        args = message.text.split()[1:]
        if len(args) != 2:
            _reply(message, "Usage: /addalert SYMBOL PRICE\nExample: /addalert ETHUSDT 2450.5")
            return
        symbol = args[0].upper()
        try:
            price = float(args[1])
        except ValueError:
            _reply(message, "Price must be a number.")
            return

        alert_id = store.add_alert(user_id, symbol, price)
        _reply(message, f"✅ Alert #{alert_id} created: {symbol} @ ${price}")

    @bot.message_handler(commands=["listalerts"])
    def cmd_listalerts(message):
        user_id = message.from_user.id
        alerts = store.get_user_alerts(user_id)
        if not alerts:
            _reply(message, "No alerts set. Use /addalert SYMBOL PRICE")
            return

        lines = ["Your Alerts:"]
        for a in alerts:
            status = "🟢 ON" if a.get("enabled") else "🔴 OFF"
            lines.append(f"#{a['id']}  {a['symbol']} @ ${a['price']}  [{status}]")
        _reply(message, "\n".join(lines))

    @bot.message_handler(commands=["togglealert"])
    def cmd_togglealert(message):
        user_id = message.from_user.id
        args = message.text.split()[1:]
        if not args:
            _reply(message, "Usage: /togglealert ID")
            return
        try:
            aid = int(args[0])
        except ValueError:
            _reply(message, "Alert ID must be an integer.")
            return

        new_state = store.toggle_alert(user_id, aid)
        if new_state is None:
            _reply(message, f"Alert #{aid} not found.")
        else:
            state_str = "ENABLED 🟢" if new_state else "DISABLED 🔴"
            _reply(message, f"Alert #{aid} is now {state_str}")

    @bot.message_handler(commands=["removealert"])
    def cmd_removealert(message):
        user_id = message.from_user.id
        args = message.text.split()[1:]
        if not args:
            _reply(message, "Usage: /removealert ID")
            return
        try:
            aid = int(args[0])
        except ValueError:
            _reply(message, "Alert ID must be an integer.")
            return

        if store.remove_alert(user_id, aid):
            _reply(message, f"🗑️ Removed alert #{aid}")
        else:
            _reply(message, f"Alert #{aid} not found.")

    @bot.message_handler(commands=["status"])
    def cmd_status(message):
        user_id = message.from_user.id
        count = store.count_for_user(user_id)
        total_users = len(store.get_all_user_ids())
        _reply(
            message,
            f"Alerts for you: {count}\n"
            f"Bot is running.\n"
            f"Total users with alerts: {total_users}"
        )

    # Catch-all for unknown commands (optional nicety)
    @bot.message_handler(func=lambda m: m.text and m.text.startswith("/"))
    def cmd_unknown(message):
        _reply(message, "Unknown command. Try /start or /help.")

    return bot
