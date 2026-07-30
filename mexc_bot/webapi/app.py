"""FastAPI V2.1 Desk — dynamic AD command platform."""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import actions, db
from .prices import market_context, watchlist_tickers
from .voice_agent import chat_with_tools, handle_voice_audio

logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent / "static"


def _desk_token() -> str:
    return (os.getenv("DESK_API_TOKEN") or os.getenv("WEB_UI_TOKEN") or "").strip()


def require_auth(
    authorization: Optional[str] = Header(None),
    x_desk_token: Optional[str] = Header(None),
):
    expected = _desk_token()
    if not expected:
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
    action: Optional[str] = None
    bounce_quality: Optional[str] = None
    behavior: Optional[str] = None
    notes: Optional[str] = None


class AlertCreate(BaseModel):
    symbol: str
    price: float
    market: str = "spot"


class AlertPatch(BaseModel):
    price: Optional[float] = None
    enabled: Optional[bool] = None


class WatchBody(BaseModel):
    symbol: str
    market: str = "futures"


class MoversBody(BaseModel):
    enabled: Optional[bool] = None
    threshold_percent: Optional[float] = None
    lookback_minutes: Optional[float] = None


class PositionOpen(BaseModel):
    symbol: str
    market: str = "futures"
    entry_avg: Optional[float] = None
    notes: Optional[str] = None


class PositionClose(BaseModel):
    trade_id: Optional[int] = None
    symbol: Optional[str] = None
    exit_avg: Optional[float] = None
    notes: Optional[str] = None


class AgentBody(BaseModel):
    message: str
    history: Optional[List[dict]] = None


def create_app() -> FastAPI:
    app = FastAPI(title="AD Desk", version="2.1.0-beta")

    @app.get("/api/health")
    def health():
        path = db.db_path()
        return {
            "ok": True,
            "version": "2.1.0-beta",
            "db": str(path),
            "db_exists": path.exists(),
            "auth_required": bool(_desk_token()),
            "xai_configured": bool(
                os.getenv("XAI_API_KEY") or os.getenv("GROK_API_KEY")
            ),
            "live_orders_allowed": actions.live_orders_allowed(),
            "ts": time.time(),
        }

    @app.get("/api/roadmap")
    def roadmap(_: bool = Depends(require_auth)):
        """Where V2 is going — product vision for the finished platform."""
        return {
            "vision": (
                "A futuristic agent trading platform that co-pilots AD panic scale-ins: "
                "sensors → memory → specialist agents → voice desk → disciplined execution."
            ),
            "now": [
                {"id": "desk_ui", "title": "AD Desk UI", "status": "beta"},
                {"id": "crud", "title": "Alerts / watchlist / journal CRUD", "status": "beta"},
                {"id": "intel", "title": "Isolated dump + multi-CEX delist intel", "status": "live"},
                {"id": "voice_tools", "title": "Grok voice + tool agent (desk control)", "status": "beta"},
                {"id": "learning", "title": "Event labels + source expertise", "status": "live"},
            ],
            "next": [
                {"id": "voice_realtime", "title": "Grok Speech-to-Speech realtime WebSocket", "status": "planned"},
                {"id": "fills", "title": "MEXC read-only fills → live positions", "status": "planned"},
                {"id": "llm_coach", "title": "Fluent multi-turn coach with full tool graph", "status": "planned"},
                {"id": "layer_planner", "title": "AD layer planner (zones + sizes)", "status": "planned"},
                {"id": "paper_sim", "title": "Paper PnL sim on journal", "status": "planned"},
                {"id": "live_orders", "title": "Optional gated live orders (explicit flag)", "status": "planned"},
                {"id": "pwa", "title": "Installable PWA + mobile layouts", "status": "planned"},
                {"id": "expert_agents", "title": "Per-domain experts (delist, hack, breadth)", "status": "in_progress"},
            ],
            "principles": [
                "Telegram = panic push; Desk = command + overview",
                "Never slow mover fires for news I/O",
                "Isolated dumps get specialist veto",
                "Journal before ego; tools before YOLO",
                "Live exchange orders off unless explicitly enabled",
            ],
        }

    @app.get("/api/overview")
    def overview(_: bool = Depends(require_auth)):
        uid = db.default_user_id()
        ctx = market_context()
        counts = {
            "alerts_enabled": 0,
            "watchlist": 0,
            "events": 0,
            "investigations": 0,
            "news": 0,
            "open_positions": 0,
        }
        try:
            if uid:
                for key, sql in (
                    ("alerts_enabled", "SELECT COUNT(*) AS c FROM alerts WHERE user_id=? AND enabled=1"),
                    ("watchlist", "SELECT COUNT(*) AS c FROM mover_watchlist WHERE user_id=?"),
                    ("events", "SELECT COUNT(*) AS c FROM learning_events WHERE user_id=?"),
                    ("investigations", "SELECT COUNT(*) AS c FROM investigations WHERE user_id=?"),
                    ("open_positions", "SELECT COUNT(*) AS c FROM journal_trades WHERE user_id=? AND status='open'"),
                ):
                    r = db.fetch_one(sql, (uid,))
                    counts[key] = int(r["c"]) if r else 0
            r = db.fetch_one("SELECT COUNT(*) AS c FROM news_events")
            counts["news"] = int(r["c"]) if r else 0
        except Exception as e:
            logger.debug("counts: %s", e)

        recent_events = (
            db.fetch_all(
                """
                SELECT id, source, symbol, market, drop_pct, velocity_band, mode, ts, price
                FROM learning_events WHERE user_id = ? ORDER BY ts DESC LIMIT 12
                """,
                (uid,),
            )
            if uid
            else []
        )
        recent_inv = (
            db.fetch_all(
                """
                SELECT id, symbol, market, drop_pct, velocity_band, heat_breadth,
                       verdict, confidence, ts
                FROM investigations WHERE user_id = ? ORDER BY ts DESC LIMIT 8
                """,
                (uid,),
            )
            if uid
            else []
        )
        positions = actions.list_positions(uid) if uid else []
        regime = ctx.get("regime", "UNKNOWN")
        return {
            "user_id": uid,
            "market": ctx,
            "counts": counts,
            "positions": positions,
            "recent_events": recent_events,
            "recent_investigations": recent_inv,
            "pulse": {
                "regime": regime,
                "ad_bias": (
                    "Prefer market-wide panic ADs"
                    if regime in ("RISK_OFF", "SOFT")
                    else "Selective — isolated dumps need intel"
                    if regime == "RANGE"
                    else "Risk-on — demand quality setups"
                ),
                "rule": "Panic + breadth + volume. Isolated + news → no-trade bias.",
            },
            "ts": time.time(),
        }

    # ---- read collections ----
    @app.get("/api/alerts")
    def get_alerts(_: bool = Depends(require_auth)):
        try:
            rows = actions.list_alerts()
            return {"user_id": db.default_user_id(), "alerts": rows}
        except ValueError as e:
            return {"user_id": None, "alerts": [], "error": str(e)}

    @app.post("/api/alerts")
    def post_alert(body: AlertCreate, _: bool = Depends(require_auth)):
        try:
            return actions.add_alert(body.symbol, body.price, body.market)
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.patch("/api/alerts/{stable_id}")
    def patch_alert(stable_id: int, body: AlertPatch, _: bool = Depends(require_auth)):
        try:
            return actions.update_alert(
                stable_id, price=body.price, enabled=body.enabled
            )
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.delete("/api/alerts/{stable_id}")
    def del_alert(stable_id: int, _: bool = Depends(require_auth)):
        try:
            return actions.delete_alert(stable_id)
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.get("/api/watchlist")
    def get_watch(_: bool = Depends(require_auth)):
        try:
            uid = db.default_user_id()
            rows = actions.list_watchlist()
            symbols = []
            for r in rows:
                s = r["symbol"]
                symbols.append(s.replace("_", "") if "_" in s else s)
            tickers = watchlist_tickers(symbols[:40])
            settings = db.fetch_one(
                "SELECT enabled, threshold_percent, lookback_seconds FROM mover_settings WHERE user_id = ?",
                (uid,),
            ) if uid else None
            return {
                "user_id": uid,
                "watchlist": rows,
                "settings": settings,
                "tickers": tickers,
            }
        except ValueError:
            return {"watchlist": [], "tickers": [], "settings": None}

    @app.post("/api/watchlist")
    def post_watch(body: WatchBody, _: bool = Depends(require_auth)):
        try:
            return actions.add_watch(body.symbol, body.market)
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.delete("/api/watchlist")
    def del_watch(
        symbol: str = Query(...),
        market: Optional[str] = None,
        _: bool = Depends(require_auth),
    ):
        try:
            return actions.remove_watch(symbol, market)
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.post("/api/movers")
    def post_movers(body: MoversBody, _: bool = Depends(require_auth)):
        try:
            return actions.set_movers(
                enabled=body.enabled,
                threshold_percent=body.threshold_percent,
                lookback_minutes=body.lookback_minutes,
            )
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.get("/api/positions")
    def get_positions(
        closed: bool = False, _: bool = Depends(require_auth)
    ):
        try:
            return {
                "positions": actions.list_positions(include_closed=closed),
                "live_orders_allowed": actions.live_orders_allowed(),
                "mode": "journal_paper"
                if not actions.live_orders_allowed()
                else "live_enabled",
            }
        except ValueError as e:
            return {"positions": [], "error": str(e)}

    @app.post("/api/positions")
    def post_position(body: PositionOpen, _: bool = Depends(require_auth)):
        try:
            return actions.open_position(
                body.symbol, body.market, body.entry_avg, body.notes
            )
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.post("/api/positions/close")
    def post_close(body: PositionClose, _: bool = Depends(require_auth)):
        try:
            return actions.close_position(
                body.trade_id, body.symbol, body.exit_avg, body.notes
            )
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.get("/api/events")
    def events(limit: int = Query(40, ge=1, le=200), _: bool = Depends(require_auth)):
        uid = db.default_user_id()
        if not uid:
            return {"events": []}
        rows = db.fetch_all(
            """
            SELECT e.*,
              (SELECT action FROM learning_labels l
               WHERE l.event_id = e.id ORDER BY l.ts DESC LIMIT 1) AS last_action,
              (SELECT bounce_quality FROM learning_labels l
               WHERE l.event_id = e.id ORDER BY l.ts DESC LIMIT 1) AS last_bounce
            FROM learning_events e WHERE e.user_id = ?
            ORDER BY e.ts DESC LIMIT ?
            """,
            (uid, limit),
        )
        return {"user_id": uid, "events": rows}

    @app.post("/api/events/label")
    def label_event(body: LabelBody, _: bool = Depends(require_auth)):
        try:
            if body.event_id:
                conn = db.connect()
                try:
                    uid = actions._uid()
                    conn.execute(
                        """
                        INSERT INTO learning_labels (
                          event_id, user_id, action, bounce_quality, behavior, notes, ts
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            body.event_id,
                            uid,
                            body.action,
                            body.bounce_quality,
                            body.behavior,
                            body.notes or "desk",
                            time.time(),
                        ),
                    )
                    conn.commit()
                    return {"ok": True, "event_id": body.event_id}
                finally:
                    conn.close()
            return actions.label_latest(
                action=body.action,
                bounce=body.bounce_quality,
                behavior=body.behavior,
            )
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.get("/api/investigations")
    def investigations(limit: int = Query(30, ge=1, le=100), _: bool = Depends(require_auth)):
        uid = db.default_user_id()
        inv = (
            db.fetch_all(
                "SELECT * FROM investigations WHERE user_id = ? ORDER BY ts DESC LIMIT ?",
                (uid, limit),
            )
            if uid
            else []
        )
        for r in inv:
            try:
                r["evidence"] = json.loads(r.get("evidence_json") or "[]")
            except Exception:
                r["evidence"] = []
        sources = db.fetch_all(
            "SELECT * FROM source_expertise ORDER BY weight DESC LIMIT 20"
        )
        return {"investigations": inv, "sources": sources}

    @app.get("/api/news")
    def news(limit: int = Query(40, ge=1, le=100), _: bool = Depends(require_auth)):
        return {
            "news": db.fetch_all(
                "SELECT * FROM news_events ORDER BY ts DESC LIMIT ?", (limit,)
            ),
            "delist_cache": db.fetch_all(
                "SELECT * FROM delist_cache ORDER BY ts DESC LIMIT ?", (limit,)
            ),
        }

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
        recent, opens = [], []
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
                "SELECT * FROM journal_trades WHERE user_id = ? AND status = 'open'",
                (uid,),
            )
        low = q.lower()
        if low in ("brief", "desk", "summary", "overview"):
            text = format_brief(
                recent_events=recent, open_trades=opens, learning_on=True
            )
        else:
            text = format_coach_reply(q, recent_events=recent, stats=None)
        return {"reply": text, "market": market_context(), "ts": time.time()}

    @app.post("/api/agent")
    def agent_text(body: AgentBody, _: bool = Depends(require_auth)):
        """Grok tool-using agent (text). Continuous history + full desk tools."""
        out = chat_with_tools(body.message, history=body.history)
        return out

    @app.post("/api/voice")
    async def agent_voice(
        file: UploadFile = File(...),
        history: Optional[str] = Form(None),
        _: bool = Depends(require_auth),
    ):
        """Continuous voice turn: STT → tools → optional TTS. Pass history JSON for multi-turn."""
        raw = await file.read()
        if not raw:
            raise HTTPException(400, "Empty audio")
        name = file.filename or "audio.webm"
        ctype = file.content_type or ""
        hist = None
        if history:
            try:
                hist = json.loads(history)
                if not isinstance(hist, list):
                    hist = None
            except Exception:
                hist = None
        out = handle_voice_audio(
            raw, filename=name, content_type=ctype, history=hist
        )
        if not out.get("ok"):
            raise HTTPException(502, out.get("error") or "voice failed")
        return out

    @app.get("/api/strategy")
    def strategy(_: bool = Depends(require_auth)):
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
                "desk": "CRUD + positions + voice tools",
                "v2_next": "realtime voice, fills, LLM coach, layer planner",
            },
        }

    if STATIC_DIR.is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=str(STATIC_DIR / "assets")),
            name="assets",
        )

        @app.get("/")
        def index():
            return FileResponse(STATIC_DIR / "index.html")

        @app.get("/{full_path:path}")
        def spa_fallback(full_path: str):
            candidate = STATIC_DIR / full_path
            if candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(STATIC_DIR / "index.html")

    return app


def run():
    import uvicorn
    from pathlib import Path

    try:
        from dotenv import load_dotenv

        # Prefer project .env so local desk picks up DESK_* / XAI_* without export
        root = Path(__file__).resolve().parents[2]
        load_dotenv(root / ".env", override=False)
        load_dotenv(override=False)
    except Exception:
        pass

    host = os.getenv("DESK_HOST", "0.0.0.0")
    port = int(os.getenv("DESK_PORT", "8080"))
    reload = os.getenv("DESK_RELOAD", "").strip().lower() in ("1", "true", "yes")
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    # reload=True: edit static JS/CSS or Python and refresh browser (fast local loop)
    kwargs: Dict[str, Any] = {
        "factory": True,
        "host": host,
        "port": port,
        "log_level": "info",
    }
    if reload:
        kwargs["reload"] = True
        kwargs["reload_dirs"] = [
            str(Path(__file__).resolve().parent),
            str(Path(__file__).resolve().parents[1]),
        ]
    uvicorn.run("mexc_bot.webapi.app:create_app", **kwargs)


if __name__ == "__main__":
    run()
