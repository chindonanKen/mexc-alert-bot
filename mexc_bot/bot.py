"""Telegram bot interface and command handlers.

Focus: dead-simple and fast alert creation.
Short aliases everywhere. Smart parsing so you can type quickly.

V3 commands (/af, /movers, /mw) are registered only when feature flags are on.
"""

import logging
import re
from typing import List, Optional, Tuple

import telebot

from .config import Settings
from .exchange import PriceProvider, normalize_futures_symbol, normalize_spot_symbol
from .monitor import PriceMonitor
from .storage import AlertStore

logger = logging.getLogger(__name__)

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
    """Spot normalization (V1 behavior)."""
    return normalize_spot_symbol(raw)


def _parse_alert_pairs(args: List[str], market: str = "spot") -> List[Tuple[str, float]]:
    """Turn ['BTC', '65000', 'eth', '2.4k'] into symbol/price pairs for the given market."""
    pairs: List[Tuple[str, float]] = []
    i = 0
    while i < len(args):
        if i + 1 >= len(args):
            break
        sym_raw = args[i]
        price_raw = args[i + 1]
        if market == "futures":
            symbol = normalize_futures_symbol(sym_raw)
        else:
            symbol = normalize_spot_symbol(sym_raw)
        price = _parse_price(price_raw)
        if price is not None and symbol:
            pairs.append((symbol, price))
        i += 2
    return pairs


def _market_tag(market: str) -> str:
    return "F" if market == "futures" else "S"


def create_bot(
    settings: Settings,
    store: AlertStore,
    price_provider: PriceProvider | None = None,
    monitor: PriceMonitor | None = None,
    futures_provider: PriceProvider | None = None,
    mover_store=None,
    mover_scanner=None,
) -> telebot.TeleBot:
    """Create and configure the Telegram bot with all handlers."""

    # No default parse_mode. Telegram Markdown treats "_" as italic, which breaks
    # futures symbols like BTC_USDT and long help text (400 can't parse entities).
    # Command replies = plain text. Alert fires use HTML in monitor/movers.
    bot = telebot.TeleBot(settings.telegram_bot_token, parse_mode=None)

    def _reply(message, text: str):
        try:
            bot.reply_to(message, text, parse_mode=None)
        except Exception as e:
            logger.warning(f"Failed to reply to user {message.from_user.id}: {e}")

    # ====================== HELP / START ======================
    @bot.message_handler(commands=["start", "help"])
    def cmd_start(message):
        lines = [
            "MEXC Alert Bot — fast & simple\n",
            "Quick add (recommended):",
            "/a BTC 65000          → spot BTCUSDT @ 65000",
            "/a eth 2.4k sol 145   → multiple in one go",
            "/a PEPE 0.000012",
            "(Multiple per symbol supported)\n",
            "Other fast commands:",
            "/l or /list             — your alerts",
            "/p BTC                  — current spot price",
            "/t 3 5 8                — toggle multiple IDs",
            "/r 3 5 8 or /r BTCUSDT  — remove by ID(s) or symbol",
            "/clearall confirm       — delete everything",
            "/disableall             — turn all off (keep list)",
            "/s or /status           — stats",
            "/d or /diag or /debug   — debug state",
        ]
        if settings.feature_futures_alerts:
            lines.extend(
                [
                    "",
                    "Futures (V3):",
                    "/af BTC 65000           → futures BTC-USDT perp @ 65000",
                    "/af eth 2.4k sol 145    → multiple futures",
                    "/p f BTC                → current futures price",
                ]
            )
        if settings.feature_mover_scanner:
            lines.extend(
                [
                    "",
                    "Downside movers (V3):",
                    "/movers on | off",
                    "/movers set 5 15        → 5% down in 15 minutes",
                    "/movers list",
                    "/mw BTC ETH SOL         → replace futures watchlist",
                    "/mw add BTC ETH         → add to watchlist",
                    "/mw remove BTC          → remove symbol(s)",
                    "/mw clear               → empty watchlist",
                ]
            )
        lines.extend(
            [
                "",
                "Alert numbers (#) are always the current position from the top of /l (1-based, no gaps).",
                "If you remove something above, the numbers below shift down automatically.",
                "Target alerts are one-shot: fire once on cross/band, then remove themselves.",
            ]
        )
        _reply(message, "\n".join(lines))

    # ====================== FAST ADD (SPOT — V1, unchanged) ======================
    @bot.message_handler(commands=["addalert", "a", "add", "alert"])
    def cmd_addalert(message):
        user_id = message.from_user.id
        args = message.text.split()[1:]

        if not args:
            _reply(message, "Quick usage:\n/a BTC 65000\n/a eth 2450 sol 140\n/a PEPE 0.00001")
            return

        pairs = _parse_alert_pairs(args, market="spot")

        if not pairs:
            _reply(message, "Couldn't parse. Try: /a BTC 65000   or   /a eth 2.4k")
            return

        successes = []
        errors = []
        for symbol, price in pairs:
            try:
                aid = store.add_alert(user_id, symbol, price, market="spot")
                successes.append((aid, symbol, price, "spot"))
            except Exception as e:
                logger.error(f"Failed adding alert for {symbol}: {e}")
                errors.append(symbol)

        if successes:
            if len(successes) == 1:
                aid, sym, pr, mkt = successes[0]
                _reply(message, f"✅ Created: {sym} @ ${pr} (#{aid}) [{_market_tag(mkt)}]")
            else:
                lines = ["✅ Created:"]
                for aid, sym, pr, mkt in successes:
                    lines.append(f"  {sym} @ ${pr} (#{aid}) [{_market_tag(mkt)}]")
                _reply(message, "\n".join(lines))

        if errors:
            _reply(message, "❌ Failed to create alert for: " + ", ".join(errors))

    # ====================== FUTURES ADD (V3, flag-gated) ======================
    if settings.feature_futures_alerts:

        @bot.message_handler(commands=["af", "addfutures", "futuresalert"])
        def cmd_addfutures(message):
            user_id = message.from_user.id
            args = message.text.split()[1:]
            if not args:
                _reply(message, "Futures usage:\n/af BTC 65000\n/af eth 2.4k sol 145")
                return
            pairs = _parse_alert_pairs(args, market="futures")
            if not pairs:
                _reply(message, "Couldn't parse. Try: /af BTC 65000")
                return
            successes = []
            errors = []
            for symbol, price in pairs:
                try:
                    aid = store.add_alert(user_id, symbol, price, market="futures")
                    successes.append((aid, symbol, price))
                except Exception as e:
                    logger.error(f"Failed adding futures alert for {symbol}: {e}")
                    errors.append(symbol)
            if successes:
                if len(successes) == 1:
                    aid, sym, pr = successes[0]
                    _reply(message, f"✅ Created futures: {sym} @ ${pr} (#{aid}) [F]")
                else:
                    lines = ["✅ Created futures:"]
                    for aid, sym, pr in successes:
                        lines.append(f"  {sym} @ ${pr} (#{aid}) [F]")
                    _reply(message, "\n".join(lines))
            if errors:
                _reply(message, "❌ Failed: " + ", ".join(errors))

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
            tag = _market_tag(a.get("market", "spot"))
            lines.append(f"#{a['id']} {a['symbol']} @ ${a['price']} [{tag}] {status}")
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
        current_alerts = store.get_user_alerts(user_id)
        id_to_symbol = {a["id"]: a["symbol"] for a in current_alerts}

        to_remove_ids: set[int] = set()
        for arg in args:
            try:
                to_remove_ids.add(int(arg))
                continue
            except ValueError:
                pass

            # Symbol: try spot form, futures form, and raw upper
            candidates = {
                _normalize_symbol(arg),
                normalize_futures_symbol(arg),
                arg.strip().upper(),
            }
            for aid, ss in id_to_symbol.items():
                if ss in candidates or ss == arg.strip().upper():
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
            _reply(
                message,
                f"You have {count} alerts.\n\n"
                f"To permanently delete all of them, reply with:\n"
                f"/clearall confirm",
            )
            return

        alerts = store.get_user_alerts(user_id)
        ids = [a["id"] for a in alerts]
        removed = store.remove_alerts_by_ids(user_id, ids)
        _reply(message, f"🗑️ Cleared all {removed} alerts.")

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
                health_line = (
                    f"\nLast poll: {h.get('last_poll_ms', '?')}ms | since success: {h.get('seconds_since_last_success', 0):.0f}s"
                    f" | tracked_last_prices: {h.get('tracked_last_prices', '?')}"
                    f" | futures_provider: {h.get('futures_provider', False)}"
                )
            except Exception:
                pass

        flags = (
            f"\nFlags: futures={settings.feature_futures_alerts} "
            f"movers={settings.feature_mover_scanner}"
        )

        mover_line = ""
        mscan = mover_scanner or getattr(bot, "_mover_scanner_ref", None)
        if mscan is not None:
            try:
                mh = mscan.get_health()
                mover_line = (
                    f"\nMovers: cycle={mh.get('last_cycle_ms')}ms "
                    f"series={mh.get('tracked_series')} fires={mh.get('fires_total')}"
                )
            except Exception:
                pass

        _reply(
            message,
            f"You have {count} alert(s)\n"
            f"Bot running. Total users: {total_users}{health_line}{flags}{mover_line}\n"
            f"Tolerance: {settings.alert_tolerance_percent*100:.3f}%"
        )

    # ====================== DIAG / DEBUG ======================
    @bot.message_handler(commands=["diag", "debug", "d"])
    def cmd_diag(message):
        user_id = message.from_user.id
        alerts = store.get_user_alerts(user_id)
        mon = monitor or getattr(bot, "_monitor_ref", None)
        debug = {}
        if mon is not None:
            try:
                debug = mon.get_user_debug_info(user_id)
            except Exception as e:
                debug = {"error": str(e)}
        lines = [f"Diag for you (user={user_id}):"]
        lines.append(f"Current alerts in store: {len(alerts)}")
        for a in alerts:
            lines.append(
                f"  visual#{a['id']} stable={a.get('stable_id','?')} "
                f"{a.get('market','spot')}:{a['symbol']}@{a['price']} en={a.get('enabled')}"
            )
        if debug:
            lines.append(f"tracked last_prices (by stable): {debug.get('last_prices_by_stable', {})}")
            lines.append(f"note: {debug.get('note', '')}")
        _reply(message, "\n".join(lines))

    # ====================== QUICK PRICE CHECK ======================
    @bot.message_handler(commands=["price", "p", "cur", "current"])
    def cmd_price(message):
        args = message.text.split()[1:]
        if not args:
            help_p = "Usage: /p BTC   or /p ETHUSDT"
            if settings.feature_futures_alerts:
                help_p += "\nFutures: /p f BTC   or /p f BTC_USDT"
            _reply(message, help_p)
            return

        want_futures = False
        if args[0].lower() in ("f", "fut", "futures", "perp"):
            want_futures = True
            args = args[1:]
            if not args:
                _reply(message, "Usage: /p f BTC")
                return

        if want_futures:
            if not settings.feature_futures_alerts or futures_provider is None:
                _reply(message, "Futures price lookup is disabled (FEATURE_FUTURES_ALERTS=false).")
                return
            symbol = normalize_futures_symbol(args[0])
            price = futures_provider.get_price(symbol)
            if price is None:
                _reply(message, f"Couldn't get futures price for {symbol}")
            else:
                _reply(message, f"{symbol} [F]: ${price:.8f}")
            return

        if price_provider is None:
            _reply(message, "Price lookup not available right now.")
            return

        symbol = _normalize_symbol(args[0])
        price = price_provider.get_price(symbol)
        if price is None:
            _reply(message, f"Couldn't get price for {symbol}")
        else:
            _reply(message, f"{symbol} [S]: ${price:.8f}")

    # ====================== MOVERS (V3, flag-gated) ======================
    if settings.feature_mover_scanner and mover_store is not None:

        @bot.message_handler(commands=["movers", "mover"])
        def cmd_movers(message):
            user_id = message.from_user.id
            args = message.text.split()[1:]
            if not args:
                _reply(
                    message,
                    "Movers (downside %):\n"
                    "/movers on | off\n"
                    "/movers set 5 15   → 5% down in 15 minutes\n"
                    "/movers list\n"
                    "/mw BTC ETH SOL    → set futures watchlist\n"
                    "/mw clear",
                )
                return

            sub = args[0].lower()
            if sub in ("on", "enable", "start"):
                s = mover_store.set_enabled(
                    user_id,
                    True,
                    settings.mover_threshold_percent,
                    settings.mover_lookback_seconds,
                )
                wl = mover_store.get_watchlist(user_id)
                extra = "" if wl else "\n⚠️ Watchlist empty — use /mw BTC ETH … first"
                _reply(
                    message,
                    f"Movers ON — {s['threshold_percent']}% down in "
                    f"{s['lookback_seconds']//60}m{extra}",
                )
                return

            if sub in ("off", "disable", "stop"):
                mover_store.set_enabled(
                    user_id,
                    False,
                    settings.mover_threshold_percent,
                    settings.mover_lookback_seconds,
                )
                _reply(message, "Movers OFF")
                return

            if sub == "set":
                if len(args) < 3:
                    _reply(message, "Usage: /movers set <percent> <minutes>\nExample: /movers set 5 15")
                    return
                try:
                    pct = float(args[1])
                    minutes = float(args[2])
                except ValueError:
                    _reply(message, "Need numbers. Example: /movers set 5 15")
                    return
                if pct <= 0 or pct > 90:
                    _reply(message, "Percent should be between 0 and 90 (e.g. 5 for 5%).")
                    return
                if minutes < 1 or minutes > 120:
                    _reply(message, "Minutes should be between 1 and 120.")
                    return
                s = mover_store.set_params(user_id, pct, int(minutes * 60))
                _reply(
                    message,
                    f"Movers params set: {s['threshold_percent']}% down in "
                    f"{s['lookback_seconds']//60}m "
                    f"({'ON' if s['enabled'] else 'OFF — /movers on to enable'})",
                )
                return

            if sub in ("list", "status", "show"):
                s = mover_store.get_settings(
                    user_id,
                    settings.mover_threshold_percent,
                    settings.mover_lookback_seconds,
                )
                wl = mover_store.get_watchlist(user_id)
                lines = [
                    f"Movers: {'ON' if s['enabled'] else 'OFF'}",
                    f"Threshold: {s['threshold_percent']}% down",
                    f"Lookback: {s['lookback_seconds']//60}m ({s['lookback_seconds']}s)",
                    f"Cooldown: {settings.mover_cooldown_seconds//60}m (global)",
                    f"Markets scanned: {settings.mover_markets}",
                    f"Watchlist ({len(wl)}):",
                ]
                if not wl:
                    lines.append("  (empty — /mw BTC ETH SOL)")
                else:
                    for it in wl:
                        lines.append(f"  {it['symbol']} [{_market_tag(it['market'])}]")
                _reply(message, "\n".join(lines))
                return

            _reply(message, "Unknown. Try /movers on | off | set | list")

        @bot.message_handler(commands=["mw", "moverwatch", "watchlist"])
        def cmd_mover_watch(message):
            user_id = message.from_user.id
            args = message.text.split()[1:]
            if not args:
                _reply(
                    message,
                    "Watchlist usage:\n"
                    "/mw BTC ETH SOL     — replace list (futures)\n"
                    "/mw add BTC ETH     — add without wiping others\n"
                    "/mw remove BTC ETH  — remove symbol(s)\n"
                    "/mw r BTC           — same as remove\n"
                    "/mw clear           — empty list\n"
                    "/movers list        — show list",
                )
                return

            sub = args[0].lower()

            if sub in ("clear", "reset", "none"):
                n = mover_store.clear_watchlist(user_id)
                _reply(message, f"Watchlist cleared ({n} removed).")
                return

            if sub in ("remove", "rm", "r", "del", "delete", "-"):
                rest = args[1:]
                if not rest:
                    _reply(message, "Usage: /mw remove BTC ETH\nOr: /mw r SOL")
                    return
                # Optional market: /mw remove f BTC  or  /mw remove s BTC
                market_filter = None
                if rest[0].lower() in ("f", "fut", "futures", "perp"):
                    market_filter = "futures"
                    rest = rest[1:]
                elif rest[0].lower() in ("s", "spot"):
                    market_filter = "spot"
                    rest = rest[1:]
                if not rest:
                    _reply(message, "Provide symbol(s) to remove. Example: /mw remove BTC")
                    return
                # Normalize each token both ways so BTC and BTC_USDT both match
                to_remove: list[str] = []
                for raw in rest:
                    to_remove.append(normalize_futures_symbol(raw))
                    to_remove.append(normalize_spot_symbol(raw))
                    to_remove.append(raw.strip().upper())
                # unique preserve order
                seen = set()
                uniq = []
                for s in to_remove:
                    if s and s not in seen:
                        seen.add(s)
                        uniq.append(s)
                n = mover_store.remove_from_watchlist(user_id, uniq, market=market_filter)
                wl = mover_store.get_watchlist(user_id)
                left = ", ".join(i["symbol"] for i in wl) if wl else "(empty)"
                _reply(message, f"Removed {n} watchlist row(s).\nLeft: {left}")
                return

            if sub in ("add", "+", "append"):
                rest = args[1:]
                market = "futures"
                if rest and rest[0].lower() in ("f", "fut", "futures", "perp"):
                    market = "futures"
                    rest = rest[1:]
                elif rest and rest[0].lower() in ("s", "spot"):
                    market = "spot"
                    rest = rest[1:]
                if not rest:
                    _reply(message, "Usage: /mw add BTC ETH")
                    return
                added = []
                for raw in rest:
                    sym = (
                        normalize_futures_symbol(raw)
                        if market == "futures"
                        else normalize_spot_symbol(raw)
                    )
                    if sym:
                        mover_store.add_watchlist(user_id, sym, market=market)
                        added.append(sym)
                wl = mover_store.get_watchlist(user_id)
                left = ", ".join(i["symbol"] for i in wl) if wl else "(empty)"
                _reply(
                    message,
                    f"Added {len(added)} [{_market_tag(market)}]: {', '.join(added)}\n"
                    f"Full list: {left}",
                )
                return

            # Default: replace entire list
            market = "futures"
            if args[0].lower() in ("f", "fut", "futures", "perp"):
                market = "futures"
                args = args[1:]
            elif args[0].lower() in ("s", "spot"):
                market = "spot"
                args = args[1:]

            if not args:
                _reply(message, "Provide symbols. Example: /mw BTC ETH SOL")
                return

            items = []
            for raw in args:
                if market == "futures":
                    sym = normalize_futures_symbol(raw)
                else:
                    sym = normalize_spot_symbol(raw)
                if sym:
                    items.append({"symbol": sym, "market": market})
            n = mover_store.set_watchlist(user_id, items)
            shown = ", ".join(f"{i['symbol']}" for i in items)
            _reply(message, f"Watchlist replaced ({n}) [{_market_tag(market)}]: {shown}")

    # Catch-all unknown
    @bot.message_handler(func=lambda m: m.text and m.text.startswith("/"))
    def cmd_unknown(message):
        _reply(message, "Unknown. Try /help or just /a BTC 65000 (fastest)")

    return bot
