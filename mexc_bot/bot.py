"""Telegram bot interface and command handlers.

Focus: dead-simple and fast alert creation.
Short aliases everywhere. Smart parsing so you can type quickly.
"""

import logging
import re
from typing import List, Tuple

import telebot

from .config import Settings
from .exchange import PriceProvider
from .monitor import PriceMonitor
from .storage import AlertStore

logger = logging.getLogger(__name__)

# Common quote suffixes we auto-append for lazy typing (BTC → BTCUSDT)
COMMON_QUOTES = ("USDT", "USDC", "BTC", "ETH", "BUSD", "FDUSD")

# Price suffix parsing: 65000, 65k, 2.45k, 1.2m, 0.00045 etc.
PRICE_RE = re.compile(r"^([0-9]*\.?[0-9]+)\s*([kKmM])?$")


def _parse_price(value: str) -> float | None:
    """Support 65000, 65k, 2.4k, 1.2m etc."""
    v = value.strip().lower().replace(",", "")
    m = PRICE_RE.match(v)
    if not m:
        return None
    num = float(m.group(1))
    suffix = m.group(2)
    if suffix == "k":
        num *= 1000
    elif suffix == "m":
        num *= 1_000_000
    return num


def _normalize_symbol(raw: str) -> str:
    """Uppercase + smart USDT suffix if it looks like a bare base asset."""
    s = raw.strip().upper().replace("-", "").replace("/", "").replace(" ", "")
    if not s:
        return s
    # Already has a quote suffix?
    for q in COMMON_QUOTES:
        if s.endswith(q) and len(s) > len(q):
            return s
    # Looks like bare base (letters only, no numbers at end usually) → append USDT
    if s.isalpha() or (s[:-1].isalpha() and s[-1].isdigit()):  # e.g. PEPE2
        return s + "USDT"
    return s


def _parse_alert_pairs(args: List[str]) -> List[Tuple[str, float]]:
    """Turn ['BTC', '65000', 'eth', '2.4k'] into [('BTCUSDT', 65000.0), ('ETHUSDT', 2400.0)]"""
    pairs: List[Tuple[str, float]] = []
    i = 0
    while i < len(args):
        if i + 1 >= len(args):
            break
        sym_raw = args[i]
        price_raw = args[i + 1]
        symbol = _normalize_symbol(sym_raw)
        price = _parse_price(price_raw)
        if price is not None and symbol:
            pairs.append((symbol, price))
        i += 2
    return pairs


def create_bot(
    settings: Settings,
    store: AlertStore,
    price_provider: PriceProvider | None = None,
    monitor: PriceMonitor | None = None,
) -> telebot.TeleBot:
    """Create and configure the Telegram bot with all handlers."""

    bot = telebot.TeleBot(settings.telegram_bot_token, parse_mode="Markdown")

    def _reply(message, text: str):
        try:
            bot.reply_to(message, text)
        except Exception as e:
            logger.warning(f"Failed to reply to user {message.from_user.id}: {e}")

    # ====================== HELP / START ======================
    @bot.message_handler(commands=["start", "help"])
    def cmd_start(message):
        text = (
            "MEXC Alert Bot — fast & simple\n\n"
            "Quick add (recommended):\n"
            "/a BTC 65000          → BTCUSDT @ 65000\n"
            "/a eth 2.4k sol 145   → multiple in one go\n"
            "/a PEPE 0.000012\n\n"
            "Other fast commands:\n"
            "/l or /list             — your alerts\n"
            "/p BTC                  — current price\n"
            "/t 3 5 8                — toggle multiple IDs\n"
            "/r 3 5 8 or /r BTCUSDT  — remove by ID(s) or symbol\n"
            "/clearall confirm       — delete *everything*\n"
            "/disableall             — turn all off (keep list)\n"
            "/s or /status           — stats\n\n"
            "Alert numbers (#) are *always* the current position from the top of /l (1-based, no gaps).\n"
            "If you remove something above, the numbers below shift down automatically.\n"
            "Alerts are one-shot: fires once when the price crosses your target (either direction since last check) or lands in the tolerance band, then removes itself."
        )
        _reply(message, text)

    # ====================== FAST ADD ======================
    @bot.message_handler(commands=["addalert", "a", "add", "alert"])
    def cmd_addalert(message):
        user_id = message.from_user.id
        args = message.text.split()[1:]

        if not args:
            _reply(message, "Quick usage:\n/a BTC 65000\n/a eth 2450 sol 140\n/a PEPE 0.00001")
            return

        pairs = _parse_alert_pairs(args)

        if not pairs:
            _reply(message, "Couldn't parse. Try: /a BTC 65000   or   /a eth 2.4k")
            return

        successes = []
        errors = []
        for symbol, price in pairs:
            try:
                aid = store.add_alert(user_id, symbol, price)
                successes.append((aid, symbol, price))
            except Exception as e:
                logger.error(f"Failed adding alert for {symbol}: {e}")
                errors.append(symbol)

        # Extremely simple and honest feedback
        if successes:
            if len(successes) == 1:
                aid, sym, pr = successes[0]
                _reply(message, f"✅ Created: {sym} @ ${pr} (#{aid})")
            else:
                lines = ["✅ Created:"]
                for aid, sym, pr in successes:
                    lines.append(f"  {sym} @ ${pr} (#{aid})")
                _reply(message, "\n".join(lines))

        if errors:
            _reply(message, "❌ Failed to create alert for: " + ", ".join(errors))

    # ====================== LIST (short) ======================
    @bot.message_handler(commands=["listalerts", "l", "list", "alerts"])
    def cmd_listalerts(message):
        user_id = message.from_user.id
        alerts = store.get_user_alerts(user_id)
        if not alerts:
            _reply(message, "No alerts. Use /a BTC 65000 (super quick)")
            return

        enabled = sum(1 for a in alerts if a.get("enabled"))
        lines = [f"Your Alerts — {len(alerts)} total ({enabled} enabled)"]
        for a in alerts:
            status = "🟢" if a.get("enabled") else "🔴"
            lines.append(f"#{a['id']} {a['symbol']} @ ${a['price']} {status}")
        _reply(message, "\n".join(lines))

    # ====================== TOGGLE (short) ======================
    @bot.message_handler(commands=["togglealert", "t", "toggle"])
    def cmd_togglealert(message):
        user_id = message.from_user.id
        args = message.text.split()[1:]
        if not args:
            _reply(message, "Usage: /t 3 5 12   (toggle multiple alerts)")
            return

        results = []
        for arg in args:
            try:
                aid = int(arg)
            except ValueError:
                results.append(f"{arg} (not a number)")
                continue

            new_state = store.toggle_alert(user_id, aid)
            if new_state is None:
                results.append(f"#{aid} (not found)")
            else:
                results.append(f"#{aid} {'🟢 ON' if new_state else '🔴 OFF'}")

        _reply(message, "Toggled: " + " | ".join(results))

    # ====================== REMOVE (short) ======================
    @bot.message_handler(commands=["removealert", "r", "remove", "del", "delete"])
    def cmd_removealert(message):
        user_id = message.from_user.id
        args = message.text.split()[1:]
        if not args:
            _reply(message, "Usage: /r 3 5 12   or   /r BTCUSDT")
            return

        # Snapshot current state so we can map symbols to their *current* ids
        # without shift issues when mixing IDs and symbols or multiple removes.
        current_alerts = store.get_user_alerts(user_id)
        id_to_symbol = {a["id"]: a["symbol"] for a in current_alerts}

        to_remove_ids: set[int] = set()
        for arg in args:
            try:
                to_remove_ids.add(int(arg))
                continue
            except ValueError:
                pass

            # Symbol: add all current ids that match this symbol
            sym = _normalize_symbol(arg)
            for aid, ss in id_to_symbol.items():
                if ss == sym:
                    to_remove_ids.add(aid)

        if not to_remove_ids:
            _reply(message, "Nothing to remove.")
            return

        removed = store.remove_alerts_by_ids(user_id, list(to_remove_ids))
        _reply(message, f"🗑️ Removed {removed} alert(s). (based on positions/symbols at time of command)")

    # ====================== CLEAR ALL (with safety) ======================
    @bot.message_handler(commands=["clearall", "removeall", "clear"])
    def cmd_clearall(message):
        user_id = message.from_user.id
        args = [a.lower() for a in message.text.split()[1:]]
        if not args or "confirm" not in args:
            count = store.count_for_user(user_id)
            _reply(message, f"You have {count} alerts.\n\nTo *permanently delete all of them*, reply with:\n`/clearall confirm`")
            return

        alerts = store.get_user_alerts(user_id)
        ids = [a["id"] for a in alerts]
        removed = store.remove_alerts_by_ids(user_id, ids)
        _reply(message, f"🗑️ *Cleared all {removed} alerts.*")

    # ====================== DISABLE ALL ======================
    @bot.message_handler(commands=["disableall"])
    def cmd_disableall(message):
        user_id = message.from_user.id
        changed = store.disable_all(user_id)
        if changed > 0:
            _reply(message, f"Disabled {changed} alerts. They are still in your list (use /l).")
        else:
            _reply(message, "No enabled alerts to disable.")

    # ====================== STATUS (enhanced with health) ======================
    @bot.message_handler(commands=["status", "s"])
    def cmd_status(message):
        user_id = message.from_user.id
        count = store.count_for_user(user_id)
        total_users = len(store.get_all_user_ids())

        health_line = ""
        mon = monitor or getattr(bot, "_monitor_ref", None)
        if mon is not None:
            try:
                h = mon.get_health()
                health_line = f"\nLast poll: {h.get('last_poll_ms', '?')}ms | since success: {h.get('seconds_since_last_success', 0):.0f}s"
            except Exception:
                pass

        _reply(
            message,
            f"You have {count} alert(s)\n"
            f"Bot running. Total users: {total_users}{health_line}\n"
            f"Tolerance: {settings.alert_tolerance_percent*100:.3f}%"
        )

    # ====================== QUICK PRICE CHECK ======================
    @bot.message_handler(commands=["price", "p", "cur", "current"])
    def cmd_price(message):
        if price_provider is None:
            _reply(message, "Price lookup not available right now.")
            return

        args = message.text.split()[1:]
        if not args:
            _reply(message, "Usage: /p BTC   or /p ETHUSDT")
            return

        symbol = _normalize_symbol(args[0])
        price = price_provider.get_price(symbol)
        if price is None:
            _reply(message, f"Couldn't get price for {symbol}")
        else:
            _reply(message, f"{symbol}: ${price:.8f}")

    # Catch-all unknown
    @bot.message_handler(func=lambda m: m.text and m.text.startswith("/"))
    def cmd_unknown(message):
        _reply(message, "Unknown. Try /help or just /a BTC 65000 (fastest)")

    return bot
