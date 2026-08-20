"""AD Super-Agent API — judgments, beliefs, cases, chart features, voice.

Replaces tag-farm learning surface with training cockpit endpoints.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from ..learning.beliefs import BeliefEngine
from ..learning.chart_features import compute_fire_features
from ..learning.chart_reader import ChartProfileStore, read_chart
from ..learning.store import EventStore
from ..learning.trades import (
    enrich_pending_row,
    get_trade_dossier,
    list_trade_dossiers,
)
from . import db


def event_store() -> EventStore:
    return EventStore(db.db_path())


def beliefs() -> BeliefEngine:
    return BeliefEngine(event_store())


def uid_or_raise() -> int:
    uid = db.default_user_id()
    if not uid:
        env = os.getenv("DESK_USER_ID") or "8630949601"
        if str(env).strip().isdigit():
            return int(env)
        raise ValueError("No DESK_USER_ID")
    return int(uid)


def _event_row(store: EventStore, user_id: int, event_id: int) -> Optional[dict]:
    with store._lock:
        r = store._get_conn().execute(
            "SELECT * FROM learning_events WHERE id=? AND user_id=?",
            (int(event_id), int(user_id)),
        ).fetchone()
    return dict(r) if r else None


def _attach_chart(event: dict) -> dict:
    """Full discretionary chart read + numeric features for a fire."""
    mkt = str(event.get("market") or "futures")
    sym = str(event.get("symbol") or "")
    read = read_chart(
        mkt,
        sym,
        mark_price=float(event.get("price") or 0) or None,
        fire_price=float(event.get("price") or 0) or None,
        fire_ts=float(event.get("ts") or time.time()),
        peak_price=float(event["ref_price"]) if event.get("ref_price") else None,
        heat_breadth=event.get("heat_breadth"),
        velocity_band=event.get("velocity_band"),
    )
    feats = read.get("features") or compute_fire_features(
        market=mkt,
        symbol=sym,
        fire_px=float(event.get("price") or 0),
        fire_ts=float(event.get("ts") or time.time()),
        peak_px=float(event["ref_price"]) if event.get("ref_price") else None,
        heat_breadth=event.get("heat_breadth"),
        velocity_band=event.get("velocity_band"),
    )
    feats = dict(feats or {})
    feats["thesis"] = read.get("thesis")
    feats["bias"] = read.get("bias")
    feats["regime"] = read.get("regime")
    feats["ad_zone"] = read.get("ad_zone") or feats.get("ad_zone")
    feats["ok"] = bool(read.get("ok") or feats.get("ok"))
    feats["discretionary_read"] = {
        k: read.get(k)
        for k in (
            "regime",
            "pace",
            "vol_flag",
            "ad_zone",
            "bias",
            "levels",
            "invalidation",
            "happening_now",
            "history_summary",
            "thesis",
        )
    }
    try:
        store = event_store()
        uid = int(event.get("user_id") or uid_or_raise())
        ChartProfileStore(store).save(uid, read)
        with store._lock:
            conn = store._get_conn()
            raw = event.get("payload_json")
            payload = json.loads(raw) if raw else {}
            payload["chart_features"] = feats
            payload["chart_read"] = read
            conn.execute(
                "UPDATE learning_events SET payload_json=? WHERE id=?",
                (json.dumps(payload), int(event["id"])),
            )
    except Exception:
        pass
    return feats


def read_symbol_chart(
    symbol: str,
    market: Optional[str] = None,
    user_id: Optional[int] = None,
    *,
    refresh: bool = True,
) -> Dict[str, Any]:
    """On-demand full chart thesis for a book symbol (voice/UI)."""
    uid = int(user_id or uid_or_raise())
    store = event_store()
    mkt = (market or "futures").lower()
    # Prefer last event price context
    fire_px = peak = heat = band = None
    fts = None
    for e in store.recent_events(uid, limit=40):
        if symbol.upper().replace("_", "") in (e.get("symbol") or "").upper().replace(
            "_", ""
        ):
            fire_px = e.get("price")
            peak = e.get("ref_price")
            heat = e.get("heat_breadth")
            band = e.get("velocity_band")
            fts = e.get("ts")
            mkt = e.get("market") or mkt
            symbol = e.get("symbol") or symbol
            break
    if not refresh:
        cached = ChartProfileStore(store).get(uid, symbol, mkt)
        if cached and cached.get("ok"):
            return {"chart": cached, "cached": True}
    read = read_chart(
        mkt,
        symbol,
        fire_price=float(fire_px) if fire_px else None,
        peak_price=float(peak) if peak else None,
        fire_ts=float(fts) if fts else None,
        heat_breadth=heat,
        velocity_band=band,
    )
    ChartProfileStore(store).save(uid, read)
    return {"chart": read, "cached": False}


def refresh_book_charts(user_id: Optional[int] = None) -> Dict[str, Any]:
    """Re-read all book charts: targets + watchlist + open positions."""
    uid = int(user_id or uid_or_raise())
    book: List[tuple] = []
    try:
        from . import actions

        for a in actions.list_alerts(uid):
            book.append((a.get("market") or "spot", a.get("symbol")))
        for w in actions.list_watchlist(uid):
            book.append((w.get("market") or "futures", w.get("symbol")))
        for p in actions.list_positions(uid):
            book.append((p.get("market") or "futures", p.get("symbol")))
    except Exception:
        pass
    # dedupe
    seen = set()
    uniq = []
    for m, s in book:
        if not s:
            continue
        key = (m, str(s).upper())
        if key in seen:
            continue
        seen.add(key)
        uniq.append((m, s))
    store = event_store()
    cps = ChartProfileStore(store)
    results = []
    for m, s in uniq[:30]:
        r = read_chart(m, s)
        cps.save(uid, r)
        results.append(
            {
                "symbol": s,
                "market": m,
                "ok": r.get("ok"),
                "bias": r.get("bias"),
                "ad_zone": r.get("ad_zone"),
                "thesis_head": (r.get("thesis") or "")[:160],
            }
        )
    return {"n": len(results), "charts": results}


def judge_fire(
    *,
    event_id: Optional[int] = None,
    symbol: Optional[str] = None,
    user_id: Optional[int] = None,
    open_case: bool = True,
) -> Dict[str, Any]:
    uid = int(user_id or uid_or_raise())
    store = event_store()
    eng = beliefs()
    event = None
    if event_id:
        event = _event_row(store, uid, int(event_id))
    elif symbol:
        recent = store.recent_events(uid, limit=40)
        for e in recent:
            if symbol.upper().replace("_", "") in (e.get("symbol") or "").upper().replace(
                "_", ""
            ):
                event = e
                break
    if not event:
        raise ValueError("No event to judge")

    chart = {}
    try:
        payload = json.loads(event.get("payload_json") or "{}")
        chart = payload.get("chart_features") or {}
    except Exception:
        chart = {}
    if not chart.get("ok"):
        chart = _attach_chart(event)

    judgment = eng.judge_fire(uid, event, chart_features=chart)
    case_id = None
    if open_case and event.get("id"):
        # avoid duplicate open cases for same event
        existing = [
            c
            for c in eng.list_cases(uid, limit=30)
            if c.get("event_id") == event.get("id") and c.get("status") == "open"
        ]
        if existing:
            case_id = existing[0]["id"]
        else:
            case_id = eng.open_case(uid, event, judgment)
    return {"judgment": judgment, "case_id": case_id, "chart": chart}


def agent_bundle(user_id: Optional[int] = None) -> Dict[str, Any]:
    """Training cockpit payload — agent brain first."""
    uid = int(user_id or uid_or_raise())
    store = event_store()
    eng = beliefs()
    cases = eng.list_cases(uid, limit=15)
    open_cases = [c for c in cases if c.get("status") in ("open", "scored")]
    active = open_cases[0] if open_cases else (cases[0] if cases else None)

    # Auto-judge latest unlabeled/recent fire if no active case
    if not active:
        recent = store.recent_events(uid, limit=5)
        if recent:
            try:
                j = judge_fire(event_id=int(recent[0]["id"]), user_id=uid, open_case=True)
                cases = eng.list_cases(uid, limit=15)
                open_cases = [c for c in cases if c.get("status") in ("open", "scored")]
                active = open_cases[0] if open_cases else None
                if active and not active.get("judgment"):
                    active["judgment"] = j.get("judgment")
            except Exception:
                pass

    pending = [
        enrich_pending_row(store, p)
        for p in store.list_pending_questions(uid, status="open", limit=5)
    ]
    setups = eng.list_setup_beliefs(uid, limit=12)
    tickers = eng.list_ticker_beliefs(uid, limit=15)
    from ..learning.money_truth import coach_last_closed_line, list_money_reviews

    # Money facts for teaching = exchange-backed entities (not journal dossiers)
    trades = list_money_reviews(
        uid, limit=12, teach_only=True, store=store
    )
    closed = [t for t in trades if t.get("status") == "closed"]
    stats = store.learning_stats(uid)
    lessons = store.list_lessons(uid, approved_only=True, limit=8)

    # Coach pulse with weight deltas
    pulse_lines = []
    if active and active.get("judgment"):
        j = active["judgment"] if isinstance(active["judgment"], dict) else {}
        s = j.get("setup") or {}
        pulse_lines.append(
            f"Active: {active.get('symbol')} → {s.get('verdict')} "
            f"(conf {j.get('confidence')}) size={j.get('size_hint')}"
        )
        for c in (j.get("cite") or [])[:2]:
            pulse_lines.append(c)
    if setups:
        best = setups[0]
        pulse_lines.append(
            f"Best setup cell {best.get('velocity_band')}+{best.get('heat_bin')}+"
            f"{best.get('drop_bin')}: edge={float(best.get('edge') or 0):+.2f} n={best.get('n')}"
        )
    pulse_lines.append(coach_last_closed_line(uid, store=store))
    if not pulse_lines:
        pulse_lines.append(
            "AD Super-Agent online. Fires train setup edges; closes train exec edges."
        )

    return {
        "user_id": uid,
        "agent": "AD-SuperAgent-v1",
        "active_case": active,
        "cases": cases[:10],
        "needs_you": {
            "pending_questions": pending,
            "count": len(pending),
        },
        "beliefs": {
            "setups": setups,
            "tickers": tickers,
        },
        "trades": trades,
        "closed_trades": closed[:10],
        "stats": stats,
        "lessons": lessons,
        "coach_pulse": "\n".join(pulse_lines),
        "fires": store.recent_events(uid, limit=15),
    }


def belief_setup_top(user_id: Optional[int] = None, limit: int = 15) -> Dict[str, Any]:
    uid = int(user_id or uid_or_raise())
    rows = beliefs().list_setup_beliefs(uid, limit=limit)
    worst = sorted(rows, key=lambda r: float(r.get("edge") or 0))[:5]
    return {"best": rows[:8], "worst": worst}


def belief_ticker(
    symbol: str, market: Optional[str] = None, user_id: Optional[int] = None
) -> Dict[str, Any]:
    uid = int(user_id or uid_or_raise())
    eng = beliefs()
    mkt = market or "futures"
    b = eng.get_ticker_belief(uid, symbol, mkt)
    # also try spot if empty
    if b.get("thin") and not market:
        b2 = eng.get_ticker_belief(uid, symbol, "spot")
        if not b2.get("thin"):
            b = b2
    chart = compute_fire_features(
        market=str(b.get("market") or mkt),
        symbol=symbol,
        fire_px=1.0,  # relative features still need price — soft
        fire_ts=time.time(),
    )
    # If fire_px=1 is bad for AD, only attach when we have a recent event price
    store = event_store()
    recent = store.recent_events(uid, limit=30)
    for e in recent:
        if symbol.upper().replace("_", "") in (e.get("symbol") or "").upper().replace(
            "_", ""
        ):
            chart = compute_fire_features(
                market=str(e.get("market") or mkt),
                symbol=str(e.get("symbol")),
                fire_px=float(e.get("price") or 1),
                fire_ts=float(e.get("ts") or time.time()),
                peak_px=float(e["ref_price"]) if e.get("ref_price") else None,
                heat_breadth=e.get("heat_breadth"),
                velocity_band=e.get("velocity_band"),
            )
            break
    return {"ticker": b, "chart": chart}


def correct_judgment(
    *,
    correct_verdict: str,
    reason: str,
    event_id: Optional[int] = None,
    case_id: Optional[int] = None,
    symbol: Optional[str] = None,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Human overrides agent call and teaches why (belief nudge)."""
    uid = int(user_id or uid_or_raise())
    eng = beliefs()
    # resolve case via symbol → latest case
    if not case_id and not event_id and symbol:
        for c in eng.list_cases(uid, limit=20):
            if symbol.upper().replace("_", "") in (c.get("symbol") or "").upper().replace(
                "_", ""
            ):
                case_id = c.get("id")
                event_id = c.get("event_id")
                break
    return eng.apply_human_correction(
        uid,
        event_id=event_id,
        case_id=case_id,
        correct_verdict=correct_verdict,
        reason=reason,
        adjust_beliefs=True,
    )


def coach_ask(question: str, user_id: Optional[int] = None) -> Dict[str, Any]:
    """Agent-native coach: always load beliefs + optional judge."""
    uid = int(user_id or uid_or_raise())
    store = event_store()
    eng = beliefs()
    q = (question or "").strip()
    ql = q.lower()

    # Natural language correction: "wrong, should be no_trade because ..."
    if any(
        w in ql
        for w in (
            "wrong",
            "incorrect",
            "should be",
            "actually",
            "correct that",
            "change to",
            "i disagree",
        )
    ):
        verdict = None
        for v in ("no_trade", "take_layers", "take_scout", "wait_deeper"):
            if v.replace("_", " ") in ql or v in ql:
                verdict = v
                break
        if "no trade" in ql or "skip" in ql and "take" not in ql:
            verdict = verdict or "no_trade"
        if "full layer" in ql or "take layers" in ql:
            verdict = verdict or "take_layers"
        if "scout" in ql or "micro" in ql:
            verdict = verdict or "take_scout"
        if verdict:
            reason = q
            try:
                corr = correct_judgment(
                    correct_verdict=verdict, reason=reason, user_id=uid
                )
                return {
                    "reply": (
                        f"Correction saved: {corr.get('previous_verdict')} → "
                        f"{corr.get('correct_verdict')}. Reason stored; setup edge nudged.\n"
                        f"{reason[:200]}"
                    ),
                    "correction": corr,
                }
            except Exception as e:
                return {
                    "reply": f"Could not apply correction: {e}. Say verdict: no_trade|take_scout|take_layers|wait_deeper and why.",
                    "error": str(e),
                }

    lines = [
        "AD SUPER-AGENT (not financial advice)",
        "Judgment from setup/ticker edges + AD rules + chart thesis. Self-critical on every call.",
        "",
    ]
    # Try judge latest or mentioned symbol
    judgment = None
    try:
        parts = q.upper().replace("/", " ").split()
        recent = store.recent_events(uid, limit=20)
        for e in recent:
            base = (e.get("symbol") or "").upper().replace("_", "").replace("USDT", "")
            if base and any(base in p or p in base for p in parts if len(p) >= 2):
                judgment = judge_fire(
                    event_id=int(e["id"]), user_id=uid, open_case=False
                )["judgment"]
                break
        if judgment is None and recent:
            if any(w in q.lower() for w in ("judge", "fire", "dump", "panic", "now", "latest")):
                judgment = judge_fire(
                    event_id=int(recent[0]["id"]), user_id=uid, open_case=False
                )["judgment"]
    except Exception:
        judgment = None

    if judgment:
        lines.append(
            f"JUDGMENT {judgment.get('symbol')}: {judgment['setup'].get('verdict')} "
            f"size={judgment.get('size_hint')} conf={judgment.get('confidence')}"
        )
        if judgment.get("human_override"):
            ho = judgment["human_override"]
            lines.append(
                f"  (human override active: {ho.get('previous_verdict')}→{ho.get('verdict')})"
            )
        for c in judgment.get("cite") or []:
            lines.append(f"  · {c}")
        sc = judgment.get("self_critique") or []
        if sc:
            lines.append("SELF-CRITIQUE:")
            for c in sc:
                lines.append(f"  ! {c}")
        ch = judgment.get("chart") or {}
        if ch.get("thesis"):
            lines.append("")
            lines.append("CHART THESIS:")
            lines.append(ch.get("thesis"))
        lines.append("")
        lines.append(
            "To change my call: say e.g. 'wrong — should be no_trade because isolated dump' "
            "or use correct_judgment tool."
        )
        lines.append("")

    setups = eng.list_setup_beliefs(uid, limit=5)
    if setups:
        lines.append("Setup beliefs (trained):")
        for s in setups:
            lines.append(
                f"  {s.get('velocity_band')}+{s.get('heat_bin')}+{s.get('drop_bin')}: "
                f"edge={float(s.get('edge') or 0):+.2f} n={s.get('n')} "
                f"g/b={s.get('n_good')}/{s.get('n_bad')}"
            )
    else:
        lines.append("Setup beliefs: none yet — need outcomes after fires (15m/1h).")

    tickers = eng.list_ticker_beliefs(uid, limit=5)
    if tickers:
        lines.append("Ticker beliefs:")
        for t in tickers:
            lines.append(
                f"  {t.get('symbol')}: setup={float(t.get('setup_edge') or 0):+.2f} "
                f"exec={float(t.get('exec_edge') or 0):+.2f} n={t.get('n_fires')}"
            )

    from ..learning.money_truth import list_money_reviews

    trades = list_money_reviews(
        uid, closed_only=True, limit=4, teach_only=True, store=store
    )
    if trades:
        lines.append("Recent closed trades (exchange-verified):")
        for d in trades:
            pnl = d.get("pnl_pct")
            usd = d.get("pnl_usd")
            lines.append(
                f"  {d.get('symbol')} [{d.get('money_truth')}] "
                f"pnl={pnl}% usd={usd} hold={d.get('hold_hours')}h "
                f"layers={d.get('n_buys')}/{d.get('n_sells')}"
            )
    else:
        lines.append(
            "Recent closed: none exchange-verified yet — do not invent $ PnL."
        )

    ql = q.lower()
    if "grind" in ql:
        lines.append(
            "Rule: GRIND → no_trade / micro only unless ticker setup_edge high with n≥10."
        )
    elif "panic" in ql:
        lines.append(
            "Rule: PANIC + broad heat + positive setup edge → take_layers; thin data → scout."
        )
    elif any(w in ql for w in ("rsi", "divergence", "volume", "candle", "chart")):
        lines.append(
            "Chart engine: sharpness, AD depth zone, volume expand/dry, RSI + bullish div proxy."
        )

    lines.append("")
    lines.append("Voice: judge_fire · belief_setup_top · belief_ticker · trade_review · record_process")
    return {
        "reply": "\n".join(lines),
        "judgment": judgment,
        "setups": setups,
        "tickers": tickers,
    }


def record_process(
    trade_id: Any,
    *,
    tags: Optional[List[str]] = None,
    note: Optional[str] = None,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Train exec edge from process tags on an exchange-backed review or journal id."""
    uid = int(user_id or uid_or_raise())
    store = event_store()
    tags = tags or []
    from ..learning.money_truth import get_money_review

    # Prefer money-truth entity (entity_key / synthetic id)
    d = get_money_review(uid, trade_id, store=store)
    if d is None:
        # legacy journal id — never treat as exchange money truth
        try:
            d = get_trade_dossier(store, uid, int(trade_id))
            if d:
                d = dict(d)
                d["money_truth"] = "journal_manual"
                d["teach_ok"] = False
                d["verified"] = False
        except (TypeError, ValueError):
            d = None
    if not d:
        raise ValueError("Trade/review not found")
    # Persist process tags: journal notes when numeric id; else durable lesson
    if tags or note:
        journal_updated = False
        try:
            tid = int(trade_id)
            for t in tags:
                note_part = f"[{t}]"
                with store._lock:
                    conn = store._get_conn()
                    row = conn.execute(
                        "SELECT notes FROM journal_trades WHERE id=? AND user_id=?",
                        (tid, uid),
                    ).fetchone()
                    if row is not None:
                        old = (row["notes"] if row else "") or ""
                        if note_part not in old:
                            merged = (old + " | " + note_part).strip(" |")
                            if note:
                                merged = (merged + " | " + note).strip(" |")
                            conn.execute(
                                "UPDATE journal_trades SET notes=? WHERE id=?",
                                (merged, tid),
                            )
                        journal_updated = True
        except (TypeError, ValueError):
            pass
        if not journal_updated:
            try:
                store.teach_lesson(
                    uid,
                    text=(
                        f"process {d.get('symbol')} tags={tags} {note or ''} "
                        f"money_truth={d.get('money_truth')}"
                    ).strip(),
                    tags=tags or ["process"],
                    needs_approval=False,
                    source="process",
                    kind="process",
                )
            except Exception as e:
                logger = __import__("logging").getLogger(__name__)
                logger.debug("teach_lesson process: %s", e)

    mt = d.get("money_truth")
    teach_ok = d.get("teach_ok") is True or mt == "exchange"
    tid_key = d.get("entity_key") or trade_id
    try:
        tid_int = int(trade_id)
    except (TypeError, ValueError):
        tid_int = abs(hash(str(tid_key))) % (10**9)

    # Never train exec edge on non-exchange $ PnL
    if not teach_ok:
        safe = dict(d)
        safe["pnl_pct"] = None
        safe["pnl_usd"] = None
        if tags:
            # process tags only — no PnL signal
            upd = beliefs().update_from_trade_close(
                uid, tid_int, dossier=safe, process_tags=tags
            )
        else:
            upd = {"updated": False, "reason": "not_exchange_verified"}
        return {
            "ok": True,
            "trade_id": trade_id,
            "entity_key": d.get("entity_key"),
            "money_truth": mt,
            "belief_update": upd,
        }

    upd = beliefs().update_from_trade_close(
        uid, tid_int, dossier=d, process_tags=tags
    )
    return {
        "ok": True,
        "trade_id": trade_id,
        "entity_key": d.get("entity_key"),
        "money_truth": mt or d.get("source"),
        "belief_update": upd,
    }


def teach(
    text: str,
    *,
    evidence_ids: Optional[List[int]] = None,
    tags: Optional[List[str]] = None,
    needs_approval: bool = False,
    user_id: Optional[int] = None,
    symbol: Optional[str] = None,
    market: Optional[str] = None,
    entity_key: Optional[str] = None,
    event_id: Optional[int] = None,
    behaviors: Optional[List[str]] = None,
    context_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Store a lesson, preferably bound to a trade or fire (holistic teaching)."""
    uid = int(user_id or uid_or_raise())
    body = (text or "").strip()
    if not body:
        return {"ok": False, "error": "empty"}

    from ..learning.integrity import ALLOWED_BEHAVIOR

    from ..learning.chip_honesty import sanitize_process_chips
    from ..learning.incident import build_incident, incident_tags
    from ..learning.symbols import (
        normalize_learning_symbol,
        rewrite_sym_tags,
    )

    # Process chips only — closed set (voice often invents free tags)
    allowed_beh = {b for b in ALLOWED_BEHAVIOR if b}
    # AD context (not process — setup quality)
    allowed_ad = {"ad_met", "ad_missed"}
    tag_list: List[str] = []
    beh_clean: List[str] = []
    ad_clean: List[str] = []
    from ..learning.buckets import ensure_bucket_in_chips_or_tags, normalize_bucket

    bucket_explicit: Optional[str] = None
    SETUP_PREFIXES = ("tf:", "regime:", "reds:", "vol:")
    for b in behaviors or []:
        s = str(b or "").strip().lower().replace(" ", "_")
        nb = normalize_bucket(s)
        if s in ("ad_take", "ad_press", "ad_wait", "ad_skip") or nb in (
            "ad_take",
            "ad_press",
            "ad_wait",
            "ad_skip",
        ):
            bucket_explicit = nb or s
            continue
        if s.startswith(SETUP_PREFIXES) and ":" in s:
            if s not in tag_list:
                tag_list.append(s)
            continue
        if s in allowed_beh and s not in beh_clean:
            beh_clean.append(s)
            tag_list.append(s)
        elif s in allowed_ad and s not in ad_clean:
            ad_clean.append(s)
            tag_list.append(s)
    # Keep only non-process free tags that look structured (sym: already added below)
    for t in tags or []:
        ts = str(t or "").strip()
        if not ts or ts in tag_list:
            continue
        low = ts.lower()
        if low.startswith("bucket:"):
            bucket_explicit = normalize_bucket(low.split(":", 1)[-1]) or bucket_explicit
            continue
        if low in ("ad_take", "ad_press", "ad_wait", "ad_skip"):
            bucket_explicit = normalize_bucket(low) or bucket_explicit
            continue
        if low in allowed_beh or low in allowed_ad:
            if low not in tag_list:
                tag_list.append(low)
            continue
        # drop invented free tags like skip_no_ad / panic_needs_ad_zone
        if ":" in ts:  # structured ok
            tag_list.append(ts)

    mkt = (market or "futures").lower() if market else None
    store = event_store()
    fire_price = None
    fire_ts = None
    ref_price = None
    drop_pct = None
    velocity_band = None
    heat_breadth = None
    if event_id:
        for e in store.recent_events(uid, limit=120):
            if int(e.get("id") or 0) == int(event_id):
                fire_price = e.get("price")
                fire_ts = e.get("ts")
                ref_price = e.get("ref_price")
                drop_pct = e.get("drop_pct")
                velocity_band = e.get("velocity_band")
                heat_breadth = e.get("heat_breadth")
                mkt = (market or e.get("market") or mkt or "futures").lower()
                if not symbol:
                    symbol = e.get("symbol")
                break

    if symbol:
        symbol = normalize_learning_symbol(str(symbol), mkt or "spot")
        tag_list.append(f"sym:{symbol}")
    if mkt:
        tag_list.append(f"mkt:{mkt}")
    if entity_key:
        tag_list.append(f"ek:{entity_key}")
    if context_type:
        tag_list.append(f"ctx:{context_type}")
    if event_id:
        tag_list.append(f"ev:{int(event_id)}")

    # Incident = fire/trade moment (not "now"), so multi-teaches on one coin stay distinct
    inc = build_incident(
        incident_ts=fire_ts,
        incident_price=fire_price,
        ref_price=ref_price,
        event_id=int(event_id) if event_id else None,
        trade_key=entity_key,
        drop_pct=drop_pct,
    )
    if fire_ts is None and context_type == "trade":
        # trade teach without fire: still stamp teach-time as weak anchor
        inc = build_incident(
            incident_ts=time.time(),
            incident_price=fire_price,
            trade_key=entity_key,
            drop_pct=drop_pct,
        )
    tag_list.extend(incident_tags(inc))
    # Chip honesty: closed set, no dual ad_met/ad_missed
    honest = sanitize_process_chips(beh_clean + ad_clean)
    tag_list = [t for t in tag_list if ":" in str(t)]
    tag_list.extend(honest)
    tag_list = rewrite_sym_tags(tag_list, mkt)
    if bucket_explicit:
        tag_list = ensure_bucket_in_chips_or_tags(
            tag_list, chips=honest, note=body, explicit=bucket_explicit
        )
    beh_clean = [c for c in honest if c not in allowed_ad]
    ad_clean = [c for c in honest if c in allowed_ad]

    # Prefix so recall always shows which trade/fire this is about
    about = ""
    if symbol:
        about = f"[{symbol}"
        if mkt:
            about += f" {mkt}"
        if context_type:
            about += f" · {context_type}"
        bits = honest
        if bits:
            about += f" · {','.join(bits)}"
        if inc.get("incident_ts"):
            about += f" · t={int(float(inc['incident_ts']))}"
        about += "] "
    full_text = about + body

    evid = list(evidence_ids or [])
    if event_id and int(event_id) not in evid:
        evid.append(int(event_id))

    lid = store.teach_lesson(
        uid,
        full_text,
        tags=tag_list,
        needs_approval=bool(needs_approval),
        source="owner",
        evidence_event_ids=evid,
    )

    # P1: freeze / update setup case with features + chips + note + incident
    case_view: Dict[str, Any] = {}
    if lid and symbol:
        try:
            from ..learning.cases import freeze_case

            case_view = freeze_case(
                store,
                uid,
                symbol=str(symbol),
                market=str(mkt or "futures"),
                event_id=int(event_id) if event_id else None,
                fire_ts=inc.get("incident_ts"),
                fire_price=inc.get("incident_price") or fire_price,
                ref_price=ref_price,
                drop_pct=drop_pct,
                velocity_band=velocity_band,
                heat_breadth=heat_breadth,
                chips=beh_clean
                + ad_clean
                + [
                    t
                    for t in tag_list
                    if str(t).startswith(("tf:", "regime:", "reds:", "vol:"))
                ],
                note=body,
                lesson_id=int(lid),
                trade_key=entity_key,
                source="teach",
                recompute=True,
            )
            if case_view.get("id"):
                # persist case: + bucket already on tags; append case id
                tag_list.append(f"case:{int(case_view['id'])}")
                try:
                    store.update_lesson(uid, int(lid), tags=tag_list)
                except Exception:
                    pass
        except Exception as e:
            logger.warning("p1 freeze on teach failed: %s", e)

    return {
        "ok": bool(lid),
        "lesson_id": lid,
        "symbol": symbol,
        "entity_key": entity_key,
        "event_id": event_id,
        "text": full_text,
        "chips": honest,
        "incident": inc,
        "case": case_view or None,
        "case_id": (case_view or {}).get("id"),
    }


def answer_question(
    question_id: int,
    *,
    answer_text: Optional[str] = None,
    action: Optional[str] = None,
    behavior: Optional[str] = None,
    dismiss: bool = False,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    uid = int(user_id or uid_or_raise())
    ok = event_store().answer_pending_question(
        uid,
        int(question_id),
        answer_text=answer_text,
        action=action,
        behavior=behavior,
        dismiss=dismiss,
    )
    return {"ok": ok, "question_id": question_id}


def on_trade_closed(user_id: int, trade_id: int) -> Dict[str, Any]:
    """After close: train exec edge only if exchange money-truth review exists."""
    store = event_store()
    from ..learning.money_truth import get_money_review, list_money_reviews

    d = get_money_review(user_id, trade_id, store=store)
    if not d or not d.get("teach_ok"):
        # Prefer latest exchange closed for symbol if journal id is stale
        try:
            j = get_trade_dossier(store, user_id, int(trade_id))
        except Exception:
            j = None
        if j and j.get("symbol"):
            for r in list_money_reviews(
                user_id, closed_only=True, teach_only=True, limit=20, store=store
            ):
                if str(r.get("symbol") or "").upper().replace("_", "") == str(
                    j.get("symbol") or ""
                ).upper().replace("_", ""):
                    d = r
                    break
        if not d or not d.get("teach_ok"):
            return {
                "ok": False,
                "reason": "no_exchange_verified_close",
                "hint": "Use list_trade_reviews teach_only for money truth",
            }
    tid = abs(hash(str(d.get("entity_key") or trade_id))) % (10**9)
    return beliefs().update_from_trade_close(user_id, tid, dossier=d)


# Back-compat aliases used by older routes
def learning_bundle(user_id: Optional[int] = None) -> Dict[str, Any]:
    return agent_bundle(user_id)


def trades_api(**kwargs):
    """Trade reviews for teaching — exchange money truth (same as Positions)."""
    uid = int(kwargs.get("user_id") or uid_or_raise())
    from ..learning.money_truth import list_money_reviews

    # Default teach_only=True so agents don't pull journal $ by accident
    teach_only = kwargs.get("teach_only")
    if teach_only is None:
        teach_only = True
    return {
        "trades": list_money_reviews(
            uid,
            closed_only=bool(kwargs.get("closed_only")),
            open_only=bool(kwargs.get("open_only")),
            symbol=kwargs.get("symbol"),
            limit=int(kwargs.get("limit") or 30),
            teach_only=bool(teach_only),
            store=event_store(),
        ),
        "money_truth": "exchange_when_available",
        "teach_only": bool(teach_only),
    }


def trade_api(trade_id: Any, user_id: Optional[int] = None):
    uid = int(user_id or uid_or_raise())
    from ..learning.money_truth import get_money_review

    d = get_money_review(uid, trade_id, store=event_store())
    if not d:
        try:
            d = get_trade_dossier(event_store(), uid, int(trade_id))
            if d:
                d = dict(d)
                d["money_truth"] = "journal_manual"
                d["teach_ok"] = False
                d["verified"] = False
                d["pnl_pct"] = None  # do not expose journal PnL as teachable
                d["pnl_usd"] = None
        except (TypeError, ValueError):
            d = None
    if not d:
        raise ValueError("Trade not found")
    return {"trade": d}


def tag_trade(trade_id: Any, **kwargs):
    tags = []
    beh = kwargs.get("behavior")
    notes = kwargs.get("notes")
    if beh:
        tags.append(beh)
    if not tags and not (notes or "").strip():
        raise ValueError("Provide behavior and/or notes to tag")
    return record_process(
        trade_id,
        tags=tags,
        note=notes,
        user_id=kwargs.get("user_id"),
    )


def ticker_api(symbol: str, market: Optional[str] = None, user_id: Optional[int] = None):
    return belief_ticker(symbol, market=market, user_id=user_id)


def approve_draft(lesson_id: int, **kwargs):
    uid = int(kwargs.get("user_id") or uid_or_raise())
    ok = event_store().approve_lesson(
        uid, int(lesson_id), dismiss=bool(kwargs.get("dismiss"))
    )
    return {"ok": ok}


def delete_lesson(lesson_id: int, *, user_id: Optional[int] = None) -> Dict[str, Any]:
    """Remove a durable lesson so the agent no longer recalls it."""
    uid = int(user_id or uid_or_raise())
    ok = event_store().delete_lesson(uid, int(lesson_id))
    return {"ok": ok, "lesson_id": int(lesson_id), "deleted": bool(ok)}


def update_lesson(
    lesson_id: int,
    *,
    text: Optional[str] = None,
    tags: Optional[List[str]] = None,
    behaviors: Optional[List[str]] = None,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Manual edit of any lesson on the desk (text + process/AD + bucket chips)."""
    uid = int(user_id or uid_or_raise())
    store = event_store()
    existing = store.get_lesson(uid, int(lesson_id))
    if not existing:
        return {"ok": False, "error": "not_found"}

    from ..learning.buckets import (
        CASE_BUCKETS,
        ensure_bucket_in_chips_or_tags,
        normalize_bucket,
    )
    from ..learning.chip_honesty import sanitize_process_chips
    from ..learning.integrity import ALLOWED_BEHAVIOR
    from ..learning.symbols import rewrite_sym_tags

    allowed_beh = {b for b in ALLOWED_BEHAVIOR if b}
    allowed_ad = {"ad_met", "ad_missed"}
    allowed_bucket = set(CASE_BUCKETS)

    old_tags: List[str] = []
    try:
        old_tags = json.loads(existing.get("tags_json") or "[]")
    except Exception:
        old_tags = []

    # Keep structured tags; drop free chips and old bucket: (re-stamped below)
    structured = []
    for t in old_tags:
        if not isinstance(t, str) or ":" not in t:
            continue
        low = t.lower()
        key = low.split(":", 1)[0]
        if key == "bucket":
            continue
        if key in allowed_beh or key in allowed_ad:
            continue
        structured.append(t)

    def _apply_bucket(tag_list: List[str], bucket_ex: Optional[str], chips: List[str]) -> List[str]:
        """Stamp or clear bucket: from explicit user selection."""
        # strip any bucket left
        out = [t for t in tag_list if not str(t).lower().startswith("bucket:")]
        if bucket_ex:
            out = ensure_bucket_in_chips_or_tags(
                out, chips=chips, note=text or existing.get("text"), explicit=bucket_ex
            )
        return out

    def _split_behaviors(
        raw: Optional[List[str]],
    ) -> tuple[List[str], Optional[str], List[str]]:
        process: List[str] = []
        setup: List[str] = []
        bucket_ex: Optional[str] = None
        for b in raw or []:
            s = str(b or "").strip().lower().replace(" ", "_")
            if not s:
                continue
            nb = normalize_bucket(s)
            if s in allowed_bucket or (nb and nb in allowed_bucket):
                bucket_ex = nb or s
                continue
            if s.startswith("bucket:"):
                bucket_ex = normalize_bucket(s.split(":", 1)[-1])
                continue
            if s.startswith(("tf:", "regime:", "reds:", "vol:")):
                setup.append(s)
                continue
            process.append(s)
        return process, bucket_ex, setup

    if tags is not None:
        tag_list: List[str] = []
        for t in tags:
            ts = str(t or "").strip()
            if not ts:
                continue
            if ts not in tag_list:
                tag_list.append(ts)
        for s in structured:
            if s not in tag_list:
                tag_list.append(s)
        free = [x for x in tag_list if ":" not in x]
        process, bucket_ex, setup = _split_behaviors(free + [x for x in tag_list if ":" in x])
        # also read bucket: from provided tags
        for t in tag_list:
            if str(t).lower().startswith("bucket:"):
                bucket_ex = normalize_bucket(str(t).split(":", 1)[-1]) or bucket_ex
        honest = sanitize_process_chips(process)
        tag_list = [t for t in tag_list if ":" in str(t) and not str(t).lower().startswith("bucket:")]
        for s in structured:
            if s not in tag_list:
                tag_list.append(s)
        for s in setup:
            if s not in tag_list:
                tag_list.append(s)
        tag_list.extend(honest)
        tag_list = rewrite_sym_tags(tag_list)
        tag_list = _apply_bucket(tag_list, bucket_ex, honest)
    elif behaviors is not None:
        # Manual desk edit: user selection wins (do not re-apply OWNER_LESSON_CHIPS)
        process, bucket_ex, setup = _split_behaviors(behaviors)
        drop_pref = ("tf:", "regime:", "reds:", "vol:")
        kept = [
            t
            for t in structured
            if not str(t).lower().startswith(drop_pref)
        ]
        tag_list = kept + setup + sanitize_process_chips(process)
        honest = sanitize_process_chips(process)
        tag_list = rewrite_sym_tags(tag_list)
        tag_list = _apply_bucket(tag_list, bucket_ex, honest)
    else:
        tag_list = None  # leave tags unchanged

    body = text
    if body is not None:
        body = str(body).strip()
        if not body:
            return {"ok": False, "error": "empty"}

    try:
        row = store.update_lesson(
            uid,
            int(lesson_id),
            text=body,
            tags=tag_list,
        )
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    if not row:
        return {"ok": False, "error": "update_failed"}

    # Keep linked setup case note/chips/bucket in sync when edited
    if body is not None or tag_list is not None:
        try:
            final_tags = tag_list if tag_list is not None else old_tags
            chips = [
                x
                for x in final_tags
                if isinstance(x, str)
                and ":" not in x
                and (x in allowed_beh or x in allowed_ad)
            ]
            bucket_val = None
            for x in final_tags:
                if isinstance(x, str) and x.lower().startswith("bucket:"):
                    bucket_val = normalize_bucket(x.split(":", 1)[-1])
            note = body if body is not None else None
            conn = store._get_conn()  # type: ignore[attr-defined]
            with store._lock:  # type: ignore[attr-defined]
                if note is not None:
                    conn.execute(
                        "UPDATE agent_setup_cases SET note = ?, chips_json = ?, "
                        "source = 'edit' WHERE user_id = ? AND lesson_id = ?",
                        (note, json.dumps(chips), uid, int(lesson_id)),
                    )
                else:
                    conn.execute(
                        "UPDATE agent_setup_cases SET chips_json = ?, source = 'edit' "
                        "WHERE user_id = ? AND lesson_id = ?",
                        (json.dumps(chips), uid, int(lesson_id)),
                    )
                # Stamp features.bucket on linked cases
                if bucket_val is not None:
                    rows = conn.execute(
                        "SELECT id, features_json FROM agent_setup_cases "
                        "WHERE user_id = ? AND lesson_id = ?",
                        (uid, int(lesson_id)),
                    ).fetchall()
                    for cr in rows:
                        try:
                            feats = json.loads(cr["features_json"] or "{}")
                        except Exception:
                            feats = {}
                        if not isinstance(feats, dict):
                            feats = {}
                        feats["bucket"] = bucket_val
                        conn.execute(
                            "UPDATE agent_setup_cases SET features_json = ? WHERE id = ?",
                            (json.dumps(feats), int(cr["id"])),
                        )
        except Exception as e:
            logger.debug("case note sync on lesson edit: %s", e)

    # Return enriched lesson so UI can show bucket without full reload races
    try:
        from ..learning.incident import enrich_lesson_row

        row = enrich_lesson_row(dict(row))
    except Exception:
        pass
    try:
        from ..learning.cases import stamp_lessons_with_cases

        stamped = stamp_lessons_with_cases(store, uid, [row] if row else [])
        if stamped:
            row = stamped[0]
    except Exception:
        pass
    return {
        "ok": True,
        "lesson": row,
        "lesson_id": int(lesson_id),
        "bucket": (row or {}).get("bucket") if isinstance(row, dict) else None,
    }
