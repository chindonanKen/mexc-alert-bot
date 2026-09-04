"""FastAPI surface. Bearer or HttpOnly cookie. live_orders_allowed always false."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .engine import Engine
from .loop import DecisionLoop, build_default_loop

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static" / "machine"
TOKEN = os.environ.get("MACHINE_TOKEN", "dev-token")
SESSION_COOKIE = "machine_session"
# Decision loop on by default while uvicorn runs. Tests set MACHINE_LOOP=0.
LOOP_ENABLED = os.environ.get("MACHINE_LOOP", "1") != "0"
FEED_INTERVAL = float(os.environ.get("MACHINE_FEED_INTERVAL", "10"))

engine = Engine()
decision_loop: DecisionLoop | None = None
_loop_task: asyncio.Task | None = None

# Load all data/plays/*.json at import (SYN/AGI/US hang on boot). Not examples/.
if (ROOT / "data" / "plays").exists():
    engine.load_plays_dir()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global decision_loop, _loop_task
    # Re-hang plays if this engine was replaced empty (e.g. after tests)
    if not engine.plans and (ROOT / "data" / "plays").exists():
        engine.load_plays_dir()
    decision_loop = None
    _loop_task = None
    if LOOP_ENABLED:
        decision_loop = build_default_loop(engine, interval_sec=FEED_INTERVAL)
        _loop_task = asyncio.create_task(decision_loop.run_forever())
    yield
    if decision_loop is not None:
        decision_loop.stop()
    if _loop_task is not None:
        try:
            await asyncio.wait_for(_loop_task, timeout=FEED_INTERVAL + 2)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            _loop_task.cancel()


app = FastAPI(title="AD Desk Machine", version="0.1.0", lifespan=lifespan)


def _matches(candidate: str, expected: str) -> bool:
    if not candidate or not expected:
        return False
    try:
        return secrets.compare_digest(candidate, expected)
    except (TypeError, ValueError):
        return False


def _login_tokens() -> list[str]:
    """Browser may use the desk token; scripts keep MACHINE_TOKEN bearer."""
    out: list[str] = []
    for raw in (
        os.environ.get("MACHINE_TOKEN") or TOKEN,
        os.environ.get("DESK_API_TOKEN") or "",
    ):
        t = (raw or "").strip()
        if t and t not in out:
            out.append(t)
    return out


def _login_ok(candidate: str) -> bool:
    return any(_matches(candidate, t) for t in _login_tokens())


def session_cookie_value() -> str:
    secret = (os.environ.get("MACHINE_TOKEN") or TOKEN or "dev-token").encode()
    return hmac.new(secret, b"ad-desk-machine-browser", hashlib.sha256).hexdigest()


def require_bearer(
    authorization: str | None = Header(default=None),
    machine_session: str | None = Cookie(default=None),
) -> None:
    if authorization:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer" and _login_ok(parts[1]):
            return
    if machine_session and _matches(machine_session, session_cookie_value()):
        return
    # Never echo the token in responses
    raise HTTPException(status_code=401, detail="missing Authorization")


@app.post("/api/machine/login")
def login(body: dict[str, Any], request: Request, response: Response) -> dict[str, Any]:
    """Password form → HttpOnly cookie. Token is not stored in the page."""
    token = str((body or {}).get("token") or "")
    if not _login_ok(token):
        raise HTTPException(status_code=401, detail="invalid token")
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "http").lower()
    response.set_cookie(
        key=SESSION_COOKIE,
        value=session_cookie_value(),
        httponly=True,
        secure=(proto == "https"),
        samesite="lax",
        path="/",
        max_age=14 * 24 * 3600,
    )
    return {"ok": True, "live_orders_allowed": False}


@app.post("/api/machine/logout")
def logout(response: Response) -> dict[str, Any]:
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True, "live_orders_allowed": False}


@app.get("/api/machine/status")
def status(_: None = Depends(require_bearer)) -> dict[str, Any]:
    s = engine.status()
    s["live_orders_allowed"] = False
    if decision_loop is not None:
        s["loop"] = decision_loop.status()
    else:
        s["loop"] = {"running": False, "live_orders_allowed": False}
    return s


@app.get("/api/machine/plans")
def plans(_: None = Depends(require_bearer)) -> dict[str, Any]:
    return {"plans": engine.ranked(), "live_orders_allowed": False}


@app.get("/api/machine/plans/{plan_id}")
def plan_one(plan_id: str, _: None = Depends(require_bearer)) -> dict[str, Any]:
    plan = engine.plans.get(plan_id)
    if plan is None:
        # try by name
        for p in engine.plans.values():
            if p.name == plan_id or p.id == plan_id:
                plan = p
                break
    if plan is None:
        raise HTTPException(status_code=404, detail="plan not found")
    row = engine.plan_row(plan)
    row["live_orders_allowed"] = False
    return row


@app.get("/api/machine/layers/{plan_id}")
def layers(plan_id: str, _: None = Depends(require_bearer)) -> dict[str, Any]:
    plan = engine.plans.get(plan_id)
    if plan is None:
        for p in engine.plans.values():
            if p.name == plan_id or p.id == plan_id:
                plan = p
                break
    if plan is None:
        raise HTTPException(status_code=404, detail="plan not found")
    return {
        "id": plan.id,
        "layers": [b.to_dict() for b in plan.fills.buy_layers],
        "sell_layers": [s.to_dict() for s in plan.fills.remaining_sells()],
        "live_orders_allowed": False,
    }


@app.get("/api/machine/trades")
def trades(_: None = Depends(require_bearer)) -> dict[str, Any]:
    return {"trades": engine.trades, "live_orders_allowed": False}


@app.get("/api/machine/feed")
def feed(_: None = Depends(require_bearer)) -> dict[str, Any]:
    return {"feed": engine.feed[-200:], "live_orders_allowed": False}


@app.get("/api/machine/log")
def log(_: None = Depends(require_bearer)) -> dict[str, Any]:
    return {"log": engine.log.as_list(), "live_orders_allowed": False}


@app.get("/api/machine/closes")
def closes(_: None = Depends(require_bearer)) -> dict[str, Any]:
    return {"closes": engine.closes, "live_orders_allowed": False}


@app.get("/api/machine/needs-you")
def needs_you(_: None = Depends(require_bearer)) -> dict[str, Any]:
    return {"needs_you": engine.needs_you, "live_orders_allowed": False}


@app.post("/api/machine/hang")
def hang(body: dict[str, Any], _: None = Depends(require_bearer)) -> dict[str, Any]:
    """Hang a written plan (watch). Never places live orders."""
    plan = engine.hang_play(body)
    rows = {p["id"]: p for p in engine.ranked()}
    row = rows.get(plan.id) or {"id": plan.id, "name": plan.name, "state": plan.state}
    row["live_orders_allowed"] = False
    return row


@app.post("/api/machine/simulate")
def simulate(body: dict[str, Any], _: None = Depends(require_bearer)) -> dict[str, Any]:
    """Staff scoring: push a synthetic print. Never places live orders."""
    from .feeds import Print

    pr = Print(
        name=str(body["name"]),
        price=float(body["price"]),
        volume_usd=float(body.get("volume_usd") or 0),
        chosen_tf_reds=int(body.get("chosen_tf_reds") or 0),
        faster_tf_reds=dict(body.get("faster_tf_reds") or {}),
        low=float(body["low"]) if "low" in body else None,
    )
    result = engine.on_print(pr)
    result["live_orders_allowed"] = False
    return result


@app.get("/machine")
@app.get("/machine/")
def machine_page() -> FileResponse:
    index = STATIC / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="machine page missing")
    return FileResponse(index)


if STATIC.exists():
    app.mount("/machine/static", StaticFiles(directory=str(STATIC)), name="machine-static")


@app.get("/")
def root() -> JSONResponse:
    return JSONResponse(
        {
            "service": "ad-desk-machine",
            "machine": "/machine",
            "live_orders_allowed": False,
        }
    )
