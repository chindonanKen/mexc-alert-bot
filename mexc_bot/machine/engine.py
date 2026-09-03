"""Machine loop: seed, recut, arm simulated layers, close → KB, rank.

No MEXC private keys. No live sends. Isolated from Positions leftover-avg.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .facts import bars_ever_met, facts_from, faster_tf_for
from .hang import hang_ad, manila_label, official_volume_n, volume_label
from .interpreter import interpret, why_sentence
from .packs import load_process_pack
from .tape import official_last_price, official_reds
from .logic import (
    at_ad_layer,
    can_open_play,
    decision_line,
    dump_depth_layers,
    is_faster_tf,
    news_kill,
    pick_working_tf,
    tf_meets_rules,
)
from .settings import (
    DEFAULT_LAYER_COUNT,
    EQUITY_USD,
    MAX_PER_PLAY_USD,
    SEED_NAMES,
    is_seed,
)
from .store import MachineStore, parse_json

TAPE_ACTIONS = (
    "paper-buy",
    "paper-sell",
    "add-panic",
    "flatten-news",
    "sit-out",
    "grind-on",
    "grind-off",
    "panic-on",
    "panic-off",
)
_FILLISH = ("paper-buy", "paper-sell", "add-panic")
_board_prev: Dict[str, Any] = {}


def sit_at_buy_line(plan: Optional[Dict[str, Any]], last: Any) -> bool:
    """Sit tape only at this chart's buy line: last 5% of L above B, through B."""
    if not plan:
        return False
    from .facts import is_met

    return is_met(
        last=last,
        ad_top=plan.get("ad_top"),
        ad_bottom=plan.get("ad_bottom"),
        ad_known=(plan.get("ad_status") == "known") and plan.get("ad_top") is not None,
    )


def purge_sit_not_at_line(store: MachineStore, user_id: int) -> int:
    """Delete sit-out tape rows whose last was not at that plan's buy line."""
    plans = {int(p["id"]): p for p in store.list_plans(user_id)}
    sits = store.list_log(user_id, actions=("sit-out",), limit=8000)
    bad: List[int] = []
    for r in sits:
        pid = r.get("plan_id")
        plan = plans.get(int(pid)) if pid is not None else None
        if not sit_at_buy_line(plan, r.get("last_price")):
            try:
                bad.append(int(r["id"]))
            except (TypeError, ValueError, KeyError):
                continue
    if bad:
        store.delete_log_ids(user_id, bad)
    return len(bad)


def last_reached_layer(last: Any, layer: Any) -> bool:
    try:
        return float(last) <= float(layer) * (1.0 + 1e-9)
    except (TypeError, ValueError):
        return False


def purge_unreached_buys(store: MachineStore, user_id: int) -> int:
    """Delete paper-buys (and wait-as-buy theater) where last never traded the layer."""
    bad: List[int] = []
    rows = store.list_log(
        user_id, actions=("paper-buy", "add-panic", "wait"), limit=8000
    )
    for r in rows:
        last = r.get("last_price")
        layer = r.get("filled_price")
        if layer is None:
            layer = r.get("intended_price")
        a = str(r.get("action") or "")
        if a == "wait" and r.get("intended_price") is not None:
            try:
                bad.append(int(r["id"]))
            except (TypeError, ValueError, KeyError):
                pass
            continue
        if a in ("paper-buy", "add-panic") and not last_reached_layer(last, layer):
            try:
                bad.append(int(r["id"]))
            except (TypeError, ValueError, KeyError):
                pass
    if bad:
        store.delete_log_ids(user_id, bad)
    return len(bad)


def purge_empty_fills(store: MachineStore, user_id: int) -> int:
    """Remove buy/sell/add rows that never got a filled price. Those were not fills."""
    rows = store.list_log(user_id, actions=_FILLISH, limit=8000)
    bad: List[int] = []
    for r in rows:
        if r.get("filled_price") is None:
            try:
                bad.append(int(r["id"]))
            except (TypeError, ValueError, KeyError):
                continue
    if bad:
        store.delete_log_ids(user_id, bad)
    return len(bad)


def public_tape_rows(
    store: MachineStore,
    user_id: int,
    *,
    plan_id: Optional[int] = None,
    since: Optional[float] = None,
    limit: int = 40,
) -> List[Dict[str, Any]]:
    """Tape the owner asked for. Fills are fills. No wait. No off-line sit."""
    purge_sit_not_at_line(store, user_id)
    purge_empty_fills(store, user_id)
    purge_unreached_buys(store, user_id)
    plans = {int(p["id"]): p for p in store.list_plans(user_id)}
    rows = store.list_log(
        user_id,
        plan_id=plan_id,
        since=since,
        limit=max(int(limit) * 3, 40),
        actions=TAPE_ACTIONS,
    )
    out: List[Dict[str, Any]] = []
    for r in rows:
        a = str(r.get("action") or "")
        if a in ("wait", "pull-pack"):
            continue
        if a in _FILLISH:
            if r.get("filled_price") is None or r.get("intended_price") is None:
                continue
        if a == "sit-out":
            pid = r.get("plan_id")
            plan = plans.get(int(pid)) if pid is not None else None
            if not sit_at_buy_line(plan, r.get("last_price")):
                continue
        out.append(r)
        if len(out) >= int(limit):
            break
    return out


def seed_plans(
    store: MachineStore,
    user_id: int,
    *,
    klines: Optional[Dict[str, Dict[str, list]]] = None,
) -> List[Dict[str, Any]]:
    """Create the six seed plans if missing. Does not invent unknown ADs."""
    klines = klines or {}
    out: List[Dict[str, Any]] = []
    for seed in SEED_NAMES:
        existing = store.get_plan_by_symbol(user_id, seed["symbol"], seed["market"])
        if existing:
            out.append(public_plan(store, existing))
            continue
        key = f"{seed['symbol']}|{seed['market']}"
        hung = hang_ad(
            seed["symbol"],
            seed["market"],
            db_path=store.db_path,
            klines_by_tf=klines.get(key),
        )
        layers = dump_depth_layers(
            hung.get("ad_top"),
            hung.get("ad_bottom"),
            budget_usd=MAX_PER_PLAY_USD,
        )
        row = store.upsert_plan(
            user_id,
            {
                "symbol": seed["symbol"],
                "market": seed["market"],
                "display_name": seed["display"],
                "tf": hung.get("tf"),
                "ad_top": hung.get("ad_top"),
                "ad_bottom": hung.get("ad_bottom"),
                "ad_status": hung.get("ad_status"),
                "ad_source": hung.get("ad_source"),
                "ad_note": hung.get("ad_note"),
                "bar_top_ts": hung.get("bar_top_ts"),
                "bar_bottom_ts": hung.get("bar_bottom_ts"),
                "bar_top_label": hung.get("bar_top_label"),
                "bar_bottom_label": hung.get("bar_bottom_label"),
                "initial_drop_top": hung.get("initial_drop_top"),
                "initial_drop_bottom": hung.get("initial_drop_bottom"),
                "zones": hung.get("zones"),
                "layers": layers,
                "remaining_layers": len(layers) if layers else 0,
                "next_layer_usd": layers[0]["usd"] if layers else None,
                "status": "watch",
                "live": False,
                "resting": False,
                "volume": "unknown",
                "news": None,
                **(
                    {
                        "decision": "No written plan, sit. (plan.none)",
                        "decision_reason": "sit_out",
                    }
                    if hung.get("ad_status") != "known"
                    else {}
                ),
            },
        )
        out.append(public_plan(store, row))
    return out


def recut(
    store: MachineStore,
    user_id: int,
    plan_id: int,
    *,
    ad_top: Optional[float] = None,
    ad_bottom: Optional[float] = None,
    remaining_layers: Optional[int] = None,
    tf: Optional[str] = None,
    volume_habit_usd: Optional[float] = None,
    candles_to_bounce: Optional[int] = None,
) -> Dict[str, Any]:
    plan = store.get_plan(user_id, plan_id)
    if not plan:
        raise KeyError("plan not found")
    if str(plan.get("status") or "") in ("closed", "killed", "blocked"):
        raise ValueError("plan is parked")
    top = float(ad_top) if ad_top is not None else plan.get("ad_top")
    bot = float(ad_bottom) if ad_bottom is not None else plan.get("ad_bottom")
    del remaining_layers
    layers = dump_depth_layers(top, bot, budget_usd=MAX_PER_PLAY_USD)
    known = False
    try:
        known = top is not None and bot is not None and float(top) > float(bot)
    except (TypeError, ValueError):
        known = False
    play = parse_json(plan.get("play_json"), {}) or {}
    play.update({
        "tf": tf or plan.get("tf") or play.get("tf"),
        "ad_top": top,
        "ad_bottom": bot,
    })
    if volume_habit_usd is not None:
        play["volume_habit_usd"] = float(volume_habit_usd)
    if candles_to_bounce is not None:
        play["candles_to_bounce"] = int(candles_to_bounce)
    patch: Dict[str, Any] = {
        "ad_top": top,
        "ad_bottom": bot,
        "ad_status": "known" if known else "unknown",
        "ad_source": "recut",
        "layers": layers,
        "remaining_layers": len(layers),
        "next_layer_usd": layers[0]["usd"] if layers else None,
        "play": play,
    }
    if tf:
        patch["tf"] = tf
    if plan.get("live"):
        working = store.list_orders(user_id, plan_id, status="working")
        filled_usd = sum(
            float(o.get("usd") or 0)
            for o in store.list_orders(user_id, plan_id, status="filled")
            if o.get("side") != "sell"
        )
        nibble_book = bool(play.get("nibble_done")) and not working
        if nibble_book:
            patch["resting"] = False
            patch["remaining_layers"] = 0
            patch["next_layer_usd"] = None
            patch["allocated_usd"] = round(filled_usd, 4)
        else:
            filled_idx = {
                int(o.get("layer_idx") or 0)
                for o in store.list_orders(user_id, plan_id, status="filled")
                if o.get("side") != "sell"
            }
            remaining = [L for L in layers if int(L.get("idx") or 0) not in filled_idx]
            store.replace_working_orders(user_id, plan_id, remaining)
            patch["resting"] = bool(remaining)
            patch["remaining_layers"] = len(remaining)
            patch["allocated_usd"] = round(filled_usd, 4)
    store.patch_plan(user_id, plan_id, **patch)
    return public_plan(store, store.get_plan(user_id, plan_id))


def write_layers(
    store: MachineStore,
    user_id: int,
    plan_id: int,
    *,
    ad_top: Optional[float] = None,
    ad_bottom: Optional[float] = None,
) -> Dict[str, Any]:
    """Persist dump-depth pack. Allowed on closed/killed. Does not recut T/B unless given."""
    plan = store.get_plan(user_id, plan_id)
    if not plan:
        raise KeyError("plan not found")
    top = float(ad_top) if ad_top is not None else plan.get("ad_top")
    bot = float(ad_bottom) if ad_bottom is not None else plan.get("ad_bottom")
    layers = dump_depth_layers(top, bot, budget_usd=MAX_PER_PLAY_USD)
    if not layers:
        raise ValueError("need a written AD top and bottom")
    parked = str(plan.get("status") or "") in ("closed", "killed", "blocked")
    patch: Dict[str, Any] = {
        "layers": layers,
        "remaining_layers": 0 if parked else len(layers),
        "next_layer_usd": None if parked else (layers[0]["usd"] if layers else None),
    }
    if ad_top is not None:
        patch["ad_top"] = top
    if ad_bottom is not None:
        patch["ad_bottom"] = bot
    if plan.get("live") and not parked:
        filled_idx = {
            int(o.get("layer_idx") or 0)
            for o in store.list_orders(user_id, plan_id, status="filled")
            if o.get("side") != "sell"
        }
        remaining = [L for L in layers if int(L.get("idx") or 0) not in filled_idx]
        store.replace_working_orders(user_id, plan_id, remaining)
        patch["remaining_layers"] = len(remaining)
        patch["resting"] = bool(remaining)
    store.patch_plan(user_id, plan_id, **patch)
    return public_plan(store, store.get_plan(user_id, plan_id))


def kill(store: MachineStore, user_id: int, plan_id: int) -> Dict[str, Any]:
    plan = store.get_plan(user_id, plan_id)
    if not plan:
        raise KeyError("plan not found")
    store.cancel_working(user_id, plan_id)
    _close_and_kb(
        store,
        user_id,
        plan,
        reason="kill",
        bounce_or_fail="kill",
        process_ok=True,
        money_pnl=0.0,
    )
    store.patch_plan(
        user_id,
        plan_id,
        status="killed",
        live=False,
        resting=False,
        allocated_usd=0,
        armed_at=None,
        next_layer_usd=None,
        **decision_line(kind="kill"),
    )
    return public_plan(store, store.get_plan(user_id, plan_id))


def propose_name(
    store: MachineStore,
    user_id: int,
    symbol: str,
    market: str,
) -> Dict[str, Any]:
    if is_seed(symbol, market):
        seed_plans(store, user_id)
        row = store.get_plan_by_symbol(user_id, symbol, market)
        return {"seed": True, "plan": public_plan(store, row) if row else None}
    existing = store.get_plan_by_symbol(user_id, symbol, market)
    if existing:
        return {"already": True, "plan": public_plan(store, existing)}
    need = store.add_need(
        user_id,
        "approve_name",
        symbol=symbol,
        market=market,
        payload={"symbol": symbol.upper(), "market": market.lower()},
    )
    return {"need": public_need(need)}


def propose_line(
    store: MachineStore,
    user_id: int,
    plan_id: int,
    ad_top: float,
    ad_bottom: float,
) -> Dict[str, Any]:
    plan = store.get_plan(user_id, plan_id)
    if not plan:
        raise KeyError("plan not found")
    need = store.add_need(
        user_id,
        "line_change",
        symbol=plan["symbol"],
        market=plan["market"],
        payload={
            "plan_id": int(plan_id),
            "ad_top": float(ad_top),
            "ad_bottom": float(ad_bottom),
            "was_top": plan.get("ad_top"),
            "was_bottom": plan.get("ad_bottom"),
        },
    )
    return public_need(need)


def resolve_need(
    store: MachineStore,
    user_id: int,
    need_id: int,
    accept: bool,
) -> Dict[str, Any]:
    need = store.get_need(user_id, need_id)
    if need.get("status") != "open":
        return {"need": public_need(need), "already": True}
    status = "accepted" if accept else "rejected"
    store.resolve_need(user_id, need_id, status)
    need = store.get_need(user_id, need_id)
    payload = parse_json(need.get("payload_json"), {})
    plan = None
    if accept and need.get("kind") == "approve_name":
        symbol = need.get("symbol") or payload.get("symbol")
        market = need.get("market") or payload.get("market") or "spot"
        hung = hang_ad(symbol, market, db_path=store.db_path)
        layers = dump_depth_layers(
            hung.get("ad_top"),
            hung.get("ad_bottom"),
            budget_usd=MAX_PER_PLAY_USD,
        )
        row = store.upsert_plan(
            user_id,
            {
                "symbol": symbol,
                "market": market,
                "display_name": str(symbol).replace("USDT", "").replace("STOCK_", ""),
                **{k: hung.get(k) for k in (
                    "tf", "ad_top", "ad_bottom", "ad_status", "ad_source",
                    "ad_note", "bar_top_ts", "bar_bottom_ts", "bar_top_label",
                    "bar_bottom_label", "initial_drop_top", "initial_drop_bottom",
                    "zones",
                )},
                "layers": layers,
                "remaining_layers": len(layers) if layers else 0,
                "next_layer_usd": layers[0]["usd"] if layers else None,
                "status": "watch",
                **(
                    {
                        "decision": "No written plan, sit. (plan.none)",
                        "decision_reason": "sit_out",
                    }
                    if hung.get("ad_status") != "known"
                    else {}
                ),
            },
        )
        plan = public_plan(store, row)
    elif accept and need.get("kind") == "line_change":
        plan = recut(
            store,
            user_id,
            int(payload["plan_id"]),
            ad_top=payload.get("ad_top"),
            ad_bottom=payload.get("ad_bottom"),
        )
    return {"need": public_need(need), "plan": plan}


def evaluate(
    store: MachineStore,
    user_id: int,
    snapshot: Optional[Dict[str, Any]] = None,
    *,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Apply gates. May arm up to 2 simulated plays. Never sends to MEXC."""
    wall = float(now if now is not None else time.time())
    snapshot = snapshot or {}
    seed_plans(store, user_id)
    recompute_closed_money(store, user_id)
    actions: List[Dict[str, Any]] = []
    for plan in store.list_plans(user_id):
        key = f"{plan['symbol']}|{plan['market']}"
        snap = snapshot.get(key) or snapshot.get(plan["symbol"]) or {}
        result = _evaluate_plan(store, user_id, plan, snap, wall)
        if result:
            actions.append(result)
    return {
        "ok": True,
        "actions": actions,
        "plans": rank_plans(store, user_id),
        "live_orders_sent": False,
    }


def try_arm(
    store: MachineStore,
    user_id: int,
    plan_id: int,
    snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    plan = store.get_plan(user_id, plan_id)
    if not plan:
        raise KeyError("plan not found")
    snap = snapshot or {}
    return _evaluate_plan(store, user_id, plan, snap, time.time()) or {
        "ok": False,
        "reason": "not_armed",
        "plan": public_plan(store, store.get_plan(user_id, plan_id)),
    }


def _set_decision(
    store: MachineStore,
    user_id: int,
    plan_id: int,
    kind: str,
    plan: Optional[Dict[str, Any]] = None,
    **extra: Any,
) -> Dict[str, str]:
    src = plan or {}
    why = decision_line(
        kind=kind,
        reds=extra.get("reds", src.get("reds")),
        tf=extra.get("tf", src.get("tf")),
        volume=extra.get("volume", src.get("volume")),
        volume_n=extra.get("volume_n", src.get("volume_n")),
    )
    store.patch_plan(user_id, int(plan_id), **why)
    return why


def _evaluate_plan(
    store: MachineStore,
    user_id: int,
    plan: Dict[str, Any],
    snap: Dict[str, Any],
    now: float,
) -> Optional[Dict[str, Any]]:
    news_hits = list(snap.get("news") or [])
    kill = news_kill(news_hits)
    reds_map = snap.get("reds") if isinstance(snap.get("reds"), dict) else {}
    play_tf = str(plan.get("tf") or "").strip() or None
    if not reds_map and snap.get("reds") is not None:
        tf = play_tf or "15m"
        reds_map = {tf: snap.get("reds")}
    volume = snap.get("volume") or plan.get("volume") or "unknown"
    volume_n = (
        snap.get("volume_n")
        if snap.get("volume_n") is not None
        else snap.get("vol_usd_fast")
        if snap.get("vol_usd_fast") is not None
        else snap.get("vol_usd_play")
        if snap.get("vol_usd_play") is not None
        else plan.get("volume_n")
    )
    last_price = official_last_price(
        ticker=snap.get("last_price")
        if snap.get("last_price") is not None
        else snap.get("ticker"),
        bars=snap.get("bars_1m"),
    )
    if last_price is None:
        last_price = plan.get("last_price")
    if snap.get("bars"):
        if not snap.get("volume"):
            volume = volume_label(snap.get("bars"))
        tape_reds_play = official_reds(snap.get("bars"))
        if tape_reds_play is not None:
            tf_key = str(play_tf or "15m")
            reds_map = dict(reds_map)
            reds_map[tf_key] = tape_reds_play
        if volume_n is None:
            volume_n = official_volume_n(snap.get("bars"))
    if snap.get("bars_1m") and volume_n is None:
        volume_n = official_volume_n(snap.get("bars_1m"))
    heat = snap.get("heat_breadth")
    panic = bool(snap.get("panic_board"))
    ad_known = (plan.get("ad_status") == "known") and plan.get("ad_top") is not None
    board = snap.get("board") if isinstance(snap.get("board"), dict) else {}

    tf_states = []
    tfs = list(reds_map.keys()) if reds_map else ([play_tf] if play_tf else [])
    for tf in tfs:
        habit = store.habit_reds(user_id, plan["symbol"], plan["market"], str(tf))
        faster = is_faster_tf(str(tf), play_tf)
        tf_states.append(
            tf_meets_rules(
                tf=str(tf),
                reds=reds_map.get(tf),
                habit_reds=habit,
                ad_known=ad_known,
                heat_breadth=heat,
                panic_board=panic,
                news_hits=news_hits,
                play_tf=bool(play_tf) and str(tf) == play_tf,
                faster_tf=faster,
            )
        )
    chosen = pick_working_tf(
        tf_states,
        respected=store.respected_scores(user_id, plan["symbol"], plan["market"]),
        locked_tf=play_tf,
    )
    if play_tf:
        sit_state = next(
            (s for s in tf_states if str(s.get("tf") or "") == play_tf), None
        )
    else:
        sit_state = next(
            (s for s in tf_states if not s.get("faster_tf_log_only")), None
        )
    faster_log = [
        {"tf": s.get("tf"), "reds": s.get("reds")}
        for s in tf_states
        if s.get("faster_tf_log_only") or str(s.get("tf") or "") == "1m"
    ]
    pack = load_process_pack(store)
    facts = facts_from(plan, snap, board=board)
    if last_price is None:
        last_price = facts.get("_last")
    if kill:
        facts["news_kill"] = True
        facts["not_news_kill"] = False
    verdict = interpret(pack, facts)
    if kill:
        verdict = {
            "action": "flatten-news",
            "rule_id": "fail.news",
            "rule_ids": ["fail.news"]
            + [i for i in (verdict.get("rule_ids") or []) if i != "fail.news"],
            "family": "fail",
            "priority": 0,
            "why": "News flatten.",
        }
    why_text = why_sentence(verdict)
    gate = {
        "tf_states": tf_states,
        "chosen": chosen,
        "news_kill": kill,
        "volume": volume,
        "kenneth_override": False,
        "play_tf": play_tf,
        "faster_tf_reds": faster_log,
        "faster_tf": "1m"
        if "1m" in reds_map
        else faster_tf_for(play_tf),
        "rule_id": verdict.get("rule_id"),
        "rule_ids": verdict.get("rule_ids"),
        "action": verdict.get("action"),
        "met": bool(plan.get("met"))
        or bars_ever_met(plan, snap, last=last_price),
        "process_version": pack.get("version"),
    }
    tape_reds = (
        (sit_state or {}).get("reds")
        if sit_state is not None
        else (chosen or {}).get("reds")
        if chosen
        else (tf_states[0].get("reds") if tf_states else None)
    )
    _tape_patch = {
        "last_price": last_price,
        "volume": volume,
        "volume_n": volume_n,
        "news": (kill or {}).get("class") if kill else None,
        "gate": gate,
        "met": 1
        if (plan.get("met") or bars_ever_met(plan, snap, last=last_price))
        else 0,
        "process_version": pack.get("version"),
        "decision": why_text,
        "decision_reason": str(verdict.get("action") or "wait").replace("-", "_"),
    }
    if tape_reds is not None:
        _tape_patch["reds"] = tape_reds
    if play_tf:
        _tape_patch["tf"] = play_tf
    if str(plan.get("status") or "") in ("killed", "blocked"):
        _tape_patch["decision"] = "Killed, not in play."
        _tape_patch["decision_reason"] = "killed"
        store.patch_plan(user_id, int(plan["id"]), **_tape_patch)
        return None
    store.patch_plan(user_id, int(plan["id"]), **_tape_patch)
    plan = store.get_plan(user_id, int(plan["id"]))

    action = str(verdict.get("action") or "wait")

    if plan.get("live"):
        bag = [
            o
            for o in store.list_orders(user_id, int(plan["id"]), status="filled")
            if o.get("side") != "sell"
        ]
        far = not facts.get("at_ad") and not facts.get("past_b") and not facts.get("past_panic")
        if not bag and far:
            store.cancel_working(user_id, int(plan["id"]))
            store.patch_plan(
                user_id,
                int(plan["id"]),
                live=False,
                status="watch",
                resting=False,
                allocated_usd=0,
                armed_at=None,
            )
            plan = store.get_plan(user_id, int(plan["id"]))

    if action == "flatten-news" or kill:
        why = {"decision": why_text, "decision_reason": "news"}
        store.patch_plan(user_id, int(plan["id"]), **why)
        if plan.get("live") or plan.get("status") not in ("killed",):
            store.cancel_working(user_id, int(plan["id"]))
            _close_and_kb(
                store,
                user_id,
                plan,
                reason="news_kill",
                bounce_or_fail="fail",
                process_ok=True,
                money_pnl=_realized_or_mark(
                    store,
                    user_id,
                    int(plan["id"]),
                    leftover_avg=plan.get("leftover_avg"),
                    last=last_price,
                ),
            )
            store.patch_plan(
                user_id,
                int(plan["id"]),
                status="blocked",
                live=False,
                resting=False,
                allocated_usd=0,
                armed_at=None,
                **why,
            )
            _write_log(
                store, user_id, plan, verdict, now=now, last=last_price, facts=facts
            )
            return {
                "ok": True,
                "action": "news_kill",
                "plan_id": plan["id"],
                **why,
                "rule_ids": verdict.get("rule_ids"),
            }
        return {
            "ok": True,
            "action": "news_blocked",
            "plan_id": plan["id"],
            **why,
            "rule_ids": verdict.get("rule_ids"),
        }

    if action in ("paper-buy", "add-panic"):
        if not plan.get("live"):
            cap = can_open_play(
                store.live_count(user_id), store.live_allocated(user_id)
            )
            if not cap["ok"]:
                why = decision_line(kind="cap")
                store.patch_plan(user_id, int(plan["id"]), **why)
                _write_log(
                    store,
                    user_id,
                    plan,
                    {**verdict, "action": "wait", "why": why["decision"]},
                    now=now,
                    last=last_price,
                    facts=facts,
                    extra={"skip_reason": cap["reason"]},
                )
                return {
                    "ok": False,
                    "action": "cap",
                    "reason": cap["reason"],
                    "plan_id": plan["id"],
                    **why,
                }
        filled = _paper_take(
            store,
            user_id,
            plan,
            last_price,
            verdict=verdict,
            facts=facts,
            now=now,
            budget_usd=can_open_play(
                store.live_count(user_id), store.live_allocated(user_id)
            ).get("budget_usd")
            or MAX_PER_PLAY_USD,
        )
        plan = store.get_plan(user_id, int(plan["id"]))
        _write_log(
            store,
            user_id,
            plan,
            verdict,
            now=now,
            last=last_price,
            facts=facts,
            extra={"filled": filled},
        )
        out_action = "paper_fill" if filled else action.replace("-", "_")
        if filled and action == "paper-buy":
            out_action = "paper_fill"
        elif action == "paper-buy" and plan.get("live"):
            out_action = "arm"
        return {
            "ok": True,
            "action": out_action,
            "plan_id": plan["id"],
            "filled": filled,
            "live_orders_sent": False,
            "decision": why_text,
            "decision_reason": str(action).replace("-", "_"),
            "rule_ids": verdict.get("rule_ids"),
            "tf": play_tf,
        }

    if action == "pull-pack" and plan.get("live"):
        lowered = _lower_pack(store, user_id, plan, last_price)
        _write_log(
            store,
            user_id,
            plan,
            verdict,
            now=now,
            last=last_price,
            facts=facts,
            extra={"lowered": lowered},
        )
        return {
            "ok": True,
            "action": "pull-pack",
            "plan_id": plan["id"],
            "decision": why_text,
            "rule_ids": verdict.get("rule_ids"),
            "live_orders_sent": False,
        }

    if action == "paper-sell" and plan.get("live"):
        rule = str(verdict.get("rule_id") or "")
        fraction = 1.0 if rule in ("exit.into_base", "exit.stale_ad") else 0.5
        filled = _paper_sell(
            store, user_id, plan, last_price, facts=facts, now=now, fraction=fraction
        )
        play = parse_json(plan.get("play_json"), {}) or {}
        play["sold_bounce"] = True
        still_usd = sum(
            float(o.get("usd") or 0)
            for o in store.list_orders(user_id, int(plan["id"]), status="filled")
            if o.get("side") != "sell"
        )
        why = {
            "decision": why_text,
            "decision_reason": "paper_sell",
        }
        if still_usd > 1e-9 and fraction < 1.0:
            store.patch_plan(
                user_id,
                int(plan["id"]),
                play=play,
                live=True,
                status="live",
                allocated_usd=round(still_usd, 4),
                remaining_bag_pct=round(
                    100.0 * still_usd / max(still_usd + sum(float(x.get("usd") or 0) for x in filled), 1e-9),
                    4,
                ),
                **why,
            )
        else:
            store.cancel_working(user_id, int(plan["id"]))
            _close_and_kb(
                store,
                user_id,
                plan,
                reason="bounce",
                bounce_or_fail="bounce",
                process_ok=True,
                money_pnl=_realized_or_mark(
                    store,
                    user_id,
                    int(plan["id"]),
                    leftover_avg=plan.get("leftover_avg"),
                    last=last_price,
                    filled=filled,
                ),
            )
            store.patch_plan(
                user_id,
                int(plan["id"]),
                status="closed",
                live=False,
                resting=False,
                allocated_usd=0,
                armed_at=None,
                play=play,
                **why,
            )
        _write_log(
            store,
            user_id,
            plan,
            verdict,
            now=now,
            last=last_price,
            facts=facts,
            extra={"filled": filled},
        )
        return {
            "ok": True,
            "action": "paper-sell",
            "plan_id": plan["id"],
            "filled": filled,
            "decision": why_text,
            "rule_ids": verdict.get("rule_ids"),
            "live_orders_sent": False,
        }

    _write_log(store, user_id, plan, verdict, now=now, last=last_price, facts=facts)
    mapped = action.replace("-", "_")
    if mapped == "sit_out":
        mapped = "sit_out"
    return {
        "ok": True,
        "action": mapped,
        "plan_id": plan["id"],
        "decision": why_text,
        "decision_reason": mapped,
        "rule_ids": verdict.get("rule_ids"),
    }


def _tape_worthy(action: str, facts: Dict[str, Any], filled_any: bool) -> bool:
    """Tape prints only the owner's decision list. Never wait. Never same pull-pack."""
    if action in ("wait", "pull-pack"):
        return False
    if action == "sit-out":
        return bool(facts.get("written_plan") and facts.get("at_ad"))
    if action in ("paper-buy", "paper-sell", "add-panic"):
        return bool(filled_any)
    return action in TAPE_ACTIONS


def _px(raw: Any) -> Optional[float]:
    try:
        x = float(raw)
    except (TypeError, ValueError):
        return None
    return x if x > 0 else None


def _is_sell_order(row: Optional[Dict[str, Any]]) -> bool:
    return str((row or {}).get("side") or "").lower() == "sell"


def buy_entry_px(
    orders: Sequence[Dict[str, Any]], leftover_avg: Any = None
) -> Optional[float]:
    """VWAP of filled buys. Never the sell print."""
    cost = 0.0
    qty = 0.0
    for o in orders or []:
        if not isinstance(o, dict) or o.get("status") != "filled" or _is_sell_order(o):
            continue
        px = _px(o.get("filled_price")) or _px(o.get("price"))
        try:
            usd = float(o.get("usd") or 0)
        except (TypeError, ValueError):
            continue
        if px and usd > 0:
            cost += usd
            qty += usd / px
    if qty > 0:
        return cost / qty
    return _px(leftover_avg)


def realized_close_pnl(
    orders: Sequence[Dict[str, Any]], leftover_avg: Any = None
) -> Optional[float]:
    """qty × (sell fill − buy fill). Sell intended is not entry."""
    entry = buy_entry_px(orders, leftover_avg)
    sells = [
        o
        for o in (orders or [])
        if isinstance(o, dict) and o.get("status") == "filled" and _is_sell_order(o)
    ]
    if not sells:
        return None
    if entry is None:
        for s in sells:
            p = _px(s.get("entry")) or _px(s.get("intended_price"))
            fp = _px(s.get("filled_price"))
            if p and fp and abs(p - fp) > 1e-18:
                entry = p
                break
    if entry is None:
        return None
    pnl = 0.0
    any_row = False
    for s in sells:
        px = _px(s.get("filled_price"))
        try:
            usd = float(s.get("usd") or 0)
        except (TypeError, ValueError):
            continue
        if not px or usd <= 0:
            continue
        pnl += (usd / entry) * (px - entry)
        any_row = True
    return round(pnl, 4) if any_row else None


def _close_fill_pnl(filled: List[Dict[str, Any]], leftover_avg: Any) -> Optional[float]:
    """Money made or lost on this close only. Entry is buy VWAP, never the sell print."""
    entry = _px(leftover_avg)
    pnl = 0.0
    any_row = False
    for row in filled or []:
        if not isinstance(row, dict):
            continue
        px = _px(row.get("filled_price"))
        try:
            usd = float(row.get("usd") or 0)
        except (TypeError, ValueError):
            continue
        ent = entry or _px(row.get("entry"))
        if not ent or not px or usd <= 0:
            continue
        pnl += (usd / ent) * (px - ent)
        any_row = True
    return round(pnl, 4) if any_row else None


def _realized_or_mark(
    store: MachineStore,
    user_id: int,
    plan_id: int,
    *,
    leftover_avg: Any = None,
    last: Optional[float] = None,
    filled: Optional[List[Dict[str, Any]]] = None,
) -> float:
    orders = store.list_orders(user_id, int(plan_id))
    pnl = realized_close_pnl(orders, leftover_avg)
    if pnl is None and filled:
        pnl = _close_fill_pnl(filled, leftover_avg)
    if pnl is None:
        pnl = paper_pnl(
            store, user_id, int(plan_id), last=last, leftover_avg=leftover_avg
        )
    return float(pnl or 0.0)


def recompute_closed_money(store: MachineStore, user_id: int) -> int:
    """Rewrite stored $0/null closes from actual buy vs sell fills."""
    n = 0
    plans = {int(p["id"]): p for p in store.list_plans(user_id)}
    for close in store.list_closes(user_id, limit=200):
        try:
            pid = int(close.get("plan_id") or 0)
            cid = int(close["id"])
        except (TypeError, ValueError, KeyError):
            continue
        plan = plans.get(pid) or {}
        pnl = realized_close_pnl(
            store.list_orders(user_id, pid), plan.get("leftover_avg")
        )
        if pnl is None:
            continue
        old = close.get("money_pnl")
        try:
            same = old is not None and abs(float(old) - pnl) < 1e-6
        except (TypeError, ValueError):
            same = False
        if same:
            continue
        store.patch_close_money(cid, pnl)
        for row in store.list_log(
            user_id, plan_id=pid, actions=("paper-sell",), limit=80
        ):
            prev = row.get("money_pnl")
            try:
                empty = prev is None or abs(float(prev)) < 1e-9
            except (TypeError, ValueError):
                empty = True
            if empty:
                store.patch_log_money(int(row["id"]), pnl)
        n += 1
    return n


def log_board_flip(store: MachineStore, user_id: int, board: Dict[str, Any]) -> None:
    """One tape line when list-wide grind or panic turns on or off."""
    global _board_prev
    now = time.time()
    grind = bool(board.get("grind"))
    panic = bool(board.get("panic"))
    confirmed_g = _board_prev.get("grind")
    confirmed_p = _board_prev.get("panic")
    pending = dict(_board_prev.get("pending") or {})
    flips = []

    def _hold(key: str, new: bool, confirmed: Optional[bool], on: str, off: str, why_on: str, why_off: str):
        if confirmed is None:
            pending.pop(key, None)
            return new, []
        if new == confirmed:
            pending.pop(key, None)
            return confirmed, []
        if pending.get(key) == new:
            pending.pop(key, None)
            return new, [(on if new else off, why_on if new else why_off)]
        pending[key] = new
        return confirmed, []

    grind, gflip = _hold(
        "grind",
        grind,
        confirmed_g,
        "grind-on",
        "grind-off",
        "Board-wide grind on.",
        "Board-wide grind off.",
    )
    panic, pflip = _hold(
        "panic",
        panic,
        confirmed_p,
        "panic-on",
        "panic-off",
        "Board-wide panic on.",
        "Board-wide panic off.",
    )
    flips.extend(gflip)
    flips.extend(pflip)
    _board_prev = {
        "grind": grind if confirmed_g is not None else grind,
        "panic": panic if confirmed_p is not None else panic,
        "pending": pending,
        "names": board.get("names"),
    }
    if confirmed_g is None and confirmed_p is None:
        _board_prev["grind"] = grind
        _board_prev["panic"] = panic
        return
    for action, why in flips:
        store.insert_log(
            user_id,
            {
                "ts": now,
                "manila": manila_label(now),
                "symbol": "BOARD",
                "tf": "",
                "action": action,
                "why": why,
                "rule_ids": ["board." + action],
                "payload": dict(board),
            },
        )


def _map_log_action(raw: Any) -> str:
    a = str(raw or "wait")
    if a in ("arm", "paper_fill", "paper-buy", "paper_buy"):
        return "paper-buy"
    if a in ("bounce", "paper-sell", "paper_sell"):
        return "paper-sell"
    if a in ("sit_out", "sit-out"):
        return "sit-out"
    if a in ("flatten-news", "news_kill", "news"):
        return "flatten-news"
    return a


def paper_pnl(
    store: MachineStore,
    user_id: int,
    plan_id: int,
    *,
    last: Optional[float] = None,
    leftover_avg: Any = None,
) -> float:
    """Mark-to-last paper P&L from filled buys vs sells. Never invent fills."""
    orders = store.list_orders(user_id, int(plan_id))
    buys = [
        o
        for o in orders
        if o.get("status") == "filled" and not _is_sell_order(o)
    ]
    sells = [
        o for o in orders if o.get("status") == "filled" and _is_sell_order(o)
    ]
    cost = 0.0
    qty = 0.0
    for o in buys:
        px = _px(o.get("filled_price")) or _px(o.get("price"))
        try:
            usd = float(o.get("usd") or 0)
        except (TypeError, ValueError):
            continue
        if not px or usd <= 0:
            continue
        cost += usd
        qty += usd / px
    avg = (cost / qty) if qty > 0 else _px(leftover_avg)
    if not avg:
        got = realized_close_pnl(orders, leftover_avg)
        return float(got or 0.0)
    if qty <= 0:
        got = realized_close_pnl(orders, leftover_avg)
        return float(got or 0.0)
    sold_qty = 0.0
    proceeds = 0.0
    for o in sells:
        px = _px(o.get("filled_price"))
        try:
            usd = float(o.get("usd") or 0)
        except (TypeError, ValueError):
            continue
        if not px or usd <= 0:
            continue
        q = usd / avg
        sold_qty += q
        proceeds += q * px
    remain = max(0.0, qty - sold_qty)
    try:
        mark = float(last) if last is not None else avg
    except (TypeError, ValueError):
        mark = avg
    return round(proceeds + remain * float(mark) - cost, 4)


def _write_log(
    store: MachineStore,
    user_id: int,
    plan: Dict[str, Any],
    verdict: Dict[str, Any],
    *,
    now: float,
    last: Optional[float],
    facts: Dict[str, Any],
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    extra = extra or {}
    action = _map_log_action(verdict.get("action"))
    if (
        action == "wait"
        and facts.get("written_plan")
        and facts.get("at_ad")
        and not facts.get("sold_bounce")
        and not facts.get("live")
    ):
        action = "sit-out"
        verdict = {
            **verdict,
            "action": "sit-out",
            "rule_id": "atad.miss",
            "why": "Written plan at this chart's AD, sat out.",
        }
    filled = extra.get("filled") or []
    filled_px = None
    intended = extra.get("intended_price")
    size_pct = extra.get("size_pct")
    if isinstance(filled, list) and filled:
        try:
            filled_px = float(filled[-1].get("filled_price") or filled[-1].get("price"))
            if intended is None:
                intended = float(filled[0].get("price"))
            if size_pct is None:
                size_pct = filled[-1].get("size_pct")
        except (TypeError, ValueError, AttributeError):
            pass
    why = why_sentence(verdict)
    filled_any = isinstance(filled, list) and any(
        (row or {}).get("filled_price") is not None for row in filled if isinstance(row, dict)
    )
    if not _tape_worthy(action, facts, filled_any):
        return
    if action in _FILLISH:
        _write_fill_rows(
            store,
            user_id,
            plan,
            verdict,
            action=action,
            why=why,
            last=last,
            now=now,
            facts=facts,
            filled=filled if isinstance(filled, list) else [],
        )
        return
    prev = store.list_log(
        user_id, plan_id=int(plan["id"]), actions=(action,), limit=1
    )
    if prev and str(prev[0].get("why") or "") == why:
        return
    pnl = None
    if action == "flatten-news":
        pnl = extra.get("money_pnl")
        if pnl is None:
            pnl = paper_pnl(
                store,
                user_id,
                int(plan["id"]),
                last=last,
                leftover_avg=plan.get("leftover_avg"),
            )
        leftover = plan.get("leftover_avg")
        intended = leftover if leftover is not None else intended
        filled_px = last if last is not None else filled_px
        if size_pct is None:
            size_pct = plan.get("remaining_bag_pct")
    store.insert_log(
        user_id,
        {
            "plan_id": int(plan["id"]),
            "ts": now,
            "manila": manila_label(now),
            "symbol": plan.get("symbol"),
            "market": plan.get("market"),
            "tf": plan.get("tf"),
            "last_price": last,
            "action": action,
            "size_pct": size_pct,
            "rule_ids": [verdict.get("rule_id")] if verdict.get("rule_id") else (verdict.get("rule_ids") or []),
            "why": why,
            "vol_usd_play": facts.get("_vol_usd_play"),
            "vol_usd_fast": facts.get("_vol_usd_fast"),
            "intended_price": intended,
            "filled_price": filled_px,
            "skip_reason": extra.get("skip_reason") or extra.get("skipped"),
            "payload": extra,
            "money_pnl": pnl,
        },
    )


def _write_fill_rows(
    store: MachineStore,
    user_id: int,
    plan: Dict[str, Any],
    verdict: Dict[str, Any],
    *,
    action: str,
    why: str,
    last: Optional[float],
    now: float,
    facts: Dict[str, Any],
    filled: List[Any],
) -> None:
    """One tape row per real fill. Intended, filled, size; PnL only on a close."""
    purge_empty_fills(store, user_id)
    purge_unreached_buys(store, user_id)
    for row in filled:
        if not isinstance(row, dict):
            continue
        try:
            filled_px = float(row["filled_price"]) if row.get("filled_price") is not None else None
            intended = float(row["price"]) if row.get("price") is not None else None
        except (TypeError, ValueError):
            continue
        if filled_px is None or intended is None:
            continue
        if (
            action in ("paper-buy", "add-panic")
            and last is not None
            and not last_reached_layer(last, intended)
        ):
            continue
        size_pct = row.get("size_pct")
        if size_pct is None:
            try:
                usd = float(row.get("usd") or 0)
                size_pct = round(usd / MAX_PER_PLAY_USD * 100.0, 4) if usd else None
            except (TypeError, ValueError):
                size_pct = None
        pnl = None
        if action == "paper-sell":
            pnl = _close_fill_pnl([row], plan.get("leftover_avg") or row.get("entry"))
        prev = store.list_log(
            user_id, plan_id=int(plan["id"]), actions=(action,), limit=1
        )
        if prev and str(prev[0].get("why") or "") == why:
            prev_fp = prev[0].get("filled_price")
            if prev_fp is None:
                pass
            else:
                try:
                    if abs(float(prev_fp) - filled_px) < 1e-12:
                        continue
                except (TypeError, ValueError):
                    pass
        store.insert_log(
            user_id,
            {
                "plan_id": int(plan["id"]),
                "ts": now,
                "manila": manila_label(now),
                "symbol": plan.get("symbol"),
                "market": plan.get("market"),
                "tf": plan.get("tf"),
                "last_price": last,
                "action": action,
                "size_pct": size_pct,
                "rule_ids": [verdict.get("rule_id")]
                if verdict.get("rule_id")
                else (verdict.get("rule_ids") or []),
                "why": why,
                "vol_usd_play": facts.get("_vol_usd_play"),
                "vol_usd_fast": facts.get("_vol_usd_fast"),
                "intended_price": intended,
                "filled_price": filled_px,
                "money_pnl": pnl,
                "payload": {"idx": row.get("idx"), "usd": row.get("usd")},
            },
        )


def _paper_take(
    store: MachineStore,
    user_id: int,
    plan: Dict[str, Any],
    last_price: Optional[float],
    *,
    verdict: Dict[str, Any],
    facts: Dict[str, Any],
    now: float,
    budget_usd: float,
) -> List[Dict[str, Any]]:
    """Paper-buy or add-panic. Fill at last. Never send to MEXC."""
    layers = parse_json(plan.get("layers_json"), [])
    if not layers:
        layers = dump_depth_layers(
            plan.get("ad_top"), plan.get("ad_bottom"), budget_usd=budget_usd
        )
    if not layers:
        return []
    try:
        px = float(last_price) if last_price is not None else None
    except (TypeError, ValueError):
        px = None
    rule = str(verdict.get("rule_id") or "")
    action = str(verdict.get("action") or "")
    take: List[Dict[str, Any]] = []
    scale = 1.0
    if facts.get("board_grind") and rule != "atad.take":
        scale *= 0.5
    filled_idx = {
        int(o.get("layer_idx") or 0)
        for o in store.list_orders(user_id, int(plan["id"]), status="filled")
        if o.get("side") != "sell"
    }
    if rule == "size.nibble":
        usd = round(MAX_PER_PLAY_USD * 0.10, 4)
        nibble_px = px if px is not None else float(
            (at_ad_layer(layers, plan.get("ad_bottom")) or {}).get("price") or 0
        )
        if nibble_px <= 0:
            return []
        play = parse_json(plan.get("play_json"), {}) or {}
        play["nibble_done"] = True
        if not plan.get("live"):
            store.replace_working_orders(user_id, int(plan["id"]), [])
        row = store.insert_order(
            user_id,
            int(plan["id"]),
            layer_idx=0,
            price=nibble_px,
            usd=usd,
            status="filled",
            side="buy",
            filled_price=nibble_px,
            size_pct=10.0,
            band="nibble",
        )
        store.patch_plan(
            user_id,
            int(plan["id"]),
            play=play,
            status="live",
            live=True,
            resting=False,
            leftover_avg=nibble_px,
            remaining_bag_pct=10.0,
            allocated_usd=usd,
            remaining_layers=0,
            next_layer_usd=None,
            armed_at=now,
            layers=layers,
        )
        return [
            {
                "id": (row or {}).get("id"),
                "idx": 0,
                "price": nibble_px,
                "filled_price": nibble_px,
                "usd": usd,
                "size_pct": 10.0,
                "band": "nibble",
            }
        ]
    elif action == "add-panic" or rule == "fail.add_panic":
        for layer in layers:
            if str(layer.get("band") or "") != "panic":
                continue
            if int(layer.get("idx") or 0) in filled_idx:
                continue
            if px is None:
                take.append(layer)
                break
            if px <= float(layer["price"]):
                take.append(layer)
    elif rule == "atad.take":
        target = at_ad_layer(layers, plan.get("ad_bottom"))
        if target and px is not None and facts.get("at_ad"):
            take = [target]
    else:
        ad = [L for L in layers if str(L.get("band") or "ad") == "ad"]
        tagged = []
        if px is not None:
            tagged = [L for L in ad if px <= float(L["price"])]
        if tagged:
            first = tagged[0]
            try:
                if int(first.get("idx") or 0) >= 4:
                    scale = 0.5
            except (TypeError, ValueError):
                pass
            take = tagged

    if px is not None:
        take = [L for L in take if px <= float(L.get("price") or 0)]
    if rule == "atad.take" and px is not None:
        take = [L for L in take if facts.get("at_ad")]
    if not take:
        return []

    working = store.list_orders(user_id, int(plan["id"]), status="working")
    if not working:
        plant = [L for L in layers if int(L.get("idx") or 0) not in filled_idx]
        if plant:
            store.replace_working_orders(user_id, int(plan["id"]), plant)

    filled: List[Dict[str, Any]] = []
    working = store.list_orders(user_id, int(plan["id"]), status="working")
    by_idx = {int(o.get("layer_idx") or 0): o for o in working}
    fill_at = px if px is not None else None
    for layer in take:
        idx = int(layer.get("idx") or 0)
        if idx in filled_idx:
            continue
        order = by_idx.get(idx)
        if not order:
            continue
        if fill_at is None:
            continue
        line = float(order.get("price"))
        # A paper buy only counts when last actually trades at that layer.
        if fill_at > line:
            continue
        usd = float(order.get("usd") or layer.get("usd") or 0) * scale
        store.fill_order(user_id, int(order["id"]), filled_price=fill_at)
        filled.append(
            {
                "id": order["id"],
                "idx": idx,
                "price": line,
                "filled_price": fill_at,
                "usd": round(usd, 4),
                "size_pct": float(layer.get("size_pct") or 0) * scale,
                "band": layer.get("band"),
            }
        )
    left = store.list_orders(user_id, int(plan["id"]), status="working")
    done = [
        o
        for o in store.list_orders(user_id, int(plan["id"]), status="filled")
        if o.get("side") != "sell"
    ]
    leftover = None
    bag = 0.0
    notion = 0.0
    qty_sum = 0.0
    for o in done:
        try:
            fp = float(o.get("filled_price") or o.get("price") or 0)
            usd = float(o.get("usd") or 0)
        except (TypeError, ValueError):
            continue
        bag += usd
        if fp > 0:
            qty_sum += usd / fp
            notion += usd
    if qty_sum > 0:
        leftover = round(notion / qty_sum, 8)
    if not filled and not done:
        return []
    play_budget = MAX_PER_PLAY_USD or 100.0
    remaining_pct = max(0.0, 100.0 - (bag / play_budget) * 100.0) if play_budget else 0.0
    store.patch_plan(
        user_id,
        int(plan["id"]),
        remaining_layers=len(left),
        next_layer_usd=left[0]["usd"] if left else None,
        resting=bool(left),
        leftover_avg=leftover,
        remaining_bag_pct=round(remaining_pct, 4),
        allocated_usd=round(bag, 4),
        live=True,
        status="live",
        armed_at=plan.get("armed_at") or now,
    )
    return filled


def _lower_pack(
    store: MachineStore,
    user_id: int,
    plan: Dict[str, Any],
    last_price: Optional[float],
) -> List[Dict[str, Any]]:
    """Move remaining working prices down. Do not recut hung T/B."""
    try:
        last = float(last_price) if last_price is not None else None
        bot = float(plan.get("ad_bottom")) if plan.get("ad_bottom") is not None else None
        top = float(plan.get("ad_top")) if plan.get("ad_top") is not None else None
    except (TypeError, ValueError):
        return []
    if last is None or top is None or bot is None:
        return []
    working = store.list_orders(user_id, int(plan["id"]), status="working")
    pulled: List[Dict[str, Any]] = []
    if last < bot:
        layers = dump_depth_layers(top, last, budget_usd=MAX_PER_PLAY_USD)
        filled_idx = {
            int(o.get("layer_idx") or 0)
            for o in store.list_orders(user_id, int(plan["id"]), status="filled")
            if o.get("side") != "sell"
        }
        remaining = [L for L in layers if int(L.get("idx") or 0) not in filled_idx]
        old_px = [
            round(float(o.get("price") or 0), 8)
            for o in working
        ]
        new_px = [round(float(L.get("price") or 0), 8) for L in remaining]
        if old_px == new_px:
            return []
        store.replace_working_orders(user_id, int(plan["id"]), remaining)
        pulled = remaining
    else:
        for order in working:
            try:
                px = float(order.get("price") or 0)
            except (TypeError, ValueError):
                continue
            if px > last:
                store.patch_order(user_id, int(order["id"]), price=last, intended_price=last)
                pulled.append({**order, "price": last})
    left = store.list_orders(user_id, int(plan["id"]), status="working")
    store.patch_plan(
        user_id,
        int(plan["id"]),
        remaining_layers=len(left),
        next_layer_usd=left[0]["usd"] if left else None,
        resting=bool(left),
    )
    return pulled


def _paper_sell(
    store: MachineStore,
    user_id: int,
    plan: Dict[str, Any],
    last_price: Optional[float],
    *,
    facts: Dict[str, Any],
    now: float,
    fraction: float = 1.0,
) -> List[Dict[str, Any]]:
    del facts, now
    filled_buys = [
        o
        for o in store.list_orders(user_id, int(plan["id"]), status="filled")
        if o.get("side") != "sell"
    ]
    if not filled_buys:
        return []
    try:
        px = float(last_price) if last_price is not None else None
    except (TypeError, ValueError):
        px = None
    if px is None or px <= 0:
        return []
    leftover = _px(plan.get("leftover_avg"))
    bag = sum(float(o.get("usd") or 0) for o in filled_buys)
    target = max(0.0, bag * max(0.0, min(1.0, float(fraction))))
    sold: List[Dict[str, Any]] = []
    acc = 0.0
    for order in reversed(filled_buys):
        if acc >= target - 1e-9:
            break
        usd = float(order.get("usd") or 0)
        take_usd = round(min(usd, target - acc), 4)
        if take_usd <= 0:
            break
        buy_fill = _px(order.get("filled_price")) or leftover
        buy_line = (
            _px(order.get("intended_price"))
            or _px(order.get("price"))
            or buy_fill
            or px
        )
        size_pct = order.get("size_pct")
        if size_pct is None:
            try:
                size_pct = round(take_usd / MAX_PER_PLAY_USD * 100.0, 4)
            except (TypeError, ValueError, ZeroDivisionError):
                size_pct = None
        row = store.insert_order(
            user_id,
            int(plan["id"]),
            layer_idx=int(order.get("layer_idx") or 0),
            price=float(buy_line),
            usd=take_usd,
            status="filled",
            side="sell",
            filled_price=px,
            size_pct=size_pct,
            band="exit",
        )
        if take_usd < usd - 1e-9:
            store.patch_order(
                user_id, int(order["id"]), usd=round(usd - take_usd, 4)
            )
        acc += take_usd
        sold.append(
            {
                "id": row.get("id") if row else order.get("id"),
                "idx": order.get("layer_idx"),
                "price": buy_line,
                "filled_price": px,
                "entry": buy_fill or leftover,
                "usd": take_usd,
                "size_pct": size_pct,
                "side": "sell",
                "partial": take_usd < usd - 1e-9,
            }
        )
    return sold


def _paper_fill(
    store: MachineStore,
    user_id: int,
    plan: Dict[str, Any],
    last_price: Optional[float],
) -> List[Dict[str, Any]]:
    """Mark working layers filled when official last tags the price. No send."""
    if last_price is None:
        return []
    try:
        px = float(last_price)
    except (TypeError, ValueError):
        return []
    if px <= 0:
        return []
    working = store.list_orders(user_id, int(plan["id"]), status="working")
    filled: List[Dict[str, Any]] = []
    for order in working:
        try:
            line = float(order.get("price"))
        except (TypeError, ValueError):
            continue
        if px <= line:
            store.fill_order(user_id, int(order["id"]), filled_price=px)
            filled.append(
                {
                    "id": order["id"],
                    "idx": order.get("layer_idx"),
                    "price": line,
                    "filled_price": px,
                    "usd": order.get("usd"),
                    "size_pct": order.get("size_pct"),
                }
            )
    if not filled:
        return []
    left = store.list_orders(user_id, int(plan["id"]), status="working")
    store.patch_plan(
        user_id,
        int(plan["id"]),
        remaining_layers=len(left),
        next_layer_usd=left[0]["usd"] if left else None,
        resting=bool(left),
    )
    return filled


def _close_and_kb(
    store: MachineStore,
    user_id: int,
    plan: Dict[str, Any],
    *,
    reason: str,
    bounce_or_fail: str,
    process_ok: bool,
    money_pnl: float,
) -> Dict[str, Any]:
    close = store.insert_close(
        user_id,
        {
            "plan_id": int(plan["id"]),
            "symbol": plan.get("symbol"),
            "market": plan.get("market"),
            "tf": plan.get("tf"),
            "reason": reason,
            "reds": plan.get("reds"),
            "volume": plan.get("volume"),
            "bounce_or_fail": bounce_or_fail,
            "process_ok": process_ok,
            "money_pnl": money_pnl,
            "allocated_usd": plan.get("allocated_usd"),
            "payload": {"reason": reason},
        },
    )
    habit = plan.get("reds") if bounce_or_fail == "bounce" else None
    if habit is not None:
        try:
            habit = int(habit)
        except (TypeError, ValueError):
            habit = None
    kb = store.insert_kb(
        user_id,
        {
            "close_id": int(close["id"]),
            "plan_id": int(plan["id"]),
            "symbol": plan.get("symbol"),
            "market": plan.get("market"),
            "tf": plan.get("tf"),
            "reds": plan.get("reds"),
            "volume": plan.get("volume"),
            "bounce_or_fail": bounce_or_fail,
            "process_ok": process_ok,
            "money_pnl": money_pnl,
            "habit_reds": habit,
        },
    )
    return {"close": close, "kb": kb}


def rank_plans(store: MachineStore, user_id: int) -> List[Dict[str, Any]]:
    from .logic import ad_gap_frac

    close_pnl: Dict[int, Any] = {}
    list_closes = getattr(store, "list_closes", None)
    if callable(list_closes):
        for c in list_closes(user_id, limit=200) or []:
            try:
                pid = int(c.get("plan_id") or 0)
            except (TypeError, ValueError):
                continue
            if pid and pid not in close_pnl:
                close_pnl[pid] = c.get("money_pnl")
    plans = [
        public_plan(store, p, close_money=close_pnl.get(int(p["id"])))
        for p in store.list_plans(user_id)
    ]
    kb_rows = store.list_kb(user_id, limit=200)
    scores: Dict[int, float] = {}
    for r in kb_rows:
        pid = int(r.get("plan_id") or 0)
        scores.setdefault(pid, 0.0)
        if r.get("bounce_or_fail") == "bounce":
            scores[pid] += 2.0
        elif r.get("bounce_or_fail") == "fail":
            scores[pid] -= 1.0
        if r.get("process_ok"):
            scores[pid] += 0.5
        try:
            scores[pid] += 0.01 * float(r.get("money_pnl") or 0)
        except (TypeError, ValueError):
            pass
    for p in plans:
        p["rank_score"] = round(scores.get(int(p["id"]), 0.0), 4)
        gap = None
        if (p.get("ad_status") or "") == "known":
            gap = ad_gap_frac(p.get("last_price"), p.get("ad_bottom"))
        p["ad_gap_frac"] = gap
    live = [p for p in plans if p.get("live")]
    rest = [p for p in plans if not p.get("live")]
    live.sort(key=lambda p: (-p["rank_score"], p.get("display") or ""))

    def _rest_key(p: Dict[str, Any]):
        g = p.get("ad_gap_frac")
        unknown = g is None
        killed = str(p.get("status") or "") in ("killed", "blocked")
        return (
            1 if killed else 0,
            1 if unknown else 0,
            9e9 if unknown else float(g),
            -float(p.get("rank_score") or 0),
            str(p.get("display") or ""),
        )

    rest.sort(key=_rest_key)
    ranked = live + rest
    for i, p in enumerate(ranked, 1):
        p["rank"] = i
    return ranked


def public_plan(
    store: MachineStore,
    row: Optional[Dict[str, Any]],
    *,
    close_money: Any = None,
) -> Dict[str, Any]:
    if not row:
        return {}
    raw_layers = parse_json(row.get("layers_json"), [])
    zones = parse_json(row.get("zones_json"), [])
    known = (row.get("ad_status") == "known") and row.get("ad_top") is not None
    # Unknown AD: never invent a ladder.
    layers = list(raw_layers) if known else []
    all_orders = store.list_orders(int(row["user_id"]), int(row["id"]))
    working = [o for o in all_orders if o.get("status") == "working"]
    filled_orders = [
        o
        for o in all_orders
        if o.get("status") == "filled" and o.get("side") != "sell"
    ]
    sell_orders = [
        o for o in all_orders if o.get("status") == "filled" and o.get("side") == "sell"
    ]
    by_idx = {int(o.get("layer_idx") or 0): o for o in all_orders}
    next_idx = None
    if working:
        next_idx = int(working[0].get("layer_idx") or 0)
    layer_idxs = {int(L.get("idx") or 0) for L in layers}
    public_layers = []
    for order in filled_orders:
        idx = int(order.get("layer_idx") or 0)
        if idx in layer_idxs:
            continue
        public_layers.append(
            {
                "idx": idx,
                "price": order.get("filled_price") or order.get("price"),
                "usd": order.get("usd"),
                "size_pct": order.get("size_pct") or 10.0,
                "band": order.get("band") or "nibble",
                "status": "filled",
                "next": False,
            }
        )
    for layer in layers:
        item = dict(layer)
        idx = int(item.get("idx") or 0)
        order = by_idx.get(idx)
        item["status"] = (order or {}).get("status") or "planned"
        item["next"] = bool(next_idx is not None and idx == next_idx)
        public_layers.append(item)
    intended_entry = None
    last_px = row.get("last_price")
    if working:
        w0 = working[0]
        layer_px = w0.get("intended_price") or w0.get("price")
        if last_px is None or last_reached_layer(last_px, layer_px):
            intended_entry = {
                "price": layer_px,
                "usd": w0.get("usd"),
                "idx": w0.get("layer_idx"),
            }
    elif known and layers:
        target = at_ad_layer(layers, row.get("ad_bottom")) or layers[0]
        layer_px = target.get("price")
        if last_px is None or last_reached_layer(last_px, layer_px):
            intended_entry = {
                "price": layer_px,
                "usd": target.get("usd"),
                "idx": target.get("idx"),
            }
    filled_entry = None
    if filled_orders:
        last_fill = filled_orders[-1]
        filled_entry = {
            "price": last_fill.get("filled_price") or last_fill.get("price"),
            "usd": last_fill.get("usd"),
            "idx": last_fill.get("layer_idx"),
            "at": last_fill.get("filled_at"),
        }
    intended_exit = None
    filled_exit = None
    if sell_orders:
        s0 = sell_orders[-1]
        filled_exit = {
            "price": s0.get("filled_price") or s0.get("price"),
            "usd": s0.get("usd"),
        }
        intended_exit = dict(filled_exit)
    elif filled_orders and row.get("live"):
        intended_exit = {"note": "re-read this TF"}
    ad_line = "unknown"
    if known:
        ad_line = f"{_num(row.get('ad_top'))} → {_num(row.get('ad_bottom'))}"
    money_pnl = close_money
    status = str(row.get("status") or "")
    closedish = bool(row.get("live")) or status in ("closed", "killed", "blocked")
    stale_zero = False
    try:
        stale_zero = (
            status == "closed"
            and bool(sell_orders)
            and money_pnl is not None
            and abs(float(money_pnl)) < 1e-9
        )
    except (TypeError, ValueError):
        stale_zero = False
    if closedish and (money_pnl is None or stale_zero):
        try:
            money_pnl = paper_pnl(
                store,
                int(row["user_id"]),
                int(row["id"]),
                last=row.get("last_price"),
                leftover_avg=row.get("leftover_avg"),
            )
        except (TypeError, ValueError, KeyError):
            if not stale_zero:
                money_pnl = None
    return {
        "id": row["id"],
        "symbol": row.get("symbol"),
        "market": row.get("market"),
        "display": row.get("display_name"),
        "name": row.get("display_name"),
        "tf": row.get("tf") or "unknown",
        "ad": ad_line,
        "ad_top": row.get("ad_top"),
        "ad_bottom": row.get("ad_bottom"),
        "ad_status": row.get("ad_status") or "unknown",
        "ad_source": row.get("ad_source"),
        "ad_note": row.get("ad_note"),
        "bar_top_label": row.get("bar_top_label") or "unknown",
        "bar_bottom_label": row.get("bar_bottom_label") or "unknown",
        "initial_drop_top": row.get("initial_drop_top"),
        "initial_drop_bottom": row.get("initial_drop_bottom"),
        "zones": zones,
        "layers": public_layers,
        "remaining_layers": row.get("remaining_layers"),
        "next_layer_usd": row.get("next_layer_usd"),
        "last_price": row.get("last_price"),
        "reds": row.get("reds") if row.get("reds") is not None else "unknown",
        "volume": row.get("volume") or "unknown",
        "volume_n": row.get("volume_n"),
        "vol_usd": row.get("volume_n"),
        "news": row.get("news"),
        "decision": row.get("decision") or "",
        "decision_reason": row.get("decision_reason"),
        "met": bool(row.get("met")),
        "paper_leftover_avg": row.get("leftover_avg"),
        "remaining_bag_pct": row.get("remaining_bag_pct"),
        "intended_entry": intended_entry,
        "filled_entry": filled_entry,
        "intended_exit": intended_exit,
        "filled_exit": filled_exit,
        "resting": bool(row.get("resting")),
        "armed_at": row.get("armed_at"),
        "live": bool(row.get("live")),
        "status": row.get("status"),
        "money_pnl": money_pnl,
        "allocated_usd": row.get("allocated_usd") or 0,
        "working_orders": [
            {
                "id": o["id"],
                "idx": o.get("layer_idx"),
                "price": o.get("price"),
                "usd": o.get("usd"),
                "status": o.get("status"),
            }
            for o in working
        ],
        "gate": parse_json(row.get("gate_json"), {}),
        "leverage": 1,
        "book": "machine",
    }


def public_need(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "kind": row.get("kind"),
        "status": row.get("status"),
        "symbol": row.get("symbol"),
        "market": row.get("market"),
        "payload": parse_json(row.get("payload_json"), {}),
        "created_at": row.get("created_at"),
        "resolved_at": row.get("resolved_at"),
    }


def public_close(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "plan_id": row.get("plan_id"),
        "symbol": row.get("symbol"),
        "market": row.get("market"),
        "tf": row.get("tf"),
        "reason": row.get("reason"),
        "reds": row.get("reds"),
        "volume": row.get("volume"),
        "bounce_or_fail": row.get("bounce_or_fail"),
        "process_ok": bool(row.get("process_ok")),
        "money_pnl": row.get("money_pnl"),
        "closed_at": row.get("closed_at"),
    }


def public_kb(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "close_id": row.get("close_id"),
        "plan_id": row.get("plan_id"),
        "symbol": row.get("symbol"),
        "tf": row.get("tf"),
        "reds": row.get("reds"),
        "volume": row.get("volume"),
        "bounce_or_fail": row.get("bounce_or_fail"),
        "process": "ok" if row.get("process_ok") else "miss",
        "money_pnl": row.get("money_pnl"),
        "habit_reds": row.get("habit_reds"),
        "created_at": row.get("created_at"),
    }


def room_state(
    plans: List[Dict[str, Any]],
    needs: List[Dict[str, Any]],
    *,
    closes: Optional[List[Dict[str, Any]]] = None,
    kb: Optional[List[Dict[str, Any]]] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Page-room flags + a living line. Never invents AD ticks."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from .settings import MANILA_TZ

    live = [p for p in plans if p.get("live")]
    hangar = [p for p in plans if not p.get("live")]
    hung = [
        p
        for p in plans
        if p.get("ad_status") == "known" and p.get("ad_top") is not None
    ]
    uncut = [p for p in plans if p not in hung]
    need_n = len(needs or [])
    if need_n:
        tone = "needs_you"
    elif live:
        tone = "live"
    elif not plans:
        tone = "empty"
    else:
        tone = "watch"
    wall = float(now if now is not None else time.time())
    manila = datetime.fromtimestamp(wall, tz=ZoneInfo(MANILA_TZ))
    last = (closes or [None])[0] if closes else None
    last_close = None
    if last:
        last_close = {
            "symbol": last.get("symbol"),
            "reason": last.get("reason"),
            "bounce_or_fail": last.get("bounce_or_fail"),
        }
    hung_names = [str(p.get("name") or p.get("display") or p.get("symbol")) for p in hung]
    return {
        "empty": not plans,
        "live_count": len(live),
        "open_slots": max(0, 2 - len(live)),
        "hangar_count": len(hangar),
        "needs_you_count": need_n,
        "needs_you_clear": need_n == 0,
        "tone": tone,
        "hung_count": len(hung),
        "uncut_count": len(uncut),
        "hung_names": hung_names,
        "kb_count": len(kb or []),
        "manila": manila.strftime("%H:%M PHT"),
        "invitation": _invitation(tone, hung_names, len(live), need_n),
        "last_close": last_close,
    }


def _invitation(
    tone: str,
    hung_names: List[str],
    live_n: int,
    need_n: int,
) -> str:
    if tone == "needs_you":
        return "The book is waiting on you."
    if tone == "live":
        return "A play is on. Recut the line or kill from the plan."
    if tone == "empty":
        return "Quiet book. Six seeds hang when the machine is on."
    if hung_names:
        shown = " and ".join(hung_names[:2])
        extra = f" +{len(hung_names) - 2}" if len(hung_names) > 2 else ""
        return f"{shown}{extra} already have a line. Both berths are open."
    return "Both berths are open. Powder ready — $100 into a line, 1x."


def account_view(store: MachineStore, user_id: int) -> Dict[str, Any]:
    live_n = store.live_count(user_id)
    allocated = store.live_allocated(user_id)
    return {
        "equity_usd": EQUITY_USD,
        "max_per_play_usd": MAX_PER_PLAY_USD,
        "max_live_plays": 2,
        "leverage": 1,
        "live_plays": live_n,
        "allocated_usd": allocated,
        "cash_usd": round(EQUITY_USD - allocated, 4),
        "book": "machine",
        "mixed_into_positions": False,
    }


def get_store(db_path: Optional[Path] = None) -> MachineStore:
    if db_path is None:
        from ..webapi import db as desk_db

        db_path = desk_db.db_path()
    return MachineStore(Path(db_path))


def _num(v: Any) -> str:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "unknown"
    if x >= 100:
        return f"{x:.2f}"
    if x >= 1:
        return f"{x:.4f}".rstrip("0").rstrip(".")
    return f"{x:.6f}".rstrip("0").rstrip(".")
