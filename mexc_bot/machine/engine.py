"""Machine loop: seed, recut, arm simulated layers, close → KB, rank.

No MEXC private keys. No live sends. Isolated from Positions leftover-avg.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .hang import hang_ad, volume_label
from .logic import (
    can_open_play,
    exponential_layers,
    failed_ad,
    news_kill,
    pick_working_tf,
    reds_required,
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
        layers = exponential_layers(
            hung.get("ad_top"),
            hung.get("ad_bottom"),
            count=DEFAULT_LAYER_COUNT,
            budget_usd=MAX_PER_PLAY_USD,
            zone_prices=hung.get("zones"),
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
) -> Dict[str, Any]:
    plan = store.get_plan(user_id, plan_id)
    if not plan:
        raise KeyError("plan not found")
    top = float(ad_top) if ad_top is not None else plan.get("ad_top")
    bot = float(ad_bottom) if ad_bottom is not None else plan.get("ad_bottom")
    n = (
        int(remaining_layers)
        if remaining_layers is not None
        else int(plan.get("remaining_layers") or DEFAULT_LAYER_COUNT)
    )
    zones = parse_json(plan.get("zones_json"), [])
    layers = exponential_layers(
        top, bot, count=n, budget_usd=MAX_PER_PLAY_USD, zone_prices=zones
    )
    known = top is not None and bot is not None and float(top) > float(bot)
    patch: Dict[str, Any] = {
        "ad_top": top,
        "ad_bottom": bot,
        "ad_status": "known" if known else "unknown",
        "ad_source": "recut",
        "layers": layers,
        "remaining_layers": len(layers),
        "next_layer_usd": layers[0]["usd"] if layers else None,
    }
    if tf:
        patch["tf"] = tf
    if plan.get("live"):
        store.replace_working_orders(user_id, plan_id, layers)
        patch["resting"] = bool(layers)
        patch["allocated_usd"] = round(sum(x["usd"] for x in layers), 4)
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
        layers = exponential_layers(
            hung.get("ad_top"),
            hung.get("ad_bottom"),
            count=DEFAULT_LAYER_COUNT,
            budget_usd=MAX_PER_PLAY_USD,
            zone_prices=hung.get("zones"),
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
    if not reds_map and snap.get("reds") is not None:
        tf = plan.get("tf") or "15m"
        reds_map = {tf: snap.get("reds")}
    volume = snap.get("volume") or plan.get("volume") or "unknown"
    if snap.get("bars"):
        volume = volume_label(snap.get("bars"))
    heat = snap.get("heat_breadth")
    panic = bool(snap.get("panic_board"))
    ad_known = (plan.get("ad_status") == "known") and plan.get("ad_top") is not None

    tf_states = []
    tfs = list(reds_map.keys()) if reds_map else ([plan.get("tf")] if plan.get("tf") else [])
    if not tfs:
        tfs = ["15m"]
    for tf in tfs:
        habit = store.habit_reds(user_id, plan["symbol"], plan["market"], str(tf))
        tf_states.append(
            tf_meets_rules(
                tf=str(tf),
                reds=reds_map.get(tf),
                habit_reds=habit,
                ad_known=ad_known,
                heat_breadth=heat,
                panic_board=panic,
                news_hits=news_hits,
            )
        )
    chosen = pick_working_tf(
        tf_states,
        respected=store.respected_scores(user_id, plan["symbol"], plan["market"]),
        locked_tf=plan.get("tf"),
    )
    gate = {
        "tf_states": tf_states,
        "chosen": chosen,
        "news_kill": kill,
        "volume": volume,
    }
    store.patch_plan(
        user_id,
        int(plan["id"]),
        reds=(chosen or {}).get("reds") if chosen else (tf_states[0].get("reds") if tf_states else None),
        volume=volume,
        news=(kill or {}).get("class") if kill else None,
        gate=gate,
        tf=(chosen or {}).get("tf") or plan.get("tf"),
    )
    plan = store.get_plan(user_id, int(plan["id"]))

    if kill:
        if plan.get("live") or plan.get("status") not in ("killed",):
            store.cancel_working(user_id, int(plan["id"]))
            _close_and_kb(
                store,
                user_id,
                plan,
                reason="news_kill",
                bounce_or_fail="fail",
                process_ok=True,
                money_pnl=0.0,
            )
            store.patch_plan(
                user_id,
                int(plan["id"]),
                status="blocked",
                live=False,
                resting=False,
                allocated_usd=0,
                armed_at=None,
            )
            return {"ok": True, "action": "news_kill", "plan_id": plan["id"]}
        return {"ok": True, "action": "news_blocked", "plan_id": plan["id"]}

    if plan.get("live") and failed_ad(
        armed_at=plan.get("armed_at"),
        now=now,
        tf=plan.get("tf"),
        bounced=bool(snap.get("bounced")),
    ):
        store.cancel_working(user_id, int(plan["id"]))
        _close_and_kb(
            store,
            user_id,
            plan,
            reason="failed_ad",
            bounce_or_fail="fail",
            process_ok=True,
            money_pnl=0.0,
        )
        store.patch_plan(
            user_id,
            int(plan["id"]),
            status="closed",
            live=False,
            resting=False,
            allocated_usd=0,
            armed_at=None,
        )
        return {"ok": True, "action": "failed_ad", "plan_id": plan["id"]}

    if snap.get("bounced") and plan.get("live"):
        store.cancel_working(user_id, int(plan["id"]))
        _close_and_kb(
            store,
            user_id,
            plan,
            reason="bounce",
            bounce_or_fail="bounce",
            process_ok=True,
            money_pnl=float(snap.get("money_pnl") or 0),
        )
        store.patch_plan(
            user_id,
            int(plan["id"]),
            status="closed",
            live=False,
            resting=False,
            allocated_usd=0,
            armed_at=None,
        )
        return {"ok": True, "action": "bounce", "plan_id": plan["id"]}

    if plan.get("live") or plan.get("status") in ("killed", "blocked"):
        return None
    if not chosen:
        return None

    cap = can_open_play(store.live_count(user_id), store.live_allocated(user_id))
    if not cap["ok"]:
        return {"ok": False, "action": "cap", "reason": cap["reason"], "plan_id": plan["id"]}

    layers = parse_json(plan.get("layers_json"), [])
    if not layers:
        return None
    budget = cap["budget_usd"]
    if budget < MAX_PER_PLAY_USD:
        layers = exponential_layers(
            plan.get("ad_top"),
            plan.get("ad_bottom"),
            count=int(plan.get("remaining_layers") or len(layers)),
            budget_usd=budget,
            zone_prices=parse_json(plan.get("zones_json"), []),
        )
    store.replace_working_orders(user_id, int(plan["id"]), layers)
    store.patch_plan(
        user_id,
        int(plan["id"]),
        status="live",
        live=True,
        resting=True,
        allocated_usd=round(sum(x["usd"] for x in layers), 4),
        remaining_layers=len(layers),
        next_layer_usd=layers[0]["usd"] if layers else None,
        layers=layers,
        armed_at=now,
        tf=chosen.get("tf"),
    )
    return {"ok": True, "action": "arm", "plan_id": plan["id"], "tf": chosen.get("tf")}


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
        habit = max(int(habit), reds_required(None))
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
    plans = [public_plan(store, p) for p in store.list_plans(user_id)]
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
    live = [p for p in plans if p.get("live")]
    rest = [p for p in plans if not p.get("live")]
    live.sort(key=lambda p: (-p["rank_score"], p.get("display") or ""))
    rest.sort(key=lambda p: (-p["rank_score"], p.get("display") or ""))
    ranked = live + rest
    for i, p in enumerate(ranked, 1):
        p["rank"] = i
    return ranked


def public_plan(store: MachineStore, row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not row:
        return {}
    layers = parse_json(row.get("layers_json"), [])
    zones = parse_json(row.get("zones_json"), [])
    working = store.list_orders(int(row["user_id"]), int(row["id"]), status="working")
    ad_line = "unknown"
    if row.get("ad_status") == "known" and row.get("ad_top") is not None:
        ad_line = f"{_num(row.get('ad_top'))} → {_num(row.get('ad_bottom'))}"
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
        "layers": layers,
        "remaining_layers": row.get("remaining_layers"),
        "next_layer_usd": row.get("next_layer_usd"),
        "reds": row.get("reds") if row.get("reds") is not None else "unknown",
        "volume": row.get("volume") or "unknown",
        "news": row.get("news"),
        "resting": bool(row.get("resting")),
        "live": bool(row.get("live")),
        "status": row.get("status"),
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
