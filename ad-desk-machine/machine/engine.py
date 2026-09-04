"""Machine evaluate. Path / Size / Chart / Fail / Exit live-read. No live orders."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .chart import at_ad_now, bars_ever_met
from .exit import exit_decision, leftover_remaining_cost, leftover_usd
from .fail import fail_decision, last_under_ad
from .fills import (
    assert_no_live_send,
    empty_out_after_buy,
    simulate_buy_fills,
    simulate_sell_fills,
)
from .log import MachineLog
from .path import path_decision
from .plays import load_exit_facts, load_hung_plays, load_play
from .settings import LIVE_ORDERS_ALLOWED, live_orders_allowed
from .size import size_gate, size_layers, volume_match

LOG = MachineLog()
NEEDS: List[Dict[str, Any]] = []
BOOK: Dict[str, List[Dict[str, Any]]] = {}


def reset_runtime() -> None:
    LOG.clear()
    NEEDS.clear()
    BOOK.clear()


def _f(raw: Any) -> Optional[float]:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _tape_from(play: Dict[str, Any], snap: Dict[str, Any]) -> Dict[str, Any]:
    last = snap.get("current_price")
    if last is None:
        last = snap.get("last") or snap.get("last_price")
    bars = snap.get("bars") or []
    faster_bars = snap.get("faster_bars") or snap.get("bars_1m") or []
    met = bars_ever_met(play, list(bars) + list(faster_bars), last=last)
    at_ad = at_ad_now(
        last=last,
        ad_top=play.get("ad_top"),
        ad_bottom=play.get("ad_bottom"),
        ad_known=True,
    )
    habit = _f(play.get("vol_at_bottom_usd") or play.get("volume_habit_usd") or snap.get("vol_habit_usd"))
    vol = _f(snap.get("vol_usd_fast") if snap.get("vol_usd_fast") is not None else snap.get("vol_usd"))
    vmatch = bool(snap.get("volume_match"))
    if not vmatch and vol is not None and habit is not None:
        vmatch = volume_match(vol, habit)
    if snap.get("vol_spike"):
        vmatch = True
    past_b = last_under_ad(last, play.get("ad_bottom"))
    fills = list(BOOK.get(str(play.get("id") or ""), []))
    in_play = bool(fills) or bool(play.get("in_play"))
    return {
        "current_price": last,
        "last": last,
        "met": met,
        "met_now": at_ad,
        "at_ad": at_ad,
        "chosen_tf_reds": snap.get("chosen_tf_reds") if snap.get("chosen_tf_reds") is not None else snap.get("reds"),
        "faster_tf_reds": snap.get("faster_tf_reds"),
        "volume_match": vmatch,
        "vol_spike": bool(snap.get("vol_spike") or vmatch),
        "prior_vol_spike": bool(snap.get("prior_vol_spike")),
        "quiet_grind": bool(snap.get("quiet_grind")),
        "board_panic": bool(snap.get("board_panic") or snap.get("panic_board")),
        "panic_board": bool(snap.get("panic_board") or snap.get("board_panic")),
        "fast_dump": bool(snap.get("fast_dump") or snap.get("fast_dump_volume")),
        "fast_dump_volume": bool(snap.get("fast_dump_volume") or snap.get("fast_dump")),
        "past_b": past_b,
        "news": snap.get("news") or [],
        "in_play": in_play,
        "grind_not_this_chart": bool(snap.get("grind_not_this_chart")),
        "weak_first_bounce": bool(snap.get("weak_first_bounce")),
        "candles_since_arm": snap.get("candles_since_arm"),
        "typical_bounce": snap.get("typical_bounce"),
        "armed_at": snap.get("armed_at") or play.get("armed_at"),
        "now": snap.get("now"),
        "bounced": bool(snap.get("bounced")),
        "bars": bars,
        "faster_bars": faster_bars,
    }


def evaluate(play: Dict[str, Any], snap: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """One live-read. Simulated fills only. live_orders_allowed stays false."""
    assert_no_live_send()
    snap = snap or {}
    extra = load_exit_facts(str(play.get("id") or "")) or {}
    if extra.get("typical_bounce") and not play.get("typical_bounce"):
        play = dict(play)
        play["typical_bounce"] = extra.get("typical_bounce")
        play.setdefault("candles_to_bounce", extra.get("candles_to_bounce"))

    tape = _tape_from(play, snap)
    play_id = str(play.get("id") or play.get("symbol") or "play")
    fills = list(BOOK.get(play_id, []))
    tape["in_play"] = bool(fills) or bool(play.get("in_play"))

    fail = fail_decision(play, tape)
    path = path_decision(play, tape)
    gate = size_gate(play, tape)
    buys = size_layers(play)
    last = tape.get("current_price")

    action = path.get("action")
    reason = path.get("reason")
    decision = path.get("decision")
    new_fills: List[Dict[str, Any]] = []

    if fail.get("action") == "flatten":
        action, reason, decision = "flatten", fail["reason"], fail["decision"]
    elif fail.get("add_panic") and not play.get("watch_only"):
        action, reason, decision = "add_panic", fail["reason"], fail["decision"]
        panic = [L for L in buys if str(L.get("band")) == "panic"]
        already = [int(f.get("idx") or 0) for f in fills if f.get("side") == "buy"]
        bag = leftover_usd(fills)
        new_fills = simulate_buy_fills(panic, last, already=already, scale=1.0, bag_usd=bag)
    elif path.get("buy") and gate.get("ok") and not play.get("watch_only"):
        already = [int(f.get("idx") or 0) for f in fills if f.get("side") == "buy"]
        bag = leftover_usd(fills)
        ad = [L for L in buys if str(L.get("band")) == "ad"]
        new_fills = simulate_buy_fills(ad, last, already=already, scale=float(gate.get("scale") or 1), bag_usd=bag)
        if new_fills:
            action, reason = "buy", path.get("reason")
            decision = path.get("decision")
        elif path.get("buy"):
            # Path said buy but last has not tagged a Size layer yet.
            action, reason, decision = "wait", "wait_for_layer", "At the AD, waiting for last to tag a Size layer."
    elif not gate.get("ok") and path.get("buy"):
        action, reason, decision = "wait", gate.get("reason"), "Grind, no volume, wait."

    if new_fills:
        fills.extend(new_fills)
        BOOK[play_id] = fills

    exit_state = exit_decision(play, tape, fills)
    sell_new: List[Dict[str, Any]] = []
    if exit_state.get("sell_now") and fills:
        already_s = [int(f.get("idx") or 0) for f in fills if f.get("side") == "sell"]
        sell_new = simulate_sell_fills(
            exit_state["sell_now"],
            last,
            already=already_s,
            remaining_usd=leftover_usd(fills),
        )
        if sell_new:
            fills.extend(sell_new)
            BOOK[play_id] = fills
            action, reason = "sell", f"bounce_{exit_state.get('bounce_kind')}"
            decision = f"{exit_state.get('bounce_kind')} bounce, selling hung sell layers."

    leftover = leftover_remaining_cost(fills)
    need = None
    if empty_out_after_buy(new_fills, play.get("sell_layers") or []):
        need = {
            "kind": "empty_out_after_buy",
            "play_id": play_id,
            "symbol": play.get("symbol"),
            "tone": "needs_you",
            "text": "Buy filled and sell layers are empty. Hung plan needs an out.",
        }
        NEEDS.append(need)
        if action == "buy":
            reason = "empty_out_after_buy"

    event = {
        "action": action,
        "reason": reason,
        "decision": decision,
        "current_price": last,
        "symbol": play.get("symbol"),
        "ts": snap.get("now"),
    }
    logged = LOG.append_if_changed(play_id, event)

    return {
        "ok": True,
        "play_id": play_id,
        "symbol": play.get("symbol"),
        "tf": play.get("tf"),
        "hung_plan": True,
        "current_price": last,
        "met": bool(tape.get("met")),
        "at_ad": bool(tape.get("at_ad")),
        "action": action,
        "reason": reason,
        "decision": decision,
        "path": path,
        "size_gate": gate,
        "buy_layers": buys,
        "sell_layers": exit_state.get("sell_layers") or [],
        "fills": list(fills),
        "new_fills": new_fills + sell_new,
        "leftover": leftover,
        "bounce_kind": exit_state.get("bounce_kind"),
        "defensive": bool(exit_state.get("defensive")),
        "needs_you": need,
        "logged": logged is not None,
        "live_orders_allowed": live_orders_allowed(),
        "live_orders_sent": False,
        "simulated": True,
    }


def evaluate_id(play_id: str, snap: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return evaluate(load_play(play_id), snap)


def public_play(play: Dict[str, Any], snap: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Owner-facing hung plan. Language: Machine / hung plan / layers / current price."""
    snap = snap or {}
    last = snap.get("current_price")
    if last is None:
        last = snap.get("last")
    fills = list(BOOK.get(str(play.get("id") or ""), []))
    return {
        "id": play.get("id"),
        "symbol": play.get("symbol"),
        "market": play.get("market") or "spot",
        "tf": play.get("tf"),
        "ad_top": play.get("ad_top"),
        "ad_bottom": play.get("ad_bottom"),
        "watch_only": bool(play.get("watch_only")),
        "habit_ready": bool(play.get("habit_ready")),
        "dump_depth": play.get("dump_depth") or "high_magnet",
        "current_price": last,
        "met": bool(play.get("met") or (snap.get("met") if snap else False)),
        "buy_layers": size_layers(play),
        "sell_layers": play.get("sell_layers") or [],
        "Size_layers": size_layers(play),
        "fills": fills,
        "leftover": leftover_remaining_cost(fills),
        "hung_plan": True,
        "live_orders_allowed": LIVE_ORDERS_ALLOWED,
    }


def hung_board(snaps: Optional[Dict[str, Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    snaps = snaps or {}
    out = []
    for play in load_hung_plays():
        pid = str(play.get("id"))
        out.append(public_play(play, snaps.get(pid)))
    return out
