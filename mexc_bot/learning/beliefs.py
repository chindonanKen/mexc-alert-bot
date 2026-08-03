"""AD Super-Agent beliefs: setup/ticker edges updated from outcomes + process.

This is the real learning loop — not tag theater.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from .store import EventStore

logger = logging.getLogger(__name__)

N_PSEUDO = 4.0  # Bayesian prior strength (2 good / 2 bad)


def heat_bin(heat_breadth: Optional[int]) -> str:
    h = int(heat_breadth or 0)
    if h <= 1:
        return "isolated"
    if h <= 4:
        return "mild"
    return "broad"


def drop_bin(drop_pct: Optional[float]) -> str:
    d = abs(float(drop_pct or 0.0))
    if d < 5:
        return "mild"
    if d < 12:
        return "std"
    return "deep"


def band_norm(band: Optional[str]) -> str:
    b = (band or "GRIND").upper()
    if b not in ("PANIC", "FAST", "GRIND"):
        return "GRIND"
    return b


def outcome_label(
    max_bounce_pct: Optional[float],
    max_dd_pct: Optional[float],
    *,
    bounce_good: float = 1.5,
    dd_bad: float = 4.0,
) -> str:
    """good | bad | flat from path after fire."""
    bounce = float(max_bounce_pct or 0.0)
    dd = float(max_dd_pct or 0.0)  # usually negative
    if bounce >= bounce_good and dd > -dd_bad:
        return "good"
    if dd <= -dd_bad and bounce < bounce_good:
        return "bad"
    if bounce >= bounce_good * 0.5:
        return "flat"
    if dd <= -dd_bad * 0.6:
        return "bad"
    return "flat"


def edge_from_counts(n_good: int, n_bad: int, n: int) -> float:
    return (n_good - n_bad) / (n + N_PSEUDO)


class BeliefEngine:
    """Persist and update agent beliefs; judge new fires."""

    def __init__(self, store: EventStore):
        self.store = store
        self._ensure_schema()

    def _conn(self):
        return self.store._get_conn()

    def _ensure_schema(self) -> None:
        with self.store._lock:
            conn = self._conn()
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS belief_ticker (
                    user_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    market TEXT NOT NULL,
                    n_fires INTEGER DEFAULT 0,
                    n_setup_good INTEGER DEFAULT 0,
                    n_setup_bad INTEGER DEFAULT 0,
                    n_took INTEGER DEFAULT 0,
                    n_skip INTEGER DEFAULT 0,
                    setup_edge REAL DEFAULT 0.0,
                    exec_edge REAL DEFAULT 0.0,
                    median_hold_s REAL,
                    last_updated REAL NOT NULL,
                    PRIMARY KEY (user_id, symbol, market)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS belief_setup (
                    user_id INTEGER NOT NULL,
                    velocity_band TEXT NOT NULL,
                    heat_bin TEXT NOT NULL,
                    drop_bin TEXT NOT NULL,
                    n INTEGER DEFAULT 0,
                    n_good INTEGER DEFAULT 0,
                    n_bad INTEGER DEFAULT 0,
                    n_flat INTEGER DEFAULT 0,
                    edge REAL DEFAULT 0.0,
                    bounce_sum REAL DEFAULT 0,
                    bounce_n INTEGER DEFAULT 0,
                    last_updated REAL NOT NULL,
                    PRIMARY KEY (user_id, velocity_band, heat_bin, drop_bin)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS belief_updates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    kind TEXT,
                    event_id INTEGER,
                    trade_id INTEGER,
                    setup_key TEXT,
                    ticker_key TEXT,
                    label TEXT,
                    delta_edge REAL,
                    reasons_json TEXT,
                    ts REAL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_cases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    event_id INTEGER,
                    symbol TEXT,
                    market TEXT,
                    status TEXT NOT NULL DEFAULT 'open',
                    judgment_json TEXT,
                    outcome_label TEXT,
                    exec_score REAL,
                    weight_delta_json TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_cases_user "
                "ON agent_cases (user_id, status, created_at DESC)"
            )

    def update_from_outcome(
        self,
        user_id: int,
        event_id: int,
        *,
        max_bounce_pct: Optional[float],
        max_dd_pct: Optional[float],
        horizon_seconds: int = 900,
    ) -> Optional[str]:
        """Update setup + ticker edges from outcome path. Returns label."""
        with self.store._lock:
            conn = self._conn()
            ev = conn.execute(
                "SELECT * FROM learning_events WHERE id = ? AND user_id = ?",
                (int(event_id), int(user_id)),
            ).fetchone()
            if not ev:
                return None
            # idempotent: one update per event+horizon for outcomes
            exists = conn.execute(
                """
                SELECT 1 FROM belief_updates
                WHERE event_id = ? AND kind = ? LIMIT 1
                """,
                (int(event_id), f"outcome_{horizon_seconds}"),
            ).fetchone()
            if exists:
                return None

            band = band_norm(ev["velocity_band"])
            hbin = heat_bin(ev["heat_breadth"])
            dbin = drop_bin(ev["drop_pct"])
            # tighten thresholds by horizon
            if horizon_seconds >= 3600:
                lab = outcome_label(
                    max_bounce_pct, max_dd_pct, bounce_good=3.0, dd_bad=6.0
                )
            else:
                lab = outcome_label(max_bounce_pct, max_dd_pct)

            now = time.time()
            row = conn.execute(
                """
                SELECT * FROM belief_setup
                WHERE user_id=? AND velocity_band=? AND heat_bin=? AND drop_bin=?
                """,
                (user_id, band, hbin, dbin),
            ).fetchone()
            if not row:
                conn.execute(
                    """
                    INSERT INTO belief_setup (
                        user_id, velocity_band, heat_bin, drop_bin,
                        n, n_good, n_bad, n_flat, edge, bounce_sum, bounce_n,
                        last_updated
                    ) VALUES (?,?,?,?,0,0,0,0,0,0,0,?)
                    """,
                    (user_id, band, hbin, dbin, now),
                )
                row = conn.execute(
                    """
                    SELECT * FROM belief_setup
                    WHERE user_id=? AND velocity_band=? AND heat_bin=? AND drop_bin=?
                    """,
                    (user_id, band, hbin, dbin),
                ).fetchone()

            n = int(row["n"]) + 1
            ng = int(row["n_good"]) + (1 if lab == "good" else 0)
            nb = int(row["n_bad"]) + (1 if lab == "bad" else 0)
            nf = int(row["n_flat"]) + (1 if lab == "flat" else 0)
            bsum = float(row["bounce_sum"] or 0) + float(max_bounce_pct or 0)
            bn = int(row["bounce_n"] or 0) + (1 if max_bounce_pct is not None else 0)
            edge = edge_from_counts(ng, nb, n)
            conn.execute(
                """
                UPDATE belief_setup SET n=?, n_good=?, n_bad=?, n_flat=?, edge=?,
                    bounce_sum=?, bounce_n=?, last_updated=?
                WHERE user_id=? AND velocity_band=? AND heat_bin=? AND drop_bin=?
                """,
                (n, ng, nb, nf, edge, bsum, bn, now, user_id, band, hbin, dbin),
            )

            # ticker
            sym, mkt = ev["symbol"], ev["market"]
            trow = conn.execute(
                """
                SELECT * FROM belief_ticker
                WHERE user_id=? AND symbol=? AND market=?
                """,
                (user_id, sym, mkt),
            ).fetchone()
            if not trow:
                conn.execute(
                    """
                    INSERT INTO belief_ticker (
                        user_id, symbol, market, n_fires, n_setup_good, n_setup_bad,
                        n_took, n_skip, setup_edge, exec_edge, last_updated
                    ) VALUES (?,?,?,0,0,0,0,0,0,0,?)
                    """,
                    (user_id, sym, mkt, now),
                )
                trow = conn.execute(
                    """
                    SELECT * FROM belief_ticker
                    WHERE user_id=? AND symbol=? AND market=?
                    """,
                    (user_id, sym, mkt),
                ).fetchone()
            tf = int(trow["n_fires"]) + 1
            tg = int(trow["n_setup_good"]) + (1 if lab == "good" else 0)
            tb = int(trow["n_setup_bad"]) + (1 if lab == "bad" else 0)
            tedge = edge_from_counts(tg, tb, tf)
            # took/skip from latest label
            act = conn.execute(
                """
                SELECT action FROM learning_labels
                WHERE event_id=? AND action IS NOT NULL
                ORDER BY ts DESC LIMIT 1
                """,
                (event_id,),
            ).fetchone()
            n_took = int(trow["n_took"])
            n_skip = int(trow["n_skip"])
            if act and act["action"] == "took":
                n_took += 1
            elif act and act["action"] == "skip":
                n_skip += 1
            conn.execute(
                """
                UPDATE belief_ticker SET n_fires=?, n_setup_good=?, n_setup_bad=?,
                    setup_edge=?, n_took=?, n_skip=?, last_updated=?
                WHERE user_id=? AND symbol=? AND market=?
                """,
                (tf, tg, tb, tedge, n_took, n_skip, now, user_id, sym, mkt),
            )
            conn.execute(
                """
                INSERT INTO belief_updates (
                    user_id, kind, event_id, setup_key, ticker_key, label,
                    delta_edge, reasons_json, ts
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    user_id,
                    f"outcome_{horizon_seconds}",
                    event_id,
                    f"{band}|{hbin}|{dbin}",
                    f"{mkt}:{sym}",
                    lab,
                    edge,
                    json.dumps(
                        {
                            "bounce": max_bounce_pct,
                            "dd": max_dd_pct,
                            "setup_edge": edge,
                            "ticker_edge": tedge,
                        }
                    ),
                    now,
                ),
            )
            # update open agent case
            conn.execute(
                """
                UPDATE agent_cases SET outcome_label=?, updated_at=?,
                    weight_delta_json=?, status=CASE WHEN status='open' THEN 'scored' ELSE status END
                WHERE user_id=? AND event_id=?
                """,
                (
                    lab,
                    now,
                    json.dumps({"setup_edge": edge, "ticker_setup_edge": tedge, "label": lab}),
                    user_id,
                    event_id,
                ),
            )
            logger.info(
                "belief.outcome event=%s label=%s setup_edge=%.3f ticker_edge=%.3f",
                event_id,
                lab,
                edge,
                tedge,
            )
            return lab

    def update_from_trade_close(
        self,
        user_id: int,
        trade_id: int,
        *,
        dossier: Dict[str, Any],
        process_tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Execution edge only — never train solely on sign(PnL)."""
        process_tags = process_tags or []
        notes = (dossier.get("notes") or "").lower()
        for code in (
            "plan_ok",
            "pride",
            "greed",
            "hesitant",
            "fomo",
            "rule_break",
            "false_panic",
            "process_skip",
        ):
            if f"[{code}]" in (dossier.get("notes") or "").lower() or code in notes:
                if code not in process_tags:
                    process_tags.append(code)

        scores: List[int] = []
        # entry timing vs linked fire
        linked = (dossier.get("linked_events") or [{}])[0]
        fire_px = linked.get("price")
        entry = dossier.get("entry_avg")
        if fire_px and entry and fire_px > 0:
            chase = (float(entry) - float(fire_px)) / float(fire_px) * 100.0
            if chase <= 0.5:
                scores.append(1)
            elif chase > 2.0:
                scores.append(-1)
            else:
                scores.append(0)
        # layers
        n_buys = int(dossier.get("n_buys") or 0)
        if n_buys >= 2:
            scores.append(1)
        elif n_buys == 1:
            scores.append(0)
        else:
            scores.append(0)
        # hold vs bad outcome
        # (lightweight)
        hold_h = dossier.get("hold_hours")
        pnl = dossier.get("pnl_pct")
        if hold_h is not None and hold_h > 6 and pnl is not None and pnl < -3:
            scores.append(-1)  # pride risk
        elif pnl is not None and pnl > 0 and n_buys >= 1:
            scores.append(1)
        else:
            scores.append(0)

        if scores:
            scores_s = sorted(scores)
            exec_score = float(scores_s[len(scores_s) // 2])
        else:
            exec_score = 0.0

        for tag in process_tags:
            if tag in ("rule_break", "fomo", "greed", "pride"):
                exec_score = min(exec_score, 0.0)
            if tag == "plan_ok":
                exec_score = max(exec_score, 1.0)

        sym = dossier.get("symbol")
        mkt = dossier.get("market") or "futures"
        if not sym:
            return {"exec_score": exec_score, "updated": False}

        now = time.time()
        with self.store._lock:
            conn = self._conn()
            exists = conn.execute(
                """
                SELECT 1 FROM belief_updates
                WHERE trade_id=? AND kind='trade_close' LIMIT 1
                """,
                (int(trade_id),),
            ).fetchone()
            if exists:
                return {"exec_score": exec_score, "updated": False, "dup": True}

            trow = conn.execute(
                """
                SELECT * FROM belief_ticker
                WHERE user_id=? AND symbol=? AND market=?
                """,
                (user_id, sym, mkt),
            ).fetchone()
            if not trow:
                conn.execute(
                    """
                    INSERT INTO belief_ticker (
                        user_id, symbol, market, n_fires, n_setup_good, n_setup_bad,
                        n_took, n_skip, setup_edge, exec_edge, last_updated
                    ) VALUES (?,?,?,0,0,0,0,0,0,0,?)
                    """,
                    (user_id, sym, mkt, now),
                )
                old_exec = 0.0
            else:
                old_exec = float(trow["exec_edge"] or 0.0)
            new_exec = 0.85 * old_exec + 0.15 * exec_score
            hold_s = dossier.get("hold_seconds")
            conn.execute(
                """
                UPDATE belief_ticker SET exec_edge=?, median_hold_s=COALESCE(?, median_hold_s),
                    last_updated=?
                WHERE user_id=? AND symbol=? AND market=?
                """,
                (new_exec, hold_s, now, user_id, sym, mkt),
            )
            conn.execute(
                """
                INSERT INTO belief_updates (
                    user_id, kind, trade_id, ticker_key, label, delta_edge,
                    reasons_json, ts
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    user_id,
                    "trade_close",
                    int(trade_id),
                    f"{mkt}:{sym}",
                    "exec",
                    new_exec - old_exec,
                    json.dumps(
                        {
                            "exec_score": exec_score,
                            "process_tags": process_tags,
                            "pnl_pct": pnl,
                            "scores": scores,
                        }
                    ),
                    now,
                ),
            )
            eid = dossier.get("primary_event_id")
            if eid:
                conn.execute(
                    """
                    UPDATE agent_cases SET exec_score=?, updated_at=?, status='closed'
                    WHERE user_id=? AND event_id=?
                    """,
                    (exec_score, now, user_id, int(eid)),
                )
        return {
            "exec_score": exec_score,
            "exec_edge": new_exec,
            "updated": True,
            "process_tags": process_tags,
        }

    def get_setup_belief(
        self, user_id: int, band: str, heat: str, drop: str
    ) -> Dict[str, Any]:
        with self.store._lock:
            row = self._conn().execute(
                """
                SELECT * FROM belief_setup
                WHERE user_id=? AND velocity_band=? AND heat_bin=? AND drop_bin=?
                """,
                (user_id, band, heat, drop),
            ).fetchone()
        if not row:
            return {
                "n": 0,
                "edge": None,
                "n_good": 0,
                "n_bad": 0,
                "thin": True,
                "median_bounce": None,
            }
        n = int(row["n"])
        med = None
        if int(row["bounce_n"] or 0) > 0:
            med = float(row["bounce_sum"]) / float(row["bounce_n"])
        return {
            "n": n,
            "edge": float(row["edge"]),
            "n_good": int(row["n_good"]),
            "n_bad": int(row["n_bad"]),
            "n_flat": int(row["n_flat"]),
            "thin": n < 5,
            "median_bounce": med,
            "velocity_band": band,
            "heat_bin": heat,
            "drop_bin": drop,
        }

    def get_ticker_belief(
        self, user_id: int, symbol: str, market: str
    ) -> Dict[str, Any]:
        with self.store._lock:
            row = self._conn().execute(
                """
                SELECT * FROM belief_ticker
                WHERE user_id=? AND symbol=? AND market=?
                """,
                (user_id, symbol, market),
            ).fetchone()
        if not row:
            return {
                "symbol": symbol,
                "market": market,
                "n_fires": 0,
                "setup_edge": None,
                "exec_edge": None,
                "thin": True,
            }
        nf = int(row["n_fires"])
        return {
            "symbol": symbol,
            "market": market,
            "n_fires": nf,
            "n_setup_good": int(row["n_setup_good"]),
            "n_setup_bad": int(row["n_setup_bad"]),
            "n_took": int(row["n_took"]),
            "n_skip": int(row["n_skip"]),
            "setup_edge": float(row["setup_edge"]),
            "exec_edge": float(row["exec_edge"]),
            "median_hold_s": row["median_hold_s"],
            "thin": nf < 3,
            "last_updated": row["last_updated"],
        }

    def list_setup_beliefs(self, user_id: int, limit: int = 20) -> List[dict]:
        with self.store._lock:
            rows = self._conn().execute(
                """
                SELECT * FROM belief_setup WHERE user_id=? AND n>0
                ORDER BY edge DESC LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_ticker_beliefs(self, user_id: int, limit: int = 20) -> List[dict]:
        with self.store._lock:
            rows = self._conn().execute(
                """
                SELECT * FROM belief_ticker WHERE user_id=? AND n_fires>0
                ORDER BY last_updated DESC LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def open_case(self, user_id: int, event: dict, judgment: dict) -> int:
        now = time.time()
        with self.store._lock:
            cur = self._conn().execute(
                """
                INSERT INTO agent_cases (
                    user_id, event_id, symbol, market, status, judgment_json,
                    created_at, updated_at
                ) VALUES (?,?,?,?, 'open', ?,?,?)
                """,
                (
                    user_id,
                    event.get("id"),
                    event.get("symbol"),
                    event.get("market"),
                    json.dumps(judgment),
                    now,
                    now,
                ),
            )
            return int(cur.lastrowid)

    def list_cases(
        self, user_id: int, *, status: Optional[str] = None, limit: int = 20
    ) -> List[dict]:
        with self.store._lock:
            if status:
                rows = self._conn().execute(
                    """
                    SELECT * FROM agent_cases WHERE user_id=? AND status=?
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (user_id, status, limit),
                ).fetchall()
            else:
                rows = self._conn().execute(
                    """
                    SELECT * FROM agent_cases WHERE user_id=?
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (user_id, limit),
                ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["judgment"] = json.loads(d.get("judgment_json") or "{}")
            except Exception:
                d["judgment"] = {}
            try:
                d["weight_delta"] = json.loads(d.get("weight_delta_json") or "null")
            except Exception:
                d["weight_delta"] = None
            out.append(d)
        return out

    def judge_fire(
        self,
        user_id: int,
        event: dict,
        *,
        chart_features: Optional[dict] = None,
    ) -> Dict[str, Any]:
        """Structured agent judgment — must cite real n/edge or thin-data."""
        band = band_norm(event.get("velocity_band"))
        hbin = heat_bin(event.get("heat_breadth"))
        dbin = drop_bin(event.get("drop_pct"))
        setup = self.get_setup_belief(user_id, band, hbin, dbin)
        ticker = self.get_ticker_belief(
            user_id, str(event.get("symbol") or ""), str(event.get("market") or "futures")
        )
        chart = chart_features or {}
        setup_prior = chart.get("setup_prior")

        # Fatal news: only ticker-specific delist/hack/scam/closure
        fatal_info: Dict[str, Any] = {"fatal": False, "hard_fatal": False, "hits": []}
        try:
            from .fatal_news import apply_fatal_to_verdict, lookup_fatal_for_ticker

            fatal_info = lookup_fatal_for_ticker(
                self.store, str(event.get("symbol") or "")
            )
        except Exception as e:
            logger.debug("fatal news lookup: %s", e)
            apply_fatal_to_verdict = None  # type: ignore

        # Hard rules
        rules = {
            "rule2_velocity": "caution" if band == "GRIND" else "ok",
            "rule2_heat": "caution" if hbin == "isolated" else "ok",
            "fatal_news": bool(fatal_info.get("hard_fatal")),
            "fatal_news_soft": bool(fatal_info.get("fatal") and not fatal_info.get("hard_fatal")),
        }

        edge = setup.get("edge")
        n = int(setup.get("n") or 0)
        t_edge = ticker.get("setup_edge")
        t_n = int(ticker.get("n_fires") or 0)

        # Verdict
        verdict = "take_scout"
        size_hint = "micro_scout"
        if band == "GRIND" or (hbin == "isolated" and dbin == "deep"):
            if t_edge is not None and float(t_edge) > 0.4 and t_n >= 10:
                verdict, size_hint = "take_scout", "micro_scout"
            else:
                verdict, size_hint = "no_trade", "none"
        elif band == "PANIC" and hbin == "broad" and edge is not None and float(edge) >= 0.25 and n >= 5:
            verdict, size_hint = "take_layers", "full_layers"
        elif band == "PANIC" and edge is not None and float(edge) < 0:
            verdict, size_hint = "no_trade", "none"
        elif band == "PANIC" and (edge is None or n < 5 or float(edge) < 0.25):
            verdict, size_hint = "take_scout", "micro_scout"
        elif band == "FAST":
            if edge is not None and float(edge) >= 0.2 and n >= 5:
                verdict, size_hint = "take_scout", "micro_scout"
            else:
                verdict, size_hint = "wait_deeper", "none"

        # Apply fatal ticker news last (overrides take)
        fatal_note = None
        try:
            from .fatal_news import apply_fatal_to_verdict

            applied = apply_fatal_to_verdict(verdict, size_hint, fatal_info)
            verdict = applied["verdict"]
            size_hint = applied["size_hint"]
            fatal_note = applied.get("note")
            if applied.get("overridden"):
                rules["fatal_news"] = True
        except Exception:
            pass

        cite = []
        if fatal_info.get("fatal"):
            prim = fatal_info.get("primary") or {}
            cite.append(
                f"FATAL NEWS [{prim.get('severity')}] {prim.get('class')}: "
                f"{(prim.get('title') or '')[:120]}"
            )
        if n > 0 and edge is not None:
            cite.append(
                f"{band}+{hbin}+{dbin}: n={n} edge={float(edge):+.2f} "
                f"good={setup.get('n_good')} bad={setup.get('n_bad')}"
                + (
                    f" med_bounce={setup['median_bounce']:.1f}%"
                    if setup.get("median_bounce") is not None
                    else ""
                )
            )
        else:
            cite.append(f"{band}+{hbin}+{dbin}: thin data (n={n}) — using AD rules")
        if t_n > 0 and t_edge is not None:
            cite.append(
                f"Ticker {event.get('symbol')}: setup_edge={float(t_edge):+.2f} "
                f"exec_edge={float(ticker.get('exec_edge') or 0):+.2f} n_fires={t_n} "
                f"took={ticker.get('n_took')} skip={ticker.get('n_skip')}"
            )
        if chart.get("ok") or chart.get("thesis"):
            cite.append(
                f"Chart: prior={chart.get('setup_prior')} zone={chart.get('ad_zone')} "
                f"vol={chart.get('vol_flag')} rsi={chart.get('rsi_now_5m')} "
                f"div_bull={chart.get('div_bull')} regime={chart.get('regime')} "
                f"bias={chart.get('bias')}"
            )
            thesis = chart.get("thesis") or ""
            if thesis:
                # first 2 lines of discretionary thesis
                for line in thesis.split("\n")[1:4]:
                    if line.strip():
                        cite.append(line.strip())

        conf = 0.35
        if n >= 5:
            conf += 0.25
        if t_n >= 3:
            conf += 0.15
        if edge is not None and t_edge is not None and (edge * t_edge) > 0:
            conf += 0.1
        if chart.get("ok") and chart.get("setup_prior"):
            conf += 0.1 * float(chart["setup_prior"])
        conf = min(0.95, conf)

        needs_human = bool(setup.get("thin") or ticker.get("thin") or verdict == "wait_deeper")

        # Self-critique: agent argues against its own call
        critique: List[str] = []
        if setup.get("thin") or n < 5:
            critique.append(
                "Thin setup sample — this call is rule+chart heavy, not yet trained on your book."
            )
        if ticker.get("thin") or t_n < 3:
            critique.append(
                "Thin ticker history — chart personality not proven for you yet."
            )
        if band == "PANIC" and hbin == "isolated":
            critique.append(
                "PANIC but isolated heat — could be coin-specific wipeout, not market panic."
            )
        if band == "GRIND":
            critique.append(
                "GRIND dumps often trend; mean-reversion call would be against Rule 2."
            )
        if chart.get("vol_flag") == "dry":
            critique.append(
                "Dry volume on the dump — big move without participation is a quality hit."
            )
        if chart.get("ad_zone") == "shallow" or chart.get("ad_zone") == "shallow_of_typical_AD":
            critique.append(
                "Still shallow vs this chart's typical AD — bounce odds may improve deeper."
            )
        if chart.get("ad_zone") in ("deep_ext", "deep_extension") and verdict == "no_trade":
            critique.append(
                "Deep extension but no_trade — may be missing best panic prices if thesis is AD."
            )
        if edge is not None and float(edge) > 0.2 and verdict == "no_trade":
            critique.append(
                "Setup edge positive but verdict no_trade — hard rule overrode trained edge; challenge if chart disagrees."
            )
        if edge is not None and float(edge) < 0 and verdict in ("take_scout", "take_layers"):
            critique.append(
                "Taking while setup edge is negative — only justified if chart/extension is exceptional."
            )
        if t_edge is not None and float(ticker.get("exec_edge") or 0) < -0.15:
            critique.append(
                "Your exec_edge on this ticker is weak — even good setups may be mis-traded by you here."
            )
        if fatal_info.get("hard_fatal"):
            critique.append(
                "HARD fatal news on this ticker — mean-reversion AD is wrong tool; capital preservation first."
            )
        elif fatal_info.get("fatal"):
            critique.append(
                "Unconfirmed fatal-class headline matched this ticker — treat dump as possibly structural, not panic liquidity."
            )
        if fatal_note:
            critique.append(fatal_note)
        if not critique:
            critique.append(
                "No major self-conflict detected; still verify invalidation and powder plan."
            )

        # chart fields for UI/voice (include thesis if present)
        chart_out = {
            k: chart.get(k)
            for k in (
                "setup_prior",
                "ad_zone",
                "ad_depth_ratio",
                "vol_flag",
                "rsi_now_5m",
                "div_bull",
                "sharp_score",
                "ok",
                "thesis",
                "bias",
                "regime",
                "pace",
            )
            if chart
        }

        judgment = {
            "event_id": event.get("id"),
            "symbol": event.get("symbol"),
            "market": event.get("market"),
            "features": {
                "velocity_band": band,
                "heat_bin": hbin,
                "drop_bin": dbin,
                "drop_pct": event.get("drop_pct"),
                "price": event.get("price"),
                "ts": event.get("ts"),
            },
            "setup": {
                "edge": edge,
                "n": n,
                "p_good": (
                    (setup.get("n_good") or 0) / n if n else None
                ),
                "median_bounce_hist": setup.get("median_bounce"),
                "verdict": verdict,
                "thin": setup.get("thin"),
            },
            "ticker": ticker,
            "chart": chart_out,
            "rules": rules,
            "size_hint": size_hint,
            "cite": cite,
            "self_critique": critique,
            "fatal_news": fatal_info,
            "confidence": round(conf, 3),
            "needs_human": needs_human or len(critique) >= 2 or bool(fatal_info.get("fatal")),
            "human_override": None,
            "agent": "AD-SuperAgent-v1",
        }
        return judgment

    def apply_human_correction(
        self,
        user_id: int,
        *,
        event_id: Optional[int] = None,
        case_id: Optional[int] = None,
        correct_verdict: str,
        reason: str,
        adjust_beliefs: bool = True,
    ) -> Dict[str, Any]:
        """Owner corrects agent interpretation — stored and optionally nudges edges."""
        allowed = {
            "no_trade",
            "take_scout",
            "take_layers",
            "wait_deeper",
        }
        cv = (correct_verdict or "").strip().lower()
        if cv not in allowed:
            raise ValueError(
                f"correct_verdict must be one of {sorted(allowed)}"
            )
        reason = (reason or "").strip()
        if not reason:
            raise ValueError("reason required — teach the agent why")

        with self.store._lock:
            conn = self._conn()
            case = None
            if case_id:
                case = conn.execute(
                    "SELECT * FROM agent_cases WHERE id=? AND user_id=?",
                    (int(case_id), int(user_id)),
                ).fetchone()
            elif event_id:
                case = conn.execute(
                    """
                    SELECT * FROM agent_cases WHERE user_id=? AND event_id=?
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (int(user_id), int(event_id)),
                ).fetchone()
            if not case:
                raise ValueError("No agent case found for correction")

            try:
                judgment = json.loads(case["judgment_json"] or "{}")
            except Exception:
                judgment = {}
            old_verdict = (judgment.get("setup") or {}).get("verdict")
            judgment["human_override"] = {
                "verdict": cv,
                "reason": reason,
                "previous_verdict": old_verdict,
                "ts": time.time(),
            }
            if "setup" not in judgment:
                judgment["setup"] = {}
            judgment["setup"]["verdict"] = cv
            judgment["setup"]["verdict_source"] = "human_correction"
            judgment["self_critique"] = list(judgment.get("self_critique") or []) + [
                f"HUMAN CORRECTION: was {old_verdict} → {cv}. Why: {reason}"
            ]
            # map size
            size_map = {
                "no_trade": "none",
                "wait_deeper": "none",
                "take_scout": "micro_scout",
                "take_layers": "full_layers",
            }
            judgment["size_hint"] = size_map.get(cv, judgment.get("size_hint"))
            judgment["needs_human"] = False

            now = time.time()
            conn.execute(
                """
                UPDATE agent_cases SET judgment_json=?, status='corrected', updated_at=?
                WHERE id=?
                """,
                (json.dumps(judgment), now, int(case["id"])),
            )

            # Persist as lesson with evidence
            try:
                self.store.teach_lesson(
                    int(user_id),
                    f"Correction on {case['symbol']}: {old_verdict}→{cv}. {reason}",
                    tags=["human_correction", cv],
                    needs_approval=False,
                    source="owner",
                    evidence_event_ids=[int(case["event_id"])]
                    if case["event_id"]
                    else [],
                )
            except Exception:
                pass

            nudged = None
            if adjust_beliefs and case["event_id"]:
                ev = conn.execute(
                    "SELECT * FROM learning_events WHERE id=?",
                    (int(case["event_id"]),),
                ).fetchone()
                if ev:
                    band = band_norm(ev["velocity_band"])
                    hbin = heat_bin(ev["heat_breadth"])
                    dbin = drop_bin(ev["drop_pct"])
                    # light Bayesian nudge: human says take_layers/scout → count as soft good prior
                    # human says no_trade/wait → soft bad if agent wanted take
                    delta_good = 1 if cv in ("take_scout", "take_layers") else 0
                    delta_bad = 1 if cv in ("no_trade", "wait_deeper") else 0
                    if old_verdict in ("take_scout", "take_layers") and cv in (
                        "no_trade",
                        "wait_deeper",
                    ):
                        delta_bad = 1
                        delta_good = 0
                    elif old_verdict in ("no_trade", "wait_deeper") and cv in (
                        "take_scout",
                        "take_layers",
                    ):
                        delta_good = 1
                        delta_bad = 0
                    row = conn.execute(
                        """
                        SELECT * FROM belief_setup
                        WHERE user_id=? AND velocity_band=? AND heat_bin=? AND drop_bin=?
                        """,
                        (user_id, band, hbin, dbin),
                    ).fetchone()
                    if not row:
                        conn.execute(
                            """
                            INSERT INTO belief_setup (
                                user_id, velocity_band, heat_bin, drop_bin,
                                n, n_good, n_bad, n_flat, edge, bounce_sum, bounce_n,
                                last_updated
                            ) VALUES (?,?,?,?,0,0,0,0,0,0,0,?)
                            """,
                            (user_id, band, hbin, dbin, now),
                        )
                        row = conn.execute(
                            """
                            SELECT * FROM belief_setup
                            WHERE user_id=? AND velocity_band=? AND heat_bin=? AND drop_bin=?
                            """,
                            (user_id, band, hbin, dbin),
                        ).fetchone()
                    n = int(row["n"]) + 1
                    ng = int(row["n_good"]) + delta_good
                    nb = int(row["n_bad"]) + delta_bad
                    edge = edge_from_counts(ng, nb, n)
                    conn.execute(
                        """
                        UPDATE belief_setup SET n=?, n_good=?, n_bad=?, edge=?, last_updated=?
                        WHERE user_id=? AND velocity_band=? AND heat_bin=? AND drop_bin=?
                        """,
                        (n, ng, nb, edge, now, user_id, band, hbin, dbin),
                    )
                    nudged = {
                        "setup_key": f"{band}|{hbin}|{dbin}",
                        "edge": edge,
                        "n": n,
                    }
                    conn.execute(
                        """
                        INSERT INTO belief_updates (
                            user_id, kind, event_id, setup_key, label, delta_edge,
                            reasons_json, ts
                        ) VALUES (?,?,?,?,?,?,?,?)
                        """,
                        (
                            user_id,
                            "human_correction",
                            int(case["event_id"]),
                            f"{band}|{hbin}|{dbin}",
                            cv,
                            edge,
                            json.dumps(
                                {
                                    "reason": reason,
                                    "old_verdict": old_verdict,
                                    "new_verdict": cv,
                                }
                            ),
                            now,
                        ),
                    )

        return {
            "ok": True,
            "case_id": int(case["id"]),
            "event_id": case["event_id"],
            "previous_verdict": old_verdict,
            "correct_verdict": cv,
            "reason": reason,
            "judgment": judgment,
            "belief_nudge": nudged,
        }
