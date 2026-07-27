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
from .exchange import (
    PriceProvider,
    normalize_futures_symbol,
    normalize_spot_symbol,
    resolve_futures_symbol,
)
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


def _resolve_futures(raw: str, futures_provider: PriceProvider | None) -> str | None:
    """Resolve user input to a live futures contract when possible."""
    if futures_provider is not None and hasattr(futures_provider, "resolve_symbol"):
        try:
            resolved = futures_provider.resolve_symbol(raw)  # type: ignore[attr-defined]
            if resolved:
                return str(resolved).upper()
        except Exception as e:
            logger.warning(f"futures resolve_symbol failed for {raw!r}: {e}")
    # Offline fallback (no live book): crypto-style BASE_USDT only
    return normalize_futures_symbol(raw) or None


def _parse_alert_pairs(
    args: List[str],
    market: str = "spot",
    futures_provider: PriceProvider | None = None,
) -> Tuple[List[Tuple[str, float]], List[str]]:
    """Turn args into symbol/price pairs.

    Returns (success_pairs, failed_symbol_raws).
    Futures symbols are resolved against the live contract list when possible.
    """
    pairs: List[Tuple[str, float]] = []
    failed: List[str] = []
    i = 0
    while i < len(args):
        if i + 1 >= len(args):
            break
        sym_raw = args[i]
        price_raw = args[i + 1]
        price = _parse_price(price_raw)
        if price is None:
            failed.append(sym_raw)
            i += 2
            continue
        if market == "futures":
            symbol = _resolve_futures(sym_raw, futures_provider)
            if not symbol:
                failed.append(sym_raw)
                i += 2
                continue
            # If we have a live provider, require the symbol to actually price
            if futures_provider is not None and hasattr(futures_provider, "resolve_symbol"):
                # re-check: normalize-only fallback may invent FOO_USDT that does not exist
                live = futures_provider.resolve_symbol(sym_raw)  # type: ignore[attr-defined]
                if not live:
                    failed.append(sym_raw)
                    i += 2
                    continue
                symbol = str(live).upper()
        else:
            symbol = normalize_spot_symbol(sym_raw)
        if symbol:
            pairs.append((symbol, price))
        else:
            failed.append(sym_raw)
        i += 2
    return pairs, failed


def _market_tag(market: str) -> str:
    return "F" if market == "futures" else "S"


def _format_watchlist(wl: list) -> list[str]:
    """Group watchlist lines by market for easy scanning."""
    if not wl:
        return ["  (empty)"]
    futures = [i for i in wl if i.get("market") == "futures"]
    spot = [i for i in wl if i.get("market") == "spot"]
    other = [i for i in wl if i.get("market") not in ("futures", "spot")]
    lines: list[str] = []
    if futures:
        lines.append(f"  Futures [F] ({len(futures)}):")
        for it in futures:
            lines.append(f"    {it['symbol']}")
    if spot:
        lines.append(f"  Spot [S] ({len(spot)}):")
        for it in spot:
            lines.append(f"    {it['symbol']}")
    if other:
        lines.append(f"  Other ({len(other)}):")
        for it in other:
            lines.append(f"    {it['symbol']} [{it.get('market')}]")
    return lines


def _format_mw_with_heat(
    wl: list,
    settings: Settings,
    mover_store,
    user_id: int,
    mover_scanner=None,
) -> list[str]:
    """Watchlist plus optional live heat rank (does not require prompting for boards)."""
    lines = _format_watchlist(wl)
    if not wl or not getattr(settings, "mover_heat_on_mw", True):
        return lines
    scanner = mover_scanner or None
    if scanner is None or not hasattr(scanner, "history"):
        return lines
    try:
        from .movers.heat import format_heat_plain, heat_snapshot

        s = mover_store.get_settings(
            user_id,
            settings.mover_threshold_percent,
            settings.mover_lookback_seconds,
        )
        lookback = float(s["lookback_seconds"])
        thr = float(s["threshold_percent"])
        breadth = getattr(settings, "mover_heat_breadth_pct", None)
        if breadth is None:
            breadth = max(0.5, thr * 0.6)
        board = heat_snapshot(
            scanner.history,
            wl,
            lookback,
            panic_per_min=float(getattr(settings, "mover_velocity_panic", 2.0)),
            fast_per_min=float(getattr(settings, "mover_velocity_fast", 0.8)),
            breadth_pct=float(breadth),
        )
        lines.append("")
        lines.extend(format_heat_plain(board, top_n=int(getattr(settings, "mover_heat_top_n", 5))))
        if getattr(settings, "mover_heat_auto", True):
            lines.append(
                f"(Auto board ON when ≥{getattr(settings, 'mover_heat_breadth_min', 3)} "
                f"names dump — no need to type /mw in a panic)"
            )
    except Exception:
        pass
    return lines


def _parse_mw_token(raw: str, default_market: str = "futures") -> tuple[str, str] | None:
    """
    Parse one watchlist token into (symbol_raw, market).

    Supports:
      SIREN           → default market
      s:SIREN  SIREN:s  SIREN:spot
      f:BTC    BTC:f    BTC:futures
    """
    s = (raw or "").strip()
    if not s:
        return None
    low = s.lower()
    market = default_market
    body = s

    if low.startswith("spot:"):
        market, body = "spot", s.split(":", 1)[1]
    elif low.startswith("futures:") or low.startswith("fut:") or low.startswith("perp:"):
        market, body = "futures", s.split(":", 1)[1]
    elif low.startswith("s:"):
        market, body = "spot", s.split(":", 1)[1]
    elif low.startswith("f:"):
        market, body = "futures", s.split(":", 1)[1]
    elif ":" in s:
        left, right = s.rsplit(":", 1)
        r = right.lower()
        if r in ("s", "spot"):
            market, body = "spot", left
        elif r in ("f", "fut", "futures", "perp"):
            market, body = "futures", left

    body = body.strip()
    if not body:
        return None
    return body, market


def _mw_resolve_symbol(
    raw_sym: str,
    market: str,
    futures_provider: PriceProvider | None,
) -> str | None:
    """Resolve a symbol for the given market; None if unknown on futures book."""
    if market == "futures":
        if futures_provider is not None and hasattr(futures_provider, "resolve_symbol"):
            live = futures_provider.resolve_symbol(raw_sym)  # type: ignore[attr-defined]
            if live:
                return str(live).upper()
            return None
        return normalize_futures_symbol(raw_sym) or None
    return normalize_spot_symbol(raw_sym) or None


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
                    "/af BTC 65000           → crypto perp",
                    "/af TSLA 250            → stock perp (auto-resolves TSLASTOCK…)",
                    "/af eth 2.4k sol 145    → multiple futures",
                    "/p f BTC  |  /p f TSLA  → futures price (short names OK)",
                ]
            )
        if settings.feature_mover_scanner:
            lines.extend(
                [
                    "",
                    "Downside movers (V3) — spot + futures can mix:",
                    "/movers on | off | set 5 15 | list",
                    "/mw                     → show watchlist",
                    "/mw add f BTC ETH       → add futures",
                    "/mw add s SIREN         → add spot (own book)",
                    "/mw add f:BTC s:SIREN   → mix in one go",
                    "/mw remove SIREN        → remove (either market)",
                    "/mw clear",
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

        pairs, _failed = _parse_alert_pairs(args, market="spot")

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
                _reply(
                    message,
                    "Futures usage:\n"
                    "/af BTC 65000\n"
                    "/af TSLA 250\n"
                    "/af zhipu 200 samsung 180\n"
                    "(Short names auto-resolve to the real MEXC contract)",
                )
                return
            pairs, failed = _parse_alert_pairs(
                args, market="futures", futures_provider=futures_provider
            )
            if not pairs and failed:
                _reply(
                    message,
                    "Couldn't resolve: "
                    + ", ".join(failed)
                    + "\nTip: try /p f SYMBOL to see if it lists on MEXC futures.",
                )
                return
            if not pairs:
                _reply(message, "Couldn't parse. Try: /af BTC 65000  or  /af TSLA 250")
                return
            successes = []
            errors = list(failed)
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
                _reply(message, "❌ Could not add/resolve: " + ", ".join(errors))

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
                    f"series={mh.get('tracked_series')} fires={mh.get('fires_total')} "
                    f"anchors={mh.get('active_anchors', 0)} "
                    f"min_gap={mh.get('min_gap_seconds', '?')}s"
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
            raw = args[0]
            symbol = _resolve_futures(raw, futures_provider)
            price = futures_provider.get_price(raw)
            if price is None:
                from .exchange import futures_symbol_candidates

                cands = futures_symbol_candidates(raw)[:8]
                tried = ", ".join(cands) if cands else (symbol or normalize_futures_symbol(raw))
                _reply(
                    message,
                    f"Couldn't get futures price for {raw}.\n"
                    f"Tried: {tried}\n"
                    f"If the chart is open, copy the exact contract id from MEXC "
                    f"(e.g. TESLA_USDT or TSLAUSDT) and use /p f THAT_ID.",
                )
            else:
                # Show resolved contract id so user learns the real name
                resolved = symbol or normalize_futures_symbol(raw)
                if resolved and resolved.upper() != raw.strip().upper():
                    _reply(message, f"{resolved} [F]: ${price:.8f}\n(from /p f {raw})")
                else:
                    _reply(message, f"{resolved} [F]: ${price:.8f}")
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
                    "Movers (downside % on your watchlist):\n"
                    "/movers on | off\n"
                    "/movers set 5 15   → 5% down in 15 minutes\n"
                    "/movers list\n\n"
                    "Watchlist (spot + futures can mix):\n"
                    "/mw                 → show list\n"
                    "/mw add f BTC ETH   → futures\n"
                    "/mw add s SIREN     → spot only (own price)\n"
                    "/mw add f:BTC s:SIREN\n"
                    "/mw remove SIREN\n"
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
                extra = "" if wl else "\n⚠️ Watchlist empty — /mw add f BTC  or  /mw add s SIREN"
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
                    f"Re-arm: step-down from last fire (+{settings.mover_recovery_percent:g}% bounce clears)",
                    f"Min gap between fires: {settings.mover_cooldown_seconds}s (not a long mute)",
                    f"Watchlist ({len(wl)}) — mixed spot/futures OK:",
                ]
                mscan = mover_scanner or getattr(bot, "_mover_scanner_ref", None)
                lines.extend(
                    _format_mw_with_heat(wl, settings, mover_store, user_id, mscan)
                )
                if not wl:
                    lines.append("Add: /mw add f BTC   or   /mw add s SIREN")
                _reply(message, "\n".join(lines))
                return

            _reply(message, "Unknown. Try /movers on | off | set | list")

        @bot.message_handler(commands=["mw", "moverwatch", "watchlist"])
        def cmd_mover_watch(message):
            user_id = message.from_user.id
            args = message.text.split()[1:]

            def show_list(header: str = "") -> None:
                s = mover_store.get_settings(
                    user_id,
                    settings.mover_threshold_percent,
                    settings.mover_lookback_seconds,
                )
                wl = mover_store.get_watchlist(user_id)
                lines = []
                if header:
                    lines.append(header)
                lines.append(
                    f"Movers: {'ON' if s['enabled'] else 'OFF'} | "
                    f"{s['threshold_percent']}% / {s['lookback_seconds']//60}m"
                )
                lines.append(f"Watchlist ({len(wl)}):")
                mscan = mover_scanner or getattr(bot, "_mover_scanner_ref", None)
                lines.extend(
                    _format_mw_with_heat(wl, settings, mover_store, user_id, mscan)
                )
                _reply(message, "\n".join(lines))

            if not args:
                show_list(
                    "Watchlist help:\n"
                    "/mw add f BTC ETH     — futures\n"
                    "/mw add s SIREN       — spot (different book)\n"
                    "/mw add f:BTC s:SIREN — mix\n"
                    "/mw remove SIREN\n"
                    "/mw clear\n"
                    "(Prefer add/remove — they do not wipe the other market)\n"
                )
                return

            sub = args[0].lower()

            if sub in ("clear", "reset", "none"):
                n = mover_store.clear_watchlist(user_id)
                _reply(message, f"Watchlist cleared ({n} removed).")
                return

            if sub in ("list", "show", "ls"):
                show_list()
                return

            if sub in ("remove", "rm", "r", "del", "delete", "-"):
                rest = args[1:]
                if not rest:
                    _reply(message, "Usage: /mw remove SIREN\n/mw remove s SIREN\n/mw remove f BTC")
                    return
                market_filter = None
                if rest[0].lower() in ("f", "fut", "futures", "perp"):
                    market_filter = "futures"
                    rest = rest[1:]
                elif rest[0].lower() in ("s", "spot"):
                    market_filter = "spot"
                    rest = rest[1:]
                if not rest:
                    _reply(message, "Provide symbol(s) to remove. Example: /mw remove SIREN")
                    return
                to_remove: list[str] = []
                for raw in rest:
                    parsed = _parse_mw_token(raw, default_market="futures")
                    body = parsed[0] if parsed else raw
                    to_remove.append(body.strip().upper())
                    to_remove.append(normalize_futures_symbol(body))
                    to_remove.append(normalize_spot_symbol(body))
                    resolved = _resolve_futures(body, futures_provider)
                    if resolved:
                        to_remove.append(resolved)
                    if futures_provider is not None and hasattr(futures_provider, "resolve_symbol"):
                        live = futures_provider.resolve_symbol(body)  # type: ignore[attr-defined]
                        if live:
                            to_remove.append(str(live).upper())
                    if parsed and parsed[1] == "spot" and market_filter is None:
                        # token was s:SIREN — prefer spot-only remove for that token
                        pass
                seen: set[str] = set()
                uniq = []
                for s in to_remove:
                    if s and s not in seen:
                        seen.add(s)
                        uniq.append(s)

                # If any token explicitly marked spot/futures, remove with that filter per token
                n = 0
                for raw in rest:
                    parsed = _parse_mw_token(raw, default_market=market_filter or "futures")
                    if not parsed:
                        continue
                    body, mkt = parsed
                    cands = [
                        body.strip().upper(),
                        normalize_spot_symbol(body),
                        normalize_futures_symbol(body),
                    ]
                    res = _mw_resolve_symbol(body, mkt, futures_provider)
                    if res:
                        cands.append(res)
                    # Also try other market resolve for untagged global remove
                    filt = market_filter if market_filter else (mkt if raw.lower().startswith(("s:", "f:", "spot:", "fut")) or ":" in raw else None)
                    n += mover_store.remove_from_watchlist(user_id, cands, market=filt)

                if n == 0:
                    # fallback bulk
                    n = mover_store.remove_from_watchlist(user_id, uniq, market=market_filter)
                show_list(f"Removed {n} row(s).")
                return

            if sub in ("add", "+", "append"):
                rest = args[1:]
                default_market = "futures"
                if rest and rest[0].lower() in ("f", "fut", "futures", "perp"):
                    default_market = "futures"
                    rest = rest[1:]
                elif rest and rest[0].lower() in ("s", "spot"):
                    default_market = "spot"
                    rest = rest[1:]
                if not rest:
                    _reply(
                        message,
                        "Usage:\n"
                        "/mw add f BTC ETH\n"
                        "/mw add s SIREN\n"
                        "/mw add f:BTC s:SIREN",
                    )
                    return
                added: list[str] = []
                failed: list[str] = []
                for raw in rest:
                    parsed = _parse_mw_token(raw, default_market=default_market)
                    if not parsed:
                        failed.append(raw)
                        continue
                    body, market = parsed
                    sym = _mw_resolve_symbol(body, market, futures_provider)
                    if not sym:
                        failed.append(f"{raw}({market})")
                        continue
                    mover_store.add_watchlist(user_id, sym, market=market)
                    added.append(f"{sym} [{_market_tag(market)}]")
                header = f"Added {len(added)}: {', '.join(added) if added else '(none)'}"
                if failed:
                    header += f"\nCould not resolve: {', '.join(failed)}"
                show_list(header)
                return

            # Bare symbols: REPLACE entire list (destructive) — prefer /mw add
            default_market = "futures"
            if args[0].lower() in ("f", "fut", "futures", "perp"):
                default_market = "futures"
                args = args[1:]
            elif args[0].lower() in ("s", "spot"):
                default_market = "spot"
                args = args[1:]

            if not args:
                _reply(message, "Example: /mw add f BTC   or   /mw add s SIREN")
                return

            items = []
            failed = []
            for raw in args:
                parsed = _parse_mw_token(raw, default_market=default_market)
                if not parsed:
                    failed.append(raw)
                    continue
                body, market = parsed
                sym = _mw_resolve_symbol(body, market, futures_provider)
                if not sym:
                    failed.append(f"{raw}({market})")
                    continue
                items.append({"symbol": sym, "market": market})
            n = mover_store.set_watchlist(user_id, items)
            header = (
                f"⚠️ Replaced entire watchlist ({n} rows). "
                f"Tip: use /mw add so you do not wipe the other market."
            )
            if failed:
                header += f"\nCould not resolve: {', '.join(failed)}"
            show_list(header)

    # Catch-all unknown
    @bot.message_handler(func=lambda m: m.text and m.text.startswith("/"))
    def cmd_unknown(message):
        _reply(message, "Unknown. Try /help or just /a BTC 65000 (fastest)")

    return bot
