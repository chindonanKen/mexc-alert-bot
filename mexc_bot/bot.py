"""Telegram bot interface and command handlers.

Focus: dead-simple and fast alert creation.
Short aliases everywhere. Smart parsing so you can type quickly.

V3 commands (/af, /movers, /mw) are registered only when feature flags are on.
"""

from __future__ import annotations

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
    event_store=None,
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
        # Progressive disclosure — assistant first, power commands secondary.
        # See docs/ASSISTANT_UX.md
        lines = [
            "MEXC trading assistant\n",
            "PRIMARY — after a dump alert:",
            "  Tap Took / Skip / Later on the message (no typing)",
            "  Or type: took · skip · later · brief · coach\n",
            "HOME: /desk",
            "Status: /s\n",
            "SENSORS (when you need levels):",
            "/a BTC 65000     spot target",
            "/l  /p BTC       list / price",
        ]
        if settings.feature_futures_alerts:
            lines.append("/af BTC 65000 · /p f TSLA   futures targets")
        if settings.feature_mover_scanner:
            lines.append("/movers on · /mw add f BTC   downside movers")
        if getattr(settings, "feature_learning", False):
            lines.extend(
                [
                    "",
                    "Assistant memory is ON.",
                    "Power tools (optional): /events /brief /coach /j /trade",
                ]
            )
        lines.extend(
            [
                "",
                "Target alerts are one-shot (fire → remove).",
                "Full command list is not the main UX — use /desk.",
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
            f"movers={settings.feature_mover_scanner} "
            f"learning={getattr(settings, 'feature_learning', False)}"
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

        learning_line = ""
        if getattr(settings, "feature_learning", False) and event_store is not None:
            try:
                since = __import__("time").time() - 86400
                n24 = event_store.count_events_since(user_id, since)
                learning_line = f"\nLearning: events_24h={n24}"
            except Exception:
                learning_line = "\nLearning: on"

        _reply(
            message,
            f"You have {count} alert(s)\n"
            f"Bot running. Total users: {total_users}{health_line}{flags}"
            f"{mover_line}{learning_line}\n"
            f"Tolerance: {settings.alert_tolerance_percent*100:.3f}%"
        )

    # ====================== ASSISTANT UX + LEARNING (V4, flag-gated) ======================
    def _learning_enabled() -> bool:
        return bool(getattr(settings, "feature_learning", False) and event_store is not None)

    def _label_event_id(
        user_id: int,
        event_id: int,
        *,
        action: Optional[str] = None,
        bounce_quality: Optional[str] = None,
        behavior: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> bool:
        if not event_store or event_id <= 0:
            return False
        return event_store.label_event(
            event_id,
            user_id,
            action=action,
            bounce_quality=bounce_quality,
            behavior=behavior,
            notes=notes,
        )

    @bot.message_handler(commands=["desk", "home", "assistant"])
    def cmd_desk(message):
        from .assistant.ux import desk_text

        recent_n = 0
        open_n = 0
        if _learning_enabled():
            try:
                recent_n = len(event_store.recent_events(message.from_user.id, limit=12))
                open_n = len(event_store.journal_list(message.from_user.id, open_only=True))
            except Exception:
                pass
        _reply(
            message,
            desk_text(
                learning_on=_learning_enabled(),
                recent_n=recent_n,
                open_trades_n=open_n,
            ),
        )

    @bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("L:"))
    def cb_learning_label(call):
        """One-tap Took/Skip/Later (and bounce) — primary assistant UX."""
        from .assistant.ux import bounce_keyboard, parse_callback

        if not _learning_enabled():
            try:
                bot.answer_callback_query(call.id, "Learning is off")
            except Exception:
                pass
            return
        parsed = parse_callback(call.data or "")
        if not parsed:
            try:
                bot.answer_callback_query(call.id, "Unknown button")
            except Exception:
                pass
            return
        action, eid = parsed
        user_id = call.from_user.id
        try:
            if action == "took":
                ok = _label_event_id(user_id, eid, action="took")
                bot.answer_callback_query(call.id, "Marked TOOK" if ok else "Failed")
                if ok:
                    try:
                        bot.send_message(
                            call.message.chat.id,
                            f"Logged TOOK on event #{eid}. Optional bounce quality:",
                            reply_markup=bounce_keyboard(eid),
                        )
                    except Exception as e:
                        logger.warning("bounce keyboard send failed: %s", e)
            elif action == "skip":
                ok = _label_event_id(user_id, eid, action="skip")
                bot.answer_callback_query(call.id, "Marked SKIP" if ok else "Failed")
                if ok:
                    bot.send_message(call.message.chat.id, f"Logged SKIP on event #{eid}.")
            elif action == "watch":
                ok = _label_event_id(user_id, eid, action="watch")
                bot.answer_callback_query(call.id, "Watching" if ok else "Failed")
                if ok:
                    bot.send_message(call.message.chat.id, f"Watching event #{eid}.")
            elif action.startswith("bounce_"):
                quality = action.replace("bounce_", "", 1)  # strong|weak|none|failed
                if quality == "failed":
                    quality = "failed"
                ok = _label_event_id(user_id, eid, bounce_quality=quality)
                bot.answer_callback_query(
                    call.id, f"Bounce={quality}" if ok else "Failed"
                )
                if ok:
                    bot.send_message(
                        call.message.chat.id,
                        f"Bounce quality on #{eid}: {quality}",
                    )
            else:
                bot.answer_callback_query(call.id, "Unknown")
        except Exception as e:
            logger.error("callback learning label failed: %s", e)
            try:
                bot.answer_callback_query(call.id, "Error")
            except Exception:
                pass

    @bot.message_handler(commands=["events"])
    def cmd_events(message):
        if not _learning_enabled():
            _reply(message, "Learning is off. Set FEATURE_LEARNING=true and restart.")
            return
        args = message.text.split()[1:]
        limit = 15
        if args:
            try:
                limit = int(args[0])
            except ValueError:
                pass
        rows = event_store.recent_events(message.from_user.id, limit=limit)
        if not rows:
            _reply(
                message,
                "No fires logged yet. When a mover dumps, use the buttons on the alert.",
            )
            return
        lines = [f"Last {len(rows)} event(s):"]
        for e in rows:
            band = e.get("velocity_band") or "—"
            drop = e.get("drop_pct")
            drop_s = f"{drop:.1f}%" if drop is not None else "?"
            act = e.get("last_action") or "unlabeled"
            lines.append(
                f"#{e['id']} [{(e.get('market') or '?')[:1].upper()}] "
                f"{e['symbol']} {drop_s} {e.get('mode') or e.get('source')} "
                f"{band} · {act}"
            )
        lines.append("\nTip: label from the fire buttons — no /j needed.")
        _reply(message, "\n".join(lines))

    @bot.message_handler(commands=["j", "journal_label"])
    def cmd_j(message):
        if not _learning_enabled():
            _reply(message, "Learning is off. Set FEATURE_LEARNING=true and restart.")
            return
        args = message.text.split()[1:]
        if not args:
            _reply(
                message,
                "Prefer buttons on the dump alert, or type: took / skip / later\n\n"
                "Power /j (optional):\n"
                "/j took [symbol]\n"
                "/j skip [symbol]\n"
                "/j bounce strong|weak|none|failed [symbol]\n"
                "/j pride [symbol]\n"
                "/j note your text…",
            )
            return
        sub = args[0].lower()
        rest = args[1:]
        user_id = message.from_user.id
        action = bounce = behavior = notes = None
        symbol = None

        if sub in ("took", "take", "in"):
            action = "took"
            symbol = rest[0] if rest else None
        elif sub in ("skip", "skipped", "pass", "no"):
            action = "skip"
            symbol = rest[0] if rest else None
        elif sub in ("watch", "watching", "later"):
            action = "watch"
            symbol = rest[0] if rest else None
        elif sub == "bounce":
            if not rest:
                _reply(message, "Usage: /j bounce strong|weak|none|failed [symbol]")
                return
            bounce = rest[0].lower()
            if bounce not in ("strong", "weak", "none", "failed"):
                _reply(message, "bounce must be strong|weak|none|failed")
                return
            symbol = rest[1] if len(rest) > 1 else None
        elif sub == "pride":
            behavior = "pride"
            symbol = rest[0] if rest else None
        elif sub == "note":
            notes = " ".join(rest) if rest else None
            if not notes:
                _reply(message, "Usage: /j note your text")
                return
        else:
            _reply(message, f"Unknown /j subcommand: {sub}\nOr just type: took / skip")
            return

        eid = event_store.label_latest(
            user_id,
            symbol=symbol,
            action=action,
            bounce_quality=bounce,
            behavior=behavior,
            notes=notes,
        )
        if eid is None:
            _reply(message, "No matching event to label. Wait for a fire (use buttons there).")
            return
        bits = [f"Labeled event #{eid}"]
        if action:
            bits.append(f"action={action}")
        if bounce:
            bits.append(f"bounce={bounce}")
        if behavior:
            bits.append(f"behavior={behavior}")
        if notes:
            bits.append("note saved")
        _reply(message, " · ".join(bits))

    @bot.message_handler(commands=["trade"])
    def cmd_trade(message):
        if not _learning_enabled():
            _reply(message, "Learning is off. Set FEATURE_LEARNING=true and restart.")
            return
        args = message.text.split()[1:]
        user_id = message.from_user.id
        if not args:
            _reply(
                message,
                "Usage:\n"
                "/trade open f TSLA [price] [notes…]\n"
                "/trade open s SIREN [price]\n"
                "/trade list\n"
                "/trade close [id|symbol] [exit_price] [notes…]",
            )
            return
        sub = args[0].lower()
        if sub == "list":
            rows = event_store.journal_list(user_id, open_only=True)
            if not rows:
                _reply(message, "No open journal trades.")
                return
            lines = ["Open journal trades:"]
            for t in rows:
                entry = t.get("entry_avg")
                es = f" @ {entry}" if entry is not None else ""
                lines.append(
                    f"#{t['id']} [{t['market'][:1].upper()}] {t['symbol']}{es}"
                )
            _reply(message, "\n".join(lines))
            return
        if sub == "open":
            rest = args[1:]
            market = "futures"
            if rest and rest[0].lower() in ("f", "futures", "s", "spot"):
                market = "futures" if rest[0].lower() in ("f", "futures") else "spot"
                rest = rest[1:]
            if not rest:
                _reply(message, "Usage: /trade open f SYMBOL [price] [notes]")
                return
            symbol = rest[0].upper()
            entry = None
            notes = None
            if len(rest) >= 2:
                try:
                    entry = float(rest[1].replace(",", ""))
                    notes = " ".join(rest[2:]) or None
                except ValueError:
                    notes = " ".join(rest[1:]) or None
            tid = event_store.journal_open(
                user_id, symbol, market, entry_avg=entry, notes=notes
            )
            _reply(
                message,
                f"Journal open #{tid} [{market[:1].upper()}] {symbol}"
                + (f" @ {entry}" if entry is not None else ""),
            )
            return
        if sub == "close":
            rest = args[1:]
            trade_id = None
            symbol = None
            exit_avg = None
            notes = None
            if rest:
                try:
                    trade_id = int(rest[0])
                    rest = rest[1:]
                except ValueError:
                    symbol = rest[0]
                    rest = rest[1:]
            if rest:
                try:
                    exit_avg = float(rest[0].replace(",", ""))
                    notes = " ".join(rest[1:]) or None
                except ValueError:
                    notes = " ".join(rest) or None
            ok = event_store.journal_close(
                user_id,
                trade_id=trade_id,
                symbol=symbol,
                exit_avg=exit_avg,
                notes=notes,
            )
            if not ok:
                _reply(message, "No open trade matched.")
                return
            _reply(message, "Journal trade closed.")
            return
        _reply(message, f"Unknown /trade subcommand: {sub}")

    @bot.message_handler(commands=["brief"])
    def cmd_brief(message):
        if not _learning_enabled():
            _reply(message, "Learning is off. Set FEATURE_LEARNING=true and restart.")
            return
        from .coach import format_brief

        recent = event_store.recent_events(message.from_user.id, limit=12)
        opens = event_store.journal_list(message.from_user.id, open_only=True)
        _reply(
            message,
            format_brief(
                recent_events=recent,
                open_trades=opens,
                learning_on=True,
            ),
        )

    @bot.message_handler(commands=["coach"])
    def cmd_coach(message):
        if not _learning_enabled():
            _reply(message, "Learning is off. Set FEATURE_LEARNING=true and restart.")
            return
        from .coach import format_coach_reply

        parts = message.text.split(maxsplit=1)
        question = parts[1] if len(parts) > 1 else "checklist"
        recent = event_store.recent_events(message.from_user.id, limit=5)
        stats = None
        # If question mentions a token-like word, pull stats
        for tok in question.replace(",", " ").split():
            t = tok.strip().upper()
            if len(t) >= 2 and t.isalpha():
                stats = event_store.stats_for_symbol(message.from_user.id, t)
                if stats.get("events"):
                    break
                stats = None
        _reply(
            message,
            format_coach_reply(question, recent_events=recent, stats=stats),
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

    # Plain-language assistant (learning on) — before unknown-/ catch-all
    @bot.message_handler(
        func=lambda m: bool(
            m.text
            and not m.text.startswith("/")
            and getattr(settings, "feature_learning", False)
            and event_store is not None
        )
    )
    def cmd_plain_assistant(message):
        from .assistant.ux import parse_plain_intent
        from .coach import format_brief, format_coach_reply

        intent = parse_plain_intent(message.text or "")
        if not intent:
            # Do not spam — only guide if short message looks like a question
            t = (message.text or "").strip()
            if len(t) < 40 and t.endswith("?"):
                _reply(message, "Try: took / skip / brief / coach / desk — or /desk")
            return

        user_id = message.from_user.id
        kind = intent["intent"]

        if kind in ("took", "skip", "watch"):
            eid = event_store.label_latest(user_id, action=kind)
            if eid is None:
                _reply(message, "No recent fire to label. Wait for a mover, then tap or say took/skip.")
                return
            _reply(message, f"OK — {kind} on event #{eid}.")
            return
        if kind == "pride":
            eid = event_store.label_latest(user_id, behavior="pride")
            if eid is None:
                _reply(message, "No recent event for pride flag.")
                return
            _reply(message, f"Pride flag on event #{eid}. Stick to the plan if structure is still valid.")
            return
        if kind == "brief":
            recent = event_store.recent_events(user_id, limit=12)
            opens = event_store.journal_list(user_id, open_only=True)
            _reply(
                message,
                format_brief(recent_events=recent, open_trades=opens, learning_on=True),
            )
            return
        if kind == "coach":
            q = intent.get("question") or "checklist"
            recent = event_store.recent_events(user_id, limit=5)
            _reply(message, format_coach_reply(q, recent_events=recent, stats=None))
            return
        if kind == "open":
            rows = event_store.journal_list(user_id, open_only=True)
            if not rows:
                _reply(
                    message,
                    "No open journal trades yet.\n"
                    "(Soon: auto from MEXC. For now /trade open … or just trade and label fires with buttons.)",
                )
                return
            lines = ["Open journal:"]
            for t in rows:
                entry = t.get("entry_avg")
                es = f" @ {entry}" if entry is not None else ""
                lines.append(f"#{t['id']} [{t['market'][:1].upper()}] {t['symbol']}{es}")
            _reply(message, "\n".join(lines))
            return
        if kind == "events":
            # reuse list
            rows = event_store.recent_events(user_id, limit=10)
            if not rows:
                _reply(message, "No events yet.")
                return
            lines = ["Recent fires:"]
            for e in rows:
                act = e.get("last_action") or "unlabeled"
                lines.append(f"#{e['id']} {e.get('symbol')} · {act}")
            _reply(message, "\n".join(lines))
            return

    # Catch-all unknown slash commands
    @bot.message_handler(func=lambda m: m.text and m.text.startswith("/"))
    def cmd_unknown(message):
        _reply(
            message,
            "Unknown command. Try /desk (assistant) or /a BTC 65000 (alert).",
        )

    return bot
