"""Trading Master HTTP API for the isolated AD Machine. Flag-on only."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..webapi.app import require_auth
from .engine import (
    TAPE_ACTIONS,
    account_view,
    evaluate,
    get_store,
    kill,
    propose_line,
    propose_name,
    public_close,
    public_kb,
    public_need,
    rank_plans,
    recut,
    resolve_need,
    room_state,
    seed_plans,
)
from .settings import machine_user_id

router = APIRouter(prefix="/api/machine", tags=["ad-machine"])


class RecutBody(BaseModel):
    ad_top: Optional[float] = None
    ad_bottom: Optional[float] = None
    remaining_layers: Optional[int] = Field(default=None, ge=1, le=12)
    tf: Optional[str] = None
    volume_habit_usd: Optional[float] = None
    candles_to_bounce: Optional[int] = None


class LayersBody(BaseModel):
    ad_top: Optional[float] = None
    ad_bottom: Optional[float] = None


class NameBody(BaseModel):
    symbol: str
    market: str = "spot"


class LineBody(BaseModel):
    ad_top: float
    ad_bottom: float


class EvaluateBody(BaseModel):
    snapshot: Optional[Dict[str, Any]] = None
    now: Optional[float] = None


def _uid() -> int:
    return machine_user_id()


def _store():
    return get_store()


@router.get("/plans")
def list_plans(_: bool = Depends(require_auth)):
    store = _store()
    uid = _uid()
    seed_plans(store, uid)
    plans = rank_plans(store, uid)
    needs = [public_need(n) for n in store.list_needs(uid)]
    closes = [public_close(c) for c in store.list_closes(uid)]
    kb = [public_kb(k) for k in store.list_kb(uid)]
    return {
        "ok": True,
        "account": account_view(store, uid),
        "plans": plans,
        "needs_you": needs,
        "room": room_state(plans, needs, closes=closes, kb=kb),
        "log": store.list_log(
            uid,
            limit=40,
            actions=TAPE_ACTIONS,
        ),
        "live_orders_sent": False,
    }


@router.get("/plans/{plan_id}")
def get_plan(plan_id: int, _: bool = Depends(require_auth)):
    store = _store()
    uid = _uid()
    from .engine import public_plan

    row = store.get_plan(uid, plan_id)
    if not row:
        raise HTTPException(404, "plan not found")
    return {"ok": True, "plan": public_plan(store, row)}


@router.post("/plans/{plan_id}/recut")
def recut_plan(plan_id: int, body: RecutBody, _: bool = Depends(require_auth)):
    store = _store()
    uid = _uid()
    try:
        plan = recut(
            store,
            uid,
            plan_id,
            ad_top=body.ad_top,
            ad_bottom=body.ad_bottom,
            remaining_layers=body.remaining_layers,
            tf=body.tf,
            volume_habit_usd=body.volume_habit_usd,
            candles_to_bounce=body.candles_to_bounce,
        )
    except KeyError:
        raise HTTPException(404, "plan not found")
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"ok": True, "plan": plan}


@router.post("/plans/{plan_id}/kill")
def kill_plan(plan_id: int, _: bool = Depends(require_auth)):
    store = _store()
    uid = _uid()
    try:
        plan = kill(store, uid, plan_id)
    except KeyError:
        raise HTTPException(404, "plan not found")
    return {"ok": True, "plan": plan}


@router.post("/plans/{plan_id}/propose-line")
def propose_plan_line(plan_id: int, body: LineBody, _: bool = Depends(require_auth)):
    store = _store()
    uid = _uid()
    try:
        need = propose_line(store, uid, plan_id, body.ad_top, body.ad_bottom)
    except KeyError:
        raise HTTPException(404, "plan not found")
    return {"ok": True, "need": need}


@router.get("/needs-you")
def list_needs(_: bool = Depends(require_auth)):
    store = _store()
    uid = _uid()
    return {"ok": True, "needs_you": [public_need(n) for n in store.list_needs(uid)]}


@router.post("/names")
def post_name(body: NameBody, _: bool = Depends(require_auth)):
    store = _store()
    uid = _uid()
    return {"ok": True, **propose_name(store, uid, body.symbol, body.market)}


@router.post("/needs-you/{need_id}/accept")
def accept_need(need_id: int, _: bool = Depends(require_auth)):
    store = _store()
    uid = _uid()
    try:
        return {"ok": True, **resolve_need(store, uid, need_id, True)}
    except KeyError:
        raise HTTPException(404, "needs-you not found")


@router.post("/needs-you/{need_id}/reject")
def reject_need(need_id: int, _: bool = Depends(require_auth)):
    store = _store()
    uid = _uid()
    try:
        return {"ok": True, **resolve_need(store, uid, need_id, False)}
    except KeyError:
        raise HTTPException(404, "needs-you not found")


@router.get("/closes")
def list_closes(_: bool = Depends(require_auth)):
    store = _store()
    uid = _uid()
    return {
        "ok": True,
        "closes": [public_close(c) for c in store.list_closes(uid)],
        "kb": [public_kb(k) for k in store.list_kb(uid)],
    }


@router.get("/ranks")
def list_ranks(_: bool = Depends(require_auth)):
    store = _store()
    uid = _uid()
    seed_plans(store, uid)
    plans = rank_plans(store, uid)
    needs = [public_need(n) for n in store.list_needs(uid)]
    closes = [public_close(c) for c in store.list_closes(uid)]
    kb = [public_kb(k) for k in store.list_kb(uid)]
    return {
        "ok": True,
        "account": account_view(store, uid),
        "ranks": plans,
        "room": room_state(plans, needs, closes=closes, kb=kb),
    }


@router.get("/plans/{plan_id}/layers")
def get_layers(plan_id: int, _: bool = Depends(require_auth)):
    store = _store()
    uid = _uid()
    from .engine import public_plan

    row = store.get_plan(uid, plan_id)
    if not row:
        raise HTTPException(404, "plan not found")
    plan = public_plan(store, row)
    return {
        "ok": True,
        "layers": plan.get("layers") or [],
        "live_orders_sent": False,
    }


@router.post("/plans/{plan_id}/layers")
def post_layers(plan_id: int, body: LayersBody, _: bool = Depends(require_auth)):
    store = _store()
    uid = _uid()
    from .engine import write_layers

    try:
        plan = write_layers(
            store,
            uid,
            plan_id,
            ad_top=body.ad_top,
            ad_bottom=body.ad_bottom,
        )
    except KeyError:
        raise HTTPException(404, "plan not found")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "ok": True,
        "plan": plan,
        "layers": plan.get("layers") or [],
        "live_orders_sent": False,
    }


@router.get("/log")
def get_log(
    plan_id: Optional[int] = None,
    since: Optional[float] = None,
    limit: int = 80,
    _: bool = Depends(require_auth),
):
    store = _store()
    uid = _uid()
    rows = store.list_log(uid, plan_id=plan_id, since=since, limit=min(int(limit), 400))
    return {"ok": True, "log": rows, "live_orders_sent": False}


@router.get("/feed")
def get_feed(since: Optional[float] = None, limit: int = 40, _: bool = Depends(require_auth)):
    """Paper actions for Trading Master. One bubble per row. No Telegram."""
    store = _store()
    uid = _uid()
    rows = store.list_log(
        uid,
        since=since,
        limit=min(int(limit), 100),
        actions=TAPE_ACTIONS,
    )
    bubbles = []
    for r in rows:
        bubbles.append(
            {
                "manila": r.get("manila"),
                "name": r.get("symbol"),
                "tf": r.get("tf"),
                "last": r.get("last_price"),
                "action": r.get("action"),
                "why": r.get("why"),
                "rule_ids": r.get("rule_ids"),
                "need_chart": str(r.get("action") or "")
                in ("sit-out", "flatten-news"),
                "size_pct": r.get("size_pct"),
                "intended": r.get("intended_price"),
                "filled": r.get("filled_price"),
                "pnl": r.get("money_pnl"),
            }
        )
    return {"ok": True, "feed": bubbles, "live_orders_sent": False}


@router.post("/evaluate")
def post_evaluate(body: EvaluateBody, _: bool = Depends(require_auth)):
    store = _store()
    uid = _uid()
    return evaluate(store, uid, body.snapshot, now=body.now)
