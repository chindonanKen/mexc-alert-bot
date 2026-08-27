"""FastAPI V2.1 Desk — dynamic AD command platform."""

from __future__ import annotations

import base64
import json
import logging
import os
import re
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
from .voice_agent import chat_with_tools, handle_voice_audio, tts_speak

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


class AgentAskBody(BaseModel):
    question: str = "What have you learned so far?"


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
    set_id: Optional[int] = None


class MoversBody(BaseModel):
    enabled: Optional[bool] = None
    threshold_percent: Optional[float] = None
    lookback_minutes: Optional[float] = None
    set_id: Optional[int] = None


class MoverSetCreate(BaseModel):
    name: str
    threshold_percent: float = 5.0
    lookback_minutes: float = 15.0
    enabled: bool = False


class MoverSetPatch(BaseModel):
    name: Optional[str] = None
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


class PositionFlagBody(BaseModel):
    """Flags on Positions: free coins + long-term hold book (exclude AD learning)."""

    entity_key: str
    symbol: str
    market: str = "spot"
    free_coins_override: Optional[str] = None  # on | off | null
    free_mark_usd: Optional[float] = None
    book: Optional[str] = None  # hold | ad
    notes: Optional[str] = None


class AgentBody(BaseModel):
    message: str
    history: Optional[List[dict]] = None


class TtsBody(BaseModel):
    text: str = ""


class TeachBody(BaseModel):
    text: str = ""
    tags: Optional[List[str]] = None
    needs_approval: bool = False
    symbol: Optional[str] = None
    market: Optional[str] = None
    entity_key: Optional[str] = None
    event_id: Optional[int] = None
    behaviors: Optional[List[str]] = None
    context_type: Optional[str] = None  # trade | fire


class ApproveBody(BaseModel):
    lesson_id: int
    dismiss: bool = False


class AnswerBody(BaseModel):
    question_id: int
    answer_text: Optional[str] = None
    action: Optional[str] = None
    behavior: Optional[str] = None
    dismiss: bool = False


# Nested-in-create_app models break body parsing under `from __future__ import annotations`
# (FastAPI treats them as query params → 422). Keep POST/PATCH bodies at module level.
class JudgeBody(BaseModel):
    event_id: Optional[int] = None
    symbol: Optional[str] = None


class ChartReadBody(BaseModel):
    symbol: str
    market: Optional[str] = None
    refresh: bool = True


class CorrectBody(BaseModel):
    correct_verdict: str
    reason: str
    event_id: Optional[int] = None
    case_id: Optional[int] = None
    symbol: Optional[str] = None


class LessonEditBody(BaseModel):
    """PATCH /api/learning/lessons/{id} — text + process/AD chips."""

    text: Optional[str] = None
    tags: Optional[List[str]] = None
    behaviors: Optional[List[str]] = None


class TagTradeBody(BaseModel):
    trade_id: str
    behavior: Optional[str] = None
    notes: Optional[str] = None


class VisualAdBody(BaseModel):
    """POST /api/learning/cases/{id}/visual-ad — staff-written visual AD."""

    tf: Optional[str] = None
    high: Optional[float] = None
    low: Optional[float] = None
    note: Optional[str] = None
    image_relpath: Optional[str] = None
    source: Optional[str] = None
    ts: Optional[float] = None


def _feature_ad_machine() -> bool:
    from ..machine.settings import feature_ad_machine

    return feature_ad_machine()


def _machine_not_found() -> None:
    raise HTTPException(status_code=404, detail="Not found")


def create_app() -> FastAPI:
    app = FastAPI(title="AD Desk", version="2.1.0-beta")

    @app.get("/api/health")
    def health():
        from .build_info import build_identity

        path = db.db_path()
        ident = build_identity()
        return {
            "ok": True,
            "version": "2.1.0-beta",
            "git_sha": ident.get("git_sha"),
            "image_tag": ident.get("image_tag"),
            "db": str(path),
            "db_exists": path.exists(),
            "auth_required": bool(_desk_token()),
            "xai_configured": bool(
                os.getenv("XAI_API_KEY") or os.getenv("GROK_API_KEY")
            ),
            "live_orders_allowed": actions.live_orders_allowed(),
            "feature_ad_machine": _feature_ad_machine(),
            "ts": time.time(),
        }

    @app.get("/api/roadmap")
    def roadmap(_: bool = Depends(require_auth)):
        """Where AD Desk is going — keep in sync with AGENTS.md + SESSION_HANDOFF."""
        return {
            "vision": (
                "Fully autonomous AD agent on MEXC panic scale-ins: truth → cases → decide+log → "
                "grade → AD policy → paper → advise → gated live. Telegram = sensors. "
                "Desk = positions + teach + agent surfaces. Never silent live risk. "
                "Canonical: docs/AD_AGENT_PLAN.md"
            ),
            "now": [
                {"id": "p0_truth", "title": "P0 Truth & teach (money_truth, Learning V1, positions)", "status": "live"},
                {"id": "targets_movers", "title": "Targets + multi-set movers + Telegram sensors", "status": "live"},
                {"id": "intel", "title": "Multi-CEX delist intel (Binance CMS 161)", "status": "live"},
                {"id": "voice_tools", "title": "Turn-based voice tools (teach, ask, pending)", "status": "beta"},
                {"id": "desk_ui", "title": "AD Desk (small edits only while agent builds)", "status": "beta"},
            ],
            "next": [
                {"id": "p1_cases", "title": "P1 Case factory — multi-TF AD + regime/vol/reds + retrieve", "status": "wip"},
                {"id": "p2_decide", "title": "P2 Decide + log (TF pick + factor stack + size hint)", "status": "planned"},
                {"id": "p3_grade", "title": "P3 Grade decisions vs teach_ok / ad_met", "status": "planned"},
                {"id": "p4_policy", "title": "P4 AD policy proposals (layers/zones)", "status": "planned"},
                {"id": "p5_paper", "title": "P5 Paper / replay + pass bar", "status": "planned"},
                {"id": "p6_advise", "title": "P6 Advise / recs (owner re-open + P5 bar)", "status": "deferred"},
                {"id": "p7_live", "title": "P7 Gated live AD (default off)", "status": "deferred"},
                {"id": "ad_machine", "title": "AD Machine isolated paper book (FEATURE_AD_MACHINE, default off)", "status": "planned"},
                {"id": "desk_small", "title": "Occasional small AD Desk UX fixes", "status": "planned"},
            ],
            "principles": [
                "Follow docs/AD_AGENT_PLAN.md — do not skip phases",
                "Telegram = panic push; Desk = positions + teach + agent",
                "Structured cases over free-text scrapbook",
                "Chart history on the working TF is the source of truth — size to how this dump matches that history",
                "Exchange money_truth only for $ (teach_ok window)",
                "Decide+log then grade before any coach or live",
                "Live exchange orders off unless explicitly enabled",
            ],
        }

    @app.get("/api/overview")
    def overview(_: bool = Depends(require_auth)):
        """Ranked Overview stack — see docs/AD_DESK_VISION.md hierarchy."""
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

        alerts = actions.list_alerts(uid) if uid else []
        now_ts = time.time()
        hour_ago = now_ts - 3600

        recent_events = (
            db.fetch_all(
                """
                SELECT id, source, symbol, market, drop_pct, velocity_band, mode, ts, price,
                       ref_price, heat_breadth
                FROM learning_events WHERE user_id = ? ORDER BY ts DESC LIMIT 80
                """,
                (uid,),
            )
            if uid
            else []
        )

        # Overview TARGETS = latest *fired* target alerts (not static armed list)
        target_fires = [
            e
            for e in recent_events
            if (e.get("source") or "") in ("target", "target_fire", "price_target")
            or (
                (e.get("source") or "").startswith("target")
            )
        ]
        # also accept source containing target
        if not target_fires:
            target_fires = [
                e for e in recent_events if "target" in (e.get("source") or "").lower()
            ]
        # dedupe by symbol keep newest
        seen_t = set()
        top_targets = []
        for e in target_fires:
            sk = (e.get("symbol") or "").upper()
            if sk in seen_t:
                continue
            seen_t.add(sk)
            e = dict(e)
            e["fired_at"] = e.get("ts")
            e["age_seconds"] = now_ts - float(e.get("ts") or now_ts)
            top_targets.append(e)
            if len(top_targets) >= 5:
                break

        # Overview MOVERS = fires in last 1h only (no multi-hour widen)
        mover_1h = []
        for e in recent_events:
            ts = float(e.get("ts") or 0)
            if ts < hour_ago:
                continue
            src = e.get("source") or ""
            if "target" in src.lower():
                continue
            if not (src.startswith("mover") or e.get("drop_pct") is not None):
                continue
            row = dict(e)
            row["fired_at"] = ts
            row["age_seconds"] = now_ts - ts
            row["move_1h_pct"] = abs(float(e.get("drop_pct") or 0))
            mover_1h.append(row)
        top_movers = sorted(
            mover_1h, key=lambda x: float(x.get("move_1h_pct") or 0), reverse=True
        )[:5]

        # Book = only symbols you care about: targets + movers watchlist + open positions
        book_syms: set = set()
        book_bases: set = set()

        def _add_book_sym(raw: str) -> None:
            s = (raw or "").upper().strip()
            if not s:
                return
            book_syms.add(s)
            base = (
                s.replace("_USDT", "")
                .replace("USDT", "")
                .replace("_USD", "")
                .replace("STOCK", "")
                .strip("_")
            )
            if base:
                book_bases.add(base)

        for a in alerts:
            _add_book_sym(a.get("symbol") or "")
        try:
            for w in actions.list_watchlist(uid) if uid else []:
                _add_book_sym(w.get("symbol") or "")
        except Exception:
            pass
        positions = actions.list_positions(uid) if uid else []
        for p in positions:
            _add_book_sym(p.get("symbol") or "")

        def _pos_for(sym: str) -> Optional[dict]:
            if not sym:
                return None
            key = sym.upper().replace("_", "")
            for p in positions:
                pk = (p.get("symbol") or "").upper().replace("_", "")
                if pk == key or key in pk or pk in key:
                    return {
                        "id": p.get("id"),
                        "symbol": p.get("symbol"),
                        "entry": p.get("entry_display") or p.get("entry_avg"),
                        "mark": p.get("mark_price"),
                        "upnl_pct": p.get("upnl_pct"),
                        "upnl_usd_est": p.get("upnl_usd_est"),
                        "size_remaining": p.get("size_remaining"),
                        "hold_hours": p.get("hold_hours"),
                        "n_buys": p.get("n_buys"),
                        "n_sells": p.get("n_sells"),
                        "change_24h_pct": p.get("change_24h_pct"),
                    }
            return None

        for row in top_targets:
            row["position"] = _pos_for(row.get("symbol") or "")
        for row in top_movers:
            row["position"] = _pos_for(row.get("symbol") or "")

        def _in_book(sym: str, title: str = "") -> bool:
            """True only if news/intel touches a book symbol (target/mover/position)."""
            if not book_bases and not book_syms:
                return False
            s = (sym or "").upper().strip()
            t = (title or "").upper()
            if s:
                if s in book_syms:
                    return True
                base = (
                    s.replace("_USDT", "")
                    .replace("USDT", "")
                    .replace("_USD", "")
                    .replace("STOCK", "")
                    .strip("_")
                )
                if base and base in book_bases:
                    return True
            # Title mention of a book base (whole-ish token)
            for b in book_bases:
                if len(b) < 2:
                    continue
                if b in t.split() or f" {b} " in f" {t} " or t.startswith(b + " ") or t.endswith(" " + b):
                    return True
            return False

        recent_inv = (
            db.fetch_all(
                """
                SELECT id, symbol, market, drop_pct, velocity_band, heat_breadth,
                       verdict, confidence, ts
                FROM investigations WHERE user_id = ? ORDER BY ts DESC LIMIT 30
                """,
                (uid,),
            )
            if uid
            else []
        )
        # Isolated-dump investigations still available (secondary)
        book_intel = [i for i in recent_inv if _in_book(i.get("symbol") or "")][:6]

        # Book intel strip: always latest bad news (delist/scam/hack/closure/listing).
        # No 48h cut-off — old delist notices remain useful reminders while coin still trades.
        try:
            from .bad_intel import load_bad_intel_feed

            book_news = load_bad_intel_feed(
                db.fetch_all,
                limit=5,
                book_bases=book_bases,
                now=now_ts,
            )
        except Exception as e:
            logger.debug("bad_intel feed: %s", e)
            book_news = []

        # Learning snapshot + Needs you (desk-native)
        labels_recent = (
            db.fetch_all(
                """
                SELECT l.action, l.bounce_quality, l.ts, e.symbol, e.velocity_band
                FROM learning_labels l
                JOIN learning_events e ON e.id = l.event_id
                WHERE l.user_id = ?
                ORDER BY l.ts DESC LIMIT 8
                """,
                (uid,),
            )
            if uid
            else []
        )
        needs_you: Dict[str, Any] = {
            "pending_questions": [],
            "count": 0,
        }
        agent_summary = ""
        learn_stats: Dict[str, Any] = {}
        if uid:
            try:
                from .learning_v1 import learning_home_v1

                lb = learning_home_v1(uid)
                needs_you = lb.get("needs_you") or needs_you
                agent_summary = lb.get("agent_summary") or lb.get("what_learned_reply") or ""
                learn_stats = lb.get("stats") or {}
            except Exception as e:
                logger.debug("learning_home_v1: %s", e)

        # Simple journal PnL sketch (closed trades with entry+exit)
        closed = (
            db.fetch_all(
                """
                SELECT entry_avg, exit_avg, symbol, closed_at
                FROM journal_trades
                WHERE user_id=? AND status='closed' AND entry_avg IS NOT NULL AND exit_avg IS NOT NULL
                ORDER BY closed_at DESC LIMIT 50
                """,
                (uid,),
            )
            if uid
            else []
        )
        realized_pct = []
        for t in closed:
            try:
                en, ex = float(t["entry_avg"]), float(t["exit_avg"])
                if en > 0:
                    realized_pct.append((ex - en) / en * 100.0)
            except Exception:
                pass
        pnl = {
            "mode": "journal",
            "open_n": len(positions),
            "closed_n": len(closed),
            "realized_avg_pct": (
                round(sum(realized_pct) / len(realized_pct), 2) if realized_pct else None
            ),
            "note": "Mark-to-market + MEXC fills when private read is on",
        }

        # Enrich open positions with rough time open
        now = time.time()
        for p in positions:
            oa = p.get("opened_at")
            try:
                p["open_hours"] = round((now - float(oa)) / 3600.0, 1) if oa else None
            except Exception:
                p["open_hours"] = None

        regime = ctx.get("regime", "UNKNOWN")
        return {
            "user_id": uid,
            "market": ctx,
            "counts": counts,
            "hierarchy": {
                "needs_you": needs_you,
                "top_targets": top_targets,
                "top_movers": top_movers,
                "book_intel": book_intel,
                "book_news": book_news,
                "positions": positions,
                "agent_summary": agent_summary,
                "learning": {
                    "recent_labels": labels_recent,
                    "stats": learn_stats,
                },
                "pnl": pnl,
            },
            "needs_you": needs_you,
            "agent_summary": agent_summary,
            "what_learned_reply": agent_summary,
            "positions": positions,
            "recent_events": recent_events[:12],
            "recent_investigations": recent_inv[:8],
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
                "agent": agent_summary,
            },
            "vision": "docs/AD_DESK_VISION.md",
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

    @app.get("/api/mover-sets")
    def get_mover_sets(_: bool = Depends(require_auth)):
        try:
            return {"sets": actions.list_mover_sets()}
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.post("/api/mover-sets")
    def post_mover_set(body: MoverSetCreate, _: bool = Depends(require_auth)):
        try:
            return actions.create_mover_set(
                body.name,
                threshold_percent=body.threshold_percent,
                lookback_minutes=body.lookback_minutes,
                enabled=body.enabled,
            )
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.patch("/api/mover-sets/{set_id}")
    def patch_mover_set(
        set_id: int, body: MoverSetPatch, _: bool = Depends(require_auth)
    ):
        try:
            return actions.update_mover_set(
                set_id,
                name=body.name,
                enabled=body.enabled,
                threshold_percent=body.threshold_percent,
                lookback_minutes=body.lookback_minutes,
            )
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.delete("/api/mover-sets/{set_id}")
    def delete_mover_set(set_id: int, _: bool = Depends(require_auth)):
        try:
            return actions.delete_mover_set(set_id)
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.get("/api/watchlist")
    def get_watch(
        set_id: Optional[int] = None, _: bool = Depends(require_auth)
    ):
        try:
            uid = db.default_user_id()
            sets = actions.list_mover_sets() if uid else []
            rows = actions.list_watchlist(set_id=set_id)
            symbols = []
            for r in rows:
                s = r["symbol"]
                symbols.append(s.replace("_", "") if "_" in s else s)
            tickers = watchlist_tickers(symbols[:40])
            # Last fire per symbol (learning_events) for live desk columns
            last_fires: Dict[str, dict] = {}
            if uid:
                try:
                    fl = db.fetch_all(
                        """
                        SELECT id, symbol, market, source, ts, price, ref_price,
                               drop_pct, velocity_band, mode
                        FROM learning_events
                        WHERE user_id = ?
                          AND source IN ('mover_peak','mover_step','target')
                        ORDER BY ts DESC LIMIT 80
                        """,
                        (int(uid),),
                    )
                    for e in fl:
                        k = str(e.get("symbol") or "").upper()
                        if k and k not in last_fires:
                            last_fires[k] = e
                            # also compact key without underscore
                            ck = k.replace("_", "")
                            if ck not in last_fires:
                                last_fires[ck] = e
                except Exception:
                    last_fires = {}
            for r in rows:
                sym = str(r.get("symbol") or "").upper()
                compact = sym.replace("_", "")
                lf = last_fires.get(sym) or last_fires.get(compact)
                if lf:
                    r["last_fire"] = {
                        "id": lf.get("id"),
                        "source": lf.get("source"),
                        "mode": lf.get("mode"),
                        "ts": lf.get("ts"),
                        "price": lf.get("price"),
                        "drop_pct": lf.get("drop_pct"),
                        "velocity_band": lf.get("velocity_band"),
                    }
            settings = None
            if uid:
                if set_id is not None:
                    for s in sets:
                        if int(s["id"]) == int(set_id):
                            settings = {
                                "enabled": s["enabled"],
                                "threshold_percent": s["threshold_percent"],
                                "lookback_seconds": s["lookback_seconds"],
                                "set_id": s["id"],
                                "name": s["name"],
                            }
                            break
                if settings is None:
                    settings = actions.get_movers_settings()
            return {
                "user_id": uid,
                "watchlist": rows,
                "settings": settings,
                "sets": sets,
                "active_set_id": set_id,
                "tickers": tickers,
            }
        except ValueError:
            return {
                "watchlist": [],
                "tickers": [],
                "settings": None,
                "sets": [],
            }

    @app.get("/api/desk/alarms")
    def desk_alarms(
        since_id: int = Query(0, ge=0),
        since_ts: float = Query(0, ge=0),
        limit: int = Query(30, ge=1, le=100),
        _: bool = Depends(require_auth),
    ):
        """Slim fire feed for desk toasts / overview (mover + target)."""
        uid = db.default_user_id()
        if not uid:
            return {"alarms": [], "max_id": 0}
        try:
            if since_id > 0:
                rows = db.fetch_all(
                    """
                    SELECT id, symbol, market, source, ts, price, ref_price,
                           drop_pct, velocity_band, mode
                    FROM learning_events
                    WHERE user_id = ? AND id > ?
                      AND source IN ('mover_peak','mover_step','target')
                    ORDER BY id ASC LIMIT ?
                    """,
                    (int(uid), int(since_id), int(limit)),
                )
            elif since_ts > 0:
                rows = db.fetch_all(
                    """
                    SELECT id, symbol, market, source, ts, price, ref_price,
                           drop_pct, velocity_band, mode
                    FROM learning_events
                    WHERE user_id = ? AND ts > ?
                      AND source IN ('mover_peak','mover_step','target')
                    ORDER BY ts ASC LIMIT ?
                    """,
                    (int(uid), float(since_ts), int(limit)),
                )
            else:
                rows = db.fetch_all(
                    """
                    SELECT id, symbol, market, source, ts, price, ref_price,
                           drop_pct, velocity_band, mode
                    FROM learning_events
                    WHERE user_id = ?
                      AND source IN ('mover_peak','mover_step','target')
                    ORDER BY ts DESC LIMIT ?
                    """,
                    (int(uid), int(limit)),
                )
                rows = list(reversed(rows))
            max_id = 0
            for r in rows:
                try:
                    max_id = max(max_id, int(r.get("id") or 0))
                except (TypeError, ValueError):
                    pass
            return {"user_id": uid, "alarms": rows, "max_id": max_id}
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.post("/api/watchlist")
    def post_watch(body: WatchBody, _: bool = Depends(require_auth)):
        try:
            return actions.add_watch(body.symbol, body.market, set_id=body.set_id)
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.post("/api/watchlist/restore-from-fires")
    def post_watch_restore(
        days: float = Query(7.0, ge=1.0, le=30.0),
        set_id: Optional[int] = None,
        _: bool = Depends(require_auth),
    ):
        """Re-add coins that mover-fired recently (after accidental wipe / migration)."""
        try:
            return actions.restore_watchlist_from_fires(days=days, set_id=set_id)
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.delete("/api/watchlist")
    def del_watch(
        symbol: str = Query(...),
        market: Optional[str] = None,
        set_id: Optional[int] = None,
        _: bool = Depends(require_auth),
    ):
        try:
            return actions.remove_watch(symbol, market, set_id=set_id)
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.post("/api/movers")
    def post_movers(body: MoversBody, _: bool = Depends(require_auth)):
        try:
            return actions.set_movers(
                enabled=body.enabled,
                threshold_percent=body.threshold_percent,
                lookback_minutes=body.lookback_minutes,
                set_id=body.set_id,
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

    @app.post("/api/positions/flags")
    def post_position_flag(body: PositionFlagBody, _: bool = Depends(require_auth)):
        """Mark free coins and/or long-term hold (exclude from AD learning)."""
        from ..learning.store import EventStore

        try:
            uid = db.default_user_id()
            if not uid:
                raise HTTPException(400, "DESK_USER_ID not set")
            store = EventStore(db.db_path())
            dump = (
                body.model_dump(exclude_unset=True)
                if hasattr(body, "model_dump")
                else body.dict(exclude_unset=True)
            )
            update_free = "free_coins_override" in dump or "free_mark_usd" in dump
            update_book = "book" in dump
            if not update_free and not update_book:
                raise ValueError("Provide free_coins_override and/or book")
            row = store.set_position_flag(
                int(uid),
                body.entity_key,
                symbol=body.symbol,
                market=body.market,
                free_coins_override=body.free_coins_override,
                free_mark_usd=body.free_mark_usd,
                book=body.book,
                notes=body.notes,
                update_free=update_free,
                update_book=update_book,
            )
            return {"ok": True, "flag": row}
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.get("/api/pnl")
    def get_pnl(
        window: str = Query("all"),
        range_name: Optional[str] = Query(None, alias="range"),
        from_date: Optional[str] = Query(None, alias="from"),
        to_date: Optional[str] = Query(None, alias="to"),
        _: bool = Depends(require_auth),
    ):
        """Smart PnL — full closed history by default (window/range=all).

        ``from`` / ``to`` are Manila calendar dates (YYYY-MM-DD). They filter
        the displayed closed list only — history is never deleted.
        """
        from .pnl import build_pnl_summary

        try:
            uid = db.default_user_id()
            if not uid:
                return {"error": "no user", "bankroll": {}, "realized": {}}
            w = (range_name or window or "all").strip() or "all"
            return build_pnl_summary(
                int(uid), window=w, from_date=from_date, to_date=to_date
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
    def news(limit: int = Query(50, ge=1, le=150), _: bool = Depends(require_auth)):
        """Fatal news + delist radar. Always expand full ticker lists for delists."""
        from pathlib import Path

        from ..investigators.store import InvestigatorStore
        from ..news.sources import enrich_news_item_bases, _enrich_mexc_article, _session
        from ..news.tickers import extract_delist_bases

        delist_rows = db.fetch_all(
            "SELECT * FROM delist_cache ORDER BY ts DESC LIMIT ?",
            (min(500, int(limit) * 12),),
        )
        announcements: list = []
        try:
            inv = InvestigatorStore(Path(db.db_path()))
            announcements = inv.list_delist_announcements(limit=int(limit))
        except Exception:
            announcements = []

        news_rows = db.fetch_all(
            "SELECT * FROM news_events ORDER BY ts DESC LIMIT ?", (limit,)
        )
        # Expand incomplete MEXC "and N other" titles for the desk
        sess = None
        enriched_news = []
        for row in news_rows:
            item = dict(row)
            bases = enrich_news_item_bases(item)
            title = item.get("title") or ""
            url = item.get("url") or ""
            needs = (
                (not bases or len(bases) < 3)
                and "mexc" in (item.get("source") or "").lower()
                and (
                    re.search(r"and\s+\d+\s+other", title, re.I)
                    or "/announcements/article/" in url
                )
            )
            if needs and "/announcements/article/" in url:
                try:
                    if sess is None:
                        sess = _session()
                    enr = _enrich_mexc_article(sess, url, title, timeout=12.0)
                    more = enr.get("bases") or []
                    if more:
                        bases = more
                        # Persist backfill so next load is free
                        try:
                            from pathlib import Path as _P

                            from ..news.store import NewsStore

                            ns = NewsStore(_P(db.db_path()))
                            list_title = title.split(" · full:")[0].strip()
                            display = (
                                f"{list_title} · full: {', '.join(bases)}"
                                if re.search(r"and\s+\d+\s+other", list_title, re.I)
                                else title
                            )
                            raw = item.get("raw_json")
                            import json as _json

                            try:
                                raw_d = _json.loads(raw) if raw else {}
                            except Exception:
                                raw_d = {}
                            raw_d["bases"] = bases
                            raw_d["body"] = enr.get("body") or raw_d.get("body")
                            ns.update_news_symbols(
                                int(item["id"]),
                                symbol=",".join(bases),
                                title=display,
                                raw=raw_d,
                            )
                            item["title"] = display
                            item["symbol"] = ",".join(bases)
                        except Exception:
                            pass
                except Exception:
                    pass
            if not bases and item.get("symbol"):
                bases = extract_delist_bases(item.get("symbol") or "", title)
            item["bases"] = bases
            item["bases_text"] = ", ".join(bases) if bases else (item.get("symbol") or "—")
            enriched_news.append(item)

        return {
            "news": enriched_news,
            "delist_cache": delist_rows[: max(40, int(limit))],
            "delist_announcements": announcements,
        }

    @app.get("/api/prices")
    def prices(
        symbols: str = Query("BTCUSDT,ETHUSDT,SOLUSDT"),
        _: bool = Depends(require_auth),
    ):
        syms = [s.strip() for s in symbols.split(",") if s.strip()]
        return {"tickers": watchlist_tickers(syms), "context": market_context()}

    @app.get("/api/learning")
    def learning_home(_: bool = Depends(require_auth)):
        """Learning V1 — teach the agent / what it has learned."""
        try:
            from .learning_v1 import learning_home_v1

            return learning_home_v1()
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.post("/api/learning/ask")
    def learning_ask(payload: AgentAskBody, _: bool = Depends(require_auth)):
        try:
            from .learning_v1 import agent_ask

            return agent_ask(payload.question or "What have you learned so far?")
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.get("/api/learning/what-learned")
    def learning_what_learned(_: bool = Depends(require_auth)):
        try:
            from .learning_v1 import what_have_you_learned

            return what_have_you_learned()
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.post("/api/learning/judge")
    def learning_judge(
        event_id: Optional[int] = None,
        symbol: Optional[str] = None,
        _: bool = Depends(require_auth),
    ):
        from .learning_api import judge_fire

        try:
            return judge_fire(event_id=event_id, symbol=symbol, open_case=True)
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.post("/api/learning/judge_body")
    def learning_judge_body(body: JudgeBody, _: bool = Depends(require_auth)):
        from .learning_api import judge_fire

        try:
            return judge_fire(
                event_id=body.event_id, symbol=body.symbol, open_case=True
            )
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.post("/api/learning/chart")
    def learning_chart(body: ChartReadBody, _: bool = Depends(require_auth)):
        from .learning_api import read_symbol_chart

        try:
            return read_symbol_chart(
                body.symbol, market=body.market, refresh=bool(body.refresh)
            )
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.post("/api/learning/chart/book")
    def learning_chart_book(_: bool = Depends(require_auth)):
        from .learning_api import refresh_book_charts

        try:
            return refresh_book_charts()
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.post("/api/learning/correct")
    def learning_correct(body: CorrectBody, _: bool = Depends(require_auth)):
        from .learning_api import correct_judgment

        try:
            return correct_judgment(
                correct_verdict=body.correct_verdict,
                reason=body.reason,
                event_id=body.event_id,
                case_id=body.case_id,
                symbol=body.symbol,
            )
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.get("/api/learning/case-preview")
    def learning_case_preview(
        event_id: Optional[int] = None,
        symbol: Optional[str] = None,
        market: Optional[str] = None,
        _: bool = Depends(require_auth),
    ):
        """P1: frozen or live-computed setup case for Learning teach panel."""
        from ..learning.cases import case_preview
        from .learning_v1 import event_store, uid_or_raise

        try:
            uid = uid_or_raise()
            return case_preview(
                event_store(),
                uid,
                event_id=event_id,
                symbol=symbol,
                market=market,
            )
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.post("/api/learning/cases/{case_id}/visual-ad")
    def learning_visual_ad_write(
        case_id: int, body: VisualAdBody, _: bool = Depends(require_auth)
    ):
        """Merge a staff visual AD onto a frozen case. Does not invent one."""
        from ..learning.cases import case_public_view
        from .learning_v1 import event_store, uid_or_raise

        payload = (
            body.model_dump(exclude_none=True)
            if hasattr(body, "model_dump")
            else body.dict(exclude_none=True)
        )
        if not payload:
            raise HTTPException(400, "visual_ad needs tf, high, low, note, or image_relpath")
        try:
            uid = uid_or_raise()
            store = event_store()
            row = store.merge_visual_ad(uid, int(case_id), payload)
            if not row:
                raise HTTPException(404, "Case not found")
            view = case_public_view(row)
            view["ok"] = True
            return view
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.get("/api/learning/cases/{case_id}/visual-ad/image")
    def learning_visual_ad_image(case_id: int, _: bool = Depends(require_auth)):
        """Serve a stored visual-AD image from data/.grokbot/visual_ad/ only."""
        from ..learning.visual_ad import (
            extract_visual_ad,
            parse_features_json,
            resolve_visual_ad_image,
        )
        from .learning_v1 import event_store, uid_or_raise

        try:
            uid = uid_or_raise()
            store = event_store()
            row = store.get_setup_case(uid, case_id=int(case_id))
            if not row:
                raise HTTPException(404, "No visual AD image")
            vad = extract_visual_ad(parse_features_json(row.get("features_json")))
            rel = (vad or {}).get("image_relpath")
            if not rel:
                raise HTTPException(404, "No visual AD image")
            path = resolve_visual_ad_image(store.db_path, str(rel))
            if not path.is_file():
                raise HTTPException(404, "No visual AD image")
            return FileResponse(path)
        except HTTPException:
            raise
        except ValueError:
            raise HTTPException(404, "No visual AD image")
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.get("/api/learning/incident-candles")
    def learning_incident_candles(
        case_id: Optional[int] = None,
        event_id: Optional[int] = None,
        symbol: Optional[str] = None,
        market: Optional[str] = None,
        fire_ts: Optional[float] = None,
        tf: Optional[str] = None,
        _: bool = Depends(require_auth),
    ):
        """OHLC for that fire/case incident. Not live TV. Does not invent visual_ad."""
        from ..learning.incident_ohlc import incident_candles
        from .learning_v1 import event_store, uid_or_raise

        try:
            uid = uid_or_raise()
            return incident_candles(
                event_store(),
                uid,
                case_id=case_id,
                event_id=event_id,
                symbol=symbol,
                market=market,
                fire_ts=fire_ts,
                tf=tf,
            )
        except FileNotFoundError as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.get("/api/learning/similar-cases")
    def learning_similar_cases(
        case_id: Optional[int] = None,
        symbol: Optional[str] = None,
        market: Optional[str] = None,
        k: int = 5,
        _: bool = Depends(require_auth),
    ):
        """P1 index: nearest frozen setups. Scores only — not advice."""
        from ..learning.cases import case_public_view
        from ..learning.retrieve import similar_cases
        from .learning_v1 import event_store, uid_or_raise

        try:
            uid = uid_or_raise()
            store = event_store()
            rows = store.list_setup_cases(uid, limit=80)
            views = [case_public_view(r) for r in rows]
            query = None
            if case_id:
                query = next((v for v in views if v.get("id") == int(case_id)), None)
            if query is None:
                # Never recompute klines here — pick already fetched case-preview.
                if not symbol:
                    return {"ok": True, "query": None, "similar": []}
                query = {
                    "symbol": symbol,
                    "market": market or "futures",
                }
            neighbors = similar_cases(
                views, query, k=min(max(int(k), 1), 8), exclude_id=query.get("id")
            )
            return {"ok": True, "query": {"id": query.get("id"), "symbol": query.get("symbol")}, "similar": neighbors}
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.post("/api/learning/teach")
    def learning_teach(body: TeachBody, _: bool = Depends(require_auth)):
        from .learning_api import teach

        if not (body.text or "").strip():
            raise HTTPException(400, "Empty lesson text")
        try:
            return teach(
                body.text,
                tags=body.tags,
                needs_approval=bool(body.needs_approval),
                symbol=body.symbol,
                market=body.market,
                entity_key=body.entity_key,
                event_id=body.event_id,
                behaviors=body.behaviors,
                context_type=body.context_type,
            )
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.post("/api/learning/normalize-index")
    def learning_normalize_index(_: bool = Depends(require_auth)):
        """One-shot: rewrite sym tags + case symbols; stamp buckets + incident anchors."""
        from .learning_api import event_store, uid_or_raise

        try:
            uid = uid_or_raise()
            out = event_store().normalize_learning_index(int(uid))
            return {"ok": True, **out}
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.post("/api/learning/approve")
    def learning_approve(body: ApproveBody, _: bool = Depends(require_auth)):
        from .learning_api import approve_draft

        try:
            return approve_draft(body.lesson_id, dismiss=bool(body.dismiss))
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.patch("/api/learning/lessons/{lesson_id}")
    def learning_edit_lesson(
        lesson_id: int, body: LessonEditBody, _: bool = Depends(require_auth)
    ):
        """Owner can manually edit any lesson text / chips on the desk."""
        from .learning_api import update_lesson

        try:
            out = update_lesson(
                int(lesson_id),
                text=body.text,
                tags=body.tags,
                behaviors=body.behaviors,
            )
            if not out.get("ok"):
                err = out.get("error") or "update failed"
                if err == "not_found":
                    raise HTTPException(404, "Lesson not found")
                raise HTTPException(400, err)
            return out
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.delete("/api/learning/lessons/{lesson_id}")
    def learning_delete_lesson(lesson_id: int, _: bool = Depends(require_auth)):
        from .learning_api import delete_lesson

        try:
            out = delete_lesson(int(lesson_id))
            if not out.get("ok"):
                raise HTTPException(404, "Lesson not found")
            return out
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.post("/api/learning/answer")
    def learning_answer(body: AnswerBody, _: bool = Depends(require_auth)):
        from .learning_api import answer_question

        try:
            return answer_question(
                body.question_id,
                answer_text=body.answer_text,
                action=body.action,
                behavior=body.behavior,
                dismiss=bool(body.dismiss),
            )
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.get("/api/learning/trades")
    def learning_trades(
        closed_only: bool = Query(False),
        open_only: bool = Query(False),
        symbol: Optional[str] = Query(None),
        limit: int = Query(30, ge=1, le=100),
        teach_only: bool = Query(True),
        _: bool = Depends(require_auth),
    ):
        from .learning_api import trades_api

        try:
            return trades_api(
                closed_only=closed_only,
                open_only=open_only,
                symbol=symbol,
                limit=limit,
                teach_only=teach_only,
            )
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.get("/api/learning/trades/{trade_id}")
    def learning_trade_one(trade_id: str, _: bool = Depends(require_auth)):
        from .learning_api import trade_api

        try:
            return trade_api(trade_id)
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.post("/api/learning/trades/tag")
    def learning_trade_tag(body: TagTradeBody, _: bool = Depends(require_auth)):
        from .learning_api import tag_trade

        try:
            return tag_trade(
                body.trade_id, behavior=body.behavior, notes=body.notes
            )
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.get("/api/learning/ticker/{symbol}")
    def learning_ticker(
        symbol: str,
        market: Optional[str] = Query(None),
        _: bool = Depends(require_auth),
    ):
        from .learning_api import ticker_api

        try:
            return ticker_api(symbol, market=market)
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.post("/api/coach")
    def coach(body: CoachBody, _: bool = Depends(require_auth)):
        """Back-compat → agent recall (no coach product)."""
        from .learning_v1 import agent_ask

        q = (body.question or body.message or "What have you learned so far?").strip()
        try:
            out = agent_ask(q)
        except Exception as e:
            raise HTTPException(400, str(e))
        return {
            "reply": out.get("reply"),
            "stats": out.get("stats"),
            "market": market_context(),
            "ts": time.time(),
        }

    @app.get("/api/notify/stub")
    def notify_stub(_: bool = Depends(require_auth)):
        """Future multi-device desk push (not Telegram). Stub only."""
        return {
            "status": "stub",
            "channels": ["web_push", "desktop"],
            "note": "Alarms stay on Telegram bot until desk push ships",
            "ready": False,
        }

    @app.post("/api/agent")
    def agent_text(body: AgentBody, _: bool = Depends(require_auth)):
        """Grok tool-using agent (text). Continuous history + full desk tools."""
        out = chat_with_tools(body.message, history=body.history)
        return out

    @app.post("/api/tts")
    def agent_tts(body: TtsBody, _: bool = Depends(require_auth)):
        """Speak text via xAI TTS (mp3 base64). Used for call replies."""
        text = (body.text or "").strip()
        if not text:
            raise HTTPException(400, "Empty text")
        raw = tts_speak(text)
        if not raw:
            raise HTTPException(502, "TTS failed — check XAI_API_KEY / voice_id")
        return {
            "ok": True,
            "audio_b64": base64.b64encode(raw).decode("ascii"),
            "format": "mp3",
        }

    @app.post("/api/voice")
    async def agent_voice(
        file: UploadFile = File(...),
        history: Optional[str] = Form(None),
        _: bool = Depends(require_auth),
    ):
        """Voice call turn: STT → tools → TTS reply. Pass history JSON for multi-turn."""
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
                "v2_next": "overview polish, engagement soak; Voice 2.0 deferred; coach only if re-opened",
            },
        }

    if _feature_ad_machine():
        from ..machine.api import router as machine_router
        from ..machine.loop import ensure_tape_loop

        app.include_router(machine_router)
        ensure_tape_loop()

        @app.get("/machine")
        def machine_page():
            path = STATIC_DIR / "machine.html"
            if not path.is_file():
                raise HTTPException(status_code=404, detail="Not found")
            return FileResponse(path)
    else:

        @app.api_route("/api/machine", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
        @app.api_route(
            "/api/machine/{full_path:path}",
            methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        )
        def machine_api_off(full_path: str = ""):
            _machine_not_found()

        @app.get("/machine")
        @app.get("/machine.html")
        def machine_page_off():
            _machine_not_found()

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
            if full_path in {"machine", "machine.html"} and not _feature_ad_machine():
                raise HTTPException(status_code=404, detail="Not found")
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
