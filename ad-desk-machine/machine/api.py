"""Standalone Machine HTTP. Bearer MACHINE_TOKEN. Live orders stay OFF."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .engine import (
    LOG,
    NEEDS,
    evaluate_id,
    hung_board,
    public_play,
    reset_runtime,
)
from .loop import ensure_loop, last_snaps, loop_wanted, tick
from .plays import HUNG_IDS, load_play
from .settings import LIVE_ORDERS_ALLOWED, live_orders_allowed, machine_token

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static" / "machine"

@asynccontextmanager
async def _lifespan(app: FastAPI):
    if loop_wanted():
        ensure_loop()
    yield


app = FastAPI(title="AD Desk Machine", version="0.1.0", lifespan=_lifespan)
if STATIC.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


class EvaluateBody(BaseModel):
    snapshot: Optional[Dict[str, Any]] = None
    now: Optional[float] = None


def _auth(authorization: Optional[str]) -> None:
    token = machine_token()
    if not token:
        raise HTTPException(503, "MACHINE_TOKEN is not set")
    got = (authorization or "").strip()
    if got != f"Bearer {token}":
        raise HTTPException(401, "unauthorized")


@app.get("/health")
def health():
    return {
        "ok": True,
        "product": "AD Desk Machine",
        "live_orders_allowed": live_orders_allowed(),
        "live_orders_sent": False,
        "hung": list(HUNG_IDS),
    }


@app.get("/machine")
def machine_page():
    index = STATIC / "index.html"
    if not index.is_file():
        raise HTTPException(404, "Machine page missing")
    return FileResponse(index)


@app.get("/plays")
def list_plays(authorization: Optional[str] = Header(default=None)):
    _auth(authorization)
    snaps = last_snaps()
    return {
        "ok": True,
        "hung_plans": hung_board(snaps),
        "live_orders_allowed": LIVE_ORDERS_ALLOWED,
        "live_orders_sent": False,
    }


@app.get("/plays/{play_id}")
def get_play(play_id: str, authorization: Optional[str] = Header(default=None)):
    _auth(authorization)
    try:
        play = load_play(play_id)
    except FileNotFoundError:
        raise HTTPException(404, "hung plan not found")
    return {
        "ok": True,
        "hung_plan": public_play(play, last_snaps().get(play_id)),
        "live_orders_allowed": False,
    }


@app.post("/plays/{play_id}/evaluate")
def post_evaluate(
    play_id: str,
    body: EvaluateBody,
    authorization: Optional[str] = Header(default=None),
):
    _auth(authorization)
    snap = dict(body.snapshot or {})
    if body.now is not None:
        snap["now"] = body.now
    try:
        result = evaluate_id(play_id, snap)
    except FileNotFoundError:
        raise HTTPException(404, "hung plan not found")
    return result


@app.post("/tick")
def post_tick(authorization: Optional[str] = Header(default=None)):
    _auth(authorization)
    return tick()


@app.get("/log")
def get_log(play_id: Optional[str] = None, authorization: Optional[str] = Header(default=None)):
    _auth(authorization)
    return {"ok": True, "log": LOG.rows(play_id)}


@app.get("/needs-you")
def get_needs(authorization: Optional[str] = Header(default=None)):
    _auth(authorization)
    return {"ok": True, "needs_you": list(NEEDS)}


@app.post("/reset")
def post_reset(authorization: Optional[str] = Header(default=None)):
    _auth(authorization)
    reset_runtime()
    return {"ok": True, "live_orders_allowed": False}


def create_app() -> FastAPI:
    return app
