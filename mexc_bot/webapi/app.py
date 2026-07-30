"""FastAPI V2 Desk application."""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db
from .prices import market_context, ticker_24h, watchlist_tickers

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _desk_token() -> str:
    return (os.getenv("DESK_API_TOKEN") or os.getenv("WEB_UI_TOKEN") or "").strip()


def require_auth(authorization: Optional[str] = Header(None), x_desk_token: Optional[str] = Header(None)):
    expected = _desk_token()
    if not expected:
        # open mode for local beta — warn in /api/health
        return True
    token = None
    if x_desk_token:
        token = x_desk_token.strip()
    elif authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not token or not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True


class CoachBody(BaseModel):
    message: str = ""
    question: str = ""


class LabelBody(BaseModel):
    event_id: Optional[int] = None
    action: Optional[str] = None  # took | skip | watch
    bounce_quality: Optional[str] = None
    behavior: Optional[str] = None
    notes: Optional[str] = None


def create_app() -> FastAPI:
    app = FastAPI(title="MEXC AD Desk", version="2.0.0-beta")

    @app.get("/api/health")
    def health():
        path = db.db_path()
        return {
            "ok": True,
            "version": "2.0.0-beta",
            "db": str(path),
            "db_exists": path.exists(),
            "auth_required": bool(_desk_token()),
            "ts": time.time(),
        }

    @app.get("/api/overview")
    def overview(_: bool = Depends(require_auth)):
        uid = db.default_user_id()
        ctx = market_context()
        alerts_n = 0
        events_n = 0
        inv_n = 0
        news_n = 0
        watch_n = 0
        try:
            if uid:
                r = db.fetch_one(
                    "SELECT COUNT(*) AS c FROM alerts WHERE user_id = ? AND enabled = 1",
                    (uid,),
                )
                alerts_n = int(r["c"]) if r else 0
                r = db.fetch_one(
                    "SELECT COUNT(*) AS c FROM learning_events WHERE user_id = ?",
                    (uid,),
                )
                events_n = int(r["c"]) if r else 0
                r = db.fetch_one(
                    "SELECT COUNT(*) AS c FROM investigations WHERE user_id = ?",
                    (uid,),
                )
                inv_n = int(r["c"]) if r else 0
                r = db.fetch_one(
                    "SELECT COUNT(*) AS c FROM mover_watchlist WHERE user_id = ?",
                    (uid,),
                )
                watch_n = int(r["c"]) if r else 0
            r = db.fetch_one("SELECT COUNT(*) AS c FROM news_events")
            news_n = int(r["c"]) if r else 0
        except Exception as e:
            logger.debug("overview counts: %s", e)

        recent_inv = []
        if uid:
            recent_inv = db.fetch_all(
                """
                SELECT id, symbol, market, drop_pct, velocity_band, heat_breadth,
                       verdict, confidence, ts
                FROM investigations WHERE user_id = ?
                ORDER BY ts DESC LIMIT 8
                """,
                (uid,),
            )
        recent_events = []
        if uid:
            recent_events = db.fetch_all(
                """
                SELECT id, source, symbol, market, drop_pct, velocity_band, mode, ts, price
                FROM learning_events WHERE user_id = ?
                ORDER BY ts DESC LIMIT 12
                """,
                (uid,),
            )

        # strategy pulse
        regime = ctx.get("regime", "UNKNOWN")
        pulse = {
            "regime": regime,
            "ad_bias": (
                "Prefer market-wide panic ADs"
                if regime in ("RISK_OFF", "SOFT")
                else "Selective — demand isolation check on single-name dumps"
                if regime == "RANGE"
                else "Risk-on tape — be pickier on dips"
            ),
            "rule": "Rule 2/6: panic + breadth + volume; isolated + news → no-trade bias",
        }

        return {
            "user_id": uid,
            "market": ctx,
            "counts": {
                "alerts_enabled": alerts_n,
                "watchlist": watch_n,
                "events": events_n,
                "investigations": inv_n,
                "news": news_n,
            },
            "pulse": pulse,
            "recent_events": recent_events,
            "recent_investigations": recent_inv,
            "ts": time.time(),
        }

    @app.get("/api/alerts")
    def alerts(
        user_id: Optional[int] = None,
        _: bool = Depends(require_auth),
    ):
        uid = user_id or db.default_user_id()
        if not uid:
            return {"alerts": [], "user_id": None}
        rows = db.fetch_all(
            """
            SELECT id, user_id, symbol, price, enabled, market
            FROM alerts WHERE user_id = ?
            ORDER BY id ASC
            """,
            (uid,),
        )
        # visual rank
        for i, r in enumerate(rows, start=1):
            r["visual_id"] = i
        return {"user_id": uid, "alerts": rows}

    @app.get("/api/watchlist")
    def watchlist(
        user_id: Optional[int] = None,
        _: bool = Depends(require_auth),
    ):
        uid = user_id or db.default_user_id()
        if not uid:
            return {"watchlist": [], "tickers": [], "user_id": None}
        rows = db.fetch_all(
            """
            SELECT symbol, market FROM mover_watchlist
            WHERE user_id = ? ORDER BY market, symbol
            """,
            (uid,),
        )
        symbols = []
        for r in rows:
            s = r["symbol"]
            if r.get("market") == "futures" and "_" in s:
                symbols.append(s.replace("_", ""))
            else:
                symbols.append(s)
        # also add bases as USDT
        tickers = watchlist_tickers(symbols[:40])
        settings = db.fetch_one(
            "SELECT enabled, threshold_percent, lookback_seconds FROM mover_settings WHERE user_id = ?",
            (uid,),
        )
        return {
            "user_id": uid,
            "watchlist": rows,
            "settings": settings,
            "tickers": tickers,
        }

    @app.get("/api/events")
    def events(
        user_id: Optional[int] = None,
        limit: int = Query(40, ge=1, le=200),
        _: bool = Depends(require_auth),
    ):
        uid = user_id or db.default_user_id()
        if not uid:
            return {"events": []}
        rows = db.fetch_all(
            """
            SELECT e.*,
              (SELECT action FROM learning_labels l
               WHERE l.event_id = e.id ORDER BY l.ts DESC LIMIT 1) AS last_action,
              (SELECT bounce_quality FROM learning_labels l
               WHERE l.event_id = e.id ORDER BY l.ts DESC LIMIT 1) AS last_bounce
            FROM learning_events e
            WHERE e.user_id = ?
            ORDER BY e.ts DESC LIMIT ?
            """,
            (uid, limit),
        )
        return {"user_id": uid, "events": rows}

    @app.post("/api/events/label")
    def label_event(body: LabelBody, _: bool = Depends(require_auth)):
        uid = db.default_user_id()
        if not uid:
            raise HTTPException(400, "No user in DB yet")
        conn = db.connect()
        try:
            eid = body.event_id
            if not eid:
                row = conn.execute(
                    "SELECT id FROM learning_events WHERE user_id = ? ORDER BY ts DESC LIMIT 1",
                    (uid,),
                ).fetchone()
                if not row:
                    raise HTTPException(404, "No events")
                eid = int(row["id"])
            conn.execute(
                """
                INSERT INTO learning_labels (
                    event_id, user_id, action, bounce_quality, behavior, notes, ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    eid,
                    uid,
                    body.action,
                    body.bounce_quality,
                    body.behavior,
                    body.notes,
                    time.time(),
                ),
            )
            conn.commit()
            return {"ok": True, "event_id": eid}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, str(e))
        finally:
            conn.close()

    @app.get("/api/investigations")
    def investigations(
        user_id: Optional[int] = None,
        limit: int = Query(30, ge=1, le=100),
        _: bool = Depends(require_auth),
    ):
        uid = user_id or db.default_user_id()
        if not uid:
            return {"investigations": [], "sources": []}
        inv = db.fetch_all(
            """
            SELECT * FROM investigations WHERE user_id = ?
            ORDER BY ts DESC LIMIT ?
            """,
            (uid, limit),
        )
        for r in inv:
            try:
                r["evidence"] = json.loads(r.get("evidence_json") or "[]")
            except Exception:
                r["evidence"] = []
        sources = db.fetch_all(
            """
            SELECT * FROM source_expertise
            ORDER BY weight DESC, confirmed_moves DESC LIMIT 20
            """
        )
        return {"user_id": uid, "investigations": inv, "sources": sources}

    @app.get("/api/news")
    def news(limit: int = Query(40, ge=1, le=100), _: bool = Depends(require_auth)):
        rows = db.fetch_all(
            "SELECT * FROM news_events ORDER BY ts DESC LIMIT ?",
            (limit,),
        )
        delists = db.fetch_all(
            "SELECT * FROM delist_cache ORDER BY ts DESC LIMIT ?",
            (limit,),
        )
        return {"news": rows, "delist_cache": delists}

    @app.get("/api/prices")
    def prices(
        symbols: str = Query("BTCUSDT,ETHUSDT,SOLUSDT"),
        _: bool = Depends(require_auth),
    ):
        syms = [s.strip() for s in symbols.split(",") if s.strip()]
        return {"tickers": watchlist_tickers(syms), "context": market_context()}

    @app.post("/api/coach")
    def coach(body: CoachBody, _: bool = Depends(require_auth)):
        from ..coach.engine import format_brief, format_coach_reply

        uid = db.default_user_id()
        q = (body.question or body.message or "checklist").strip()
        recent = []
        opens = []
        if uid:
            recent = db.fetch_all(
                """
                SELECT e.*,
                  (SELECT action FROM learning_labels l
                   WHERE l.event_id = e.id ORDER BY l.ts DESC LIMIT 1) AS last_action
                FROM learning_events e WHERE e.user_id = ?
                ORDER BY e.ts DESC LIMIT 12
                """,
                (uid,),
            )
            opens = db.fetch_all(
                """
                SELECT * FROM journal_trades
                WHERE user_id = ? AND status = 'open'
                ORDER BY opened_at DESC
                """,
                (uid,),
            )
        # simple intents
        low = q.lower()
        if low in ("brief", "desk", "summary", "overview"):
            text = format_brief(
                recent_events=recent, open_trades=opens, learning_on=True
            )
        else:
            text = format_coach_reply(q, recent_events=recent, stats=None)
        return {
            "reply": text,
            "market": market_context(),
            "ts": time.time(),
        }

    @app.get("/api/strategy")
    def strategy(_: bool = Depends(require_auth)):
        """AD desk playbook snapshot for the UI."""
        return {
            "name": "Average Drop / Panic Scale-in",
            "core": (
                "Find charts that respect their own average drops; scale in on panic "
                "with volume; layer out on meaningful bounces; treat grinds and "
                "isolated news dumps as high risk."
            ),
            "prefer": [
                "Sharp panic dumps (FAST/PANIC velocity)",
                "Market-wide selling (heat breadth)",
                "Volume expansion into the low",
                "Familiar AD range / Initial Drop defined",
            ],
            "avoid": [
                "Slow GRIND dumps",
                "Isolated single-name collapse + delist/hack news",
                "No-volume big moves",
                "Pride holds past failed AD",
            ],
            "workflow": "Plan levels → alarms wait → engage only when tools fire",
            "layers": "5–10 exponential layers; powder for extensions",
            "modules": {
                "sensors": "targets + movers peak/step",
                "memory": "events + labels + outcomes",
                "isolated_agent": "async delist/hack check",
                "news": "fatal-class continuous",
                "v2_planned": "voice + MEXC fills + fluent LLM coach",
            },
        }

    # Static SPA
    if STATIC_DIR.is_dir():
        app.mount("/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="assets")

        @app.get("/")
        def index():
            return FileResponse(STATIC_DIR / "index.html")

        @app.get("/{full_path:path}")
        def spa_fallback(full_path: str):
            # API already registered; static files
            candidate = STATIC_DIR / full_path
            if candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(STATIC_DIR / "index.html")

    return app


def run():
    import uvicorn

    host = os.getenv("DESK_HOST", "0.0.0.0")
    port = int(os.getenv("DESK_PORT", "8080"))
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    uvicorn.run(
        "mexc_bot.webapi.app:create_app",
        factory=True,
        host=host,
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    run()
