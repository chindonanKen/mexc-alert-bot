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

        # Hard rules
        rules = {
            "rule2_velocity": "caution" if band == "GRIND" else "ok",
            "rule2_heat": "caution" if hbin == "isolated" else "ok",
            "fatal_news": False,
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

        cite = []
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
        if chart.get("ok"):
            cite.append(
                f"Chart: prior={chart.get('setup_prior')} zone={chart.get('ad_zone')} "
                f"vol={chart.get('vol_flag')} rsi={chart.get('rsi_now_5m')} "
                f"div_bull={chart.get('div_bull')}"
            )

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
            "chart": {
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
                )
                if chart
            },
            "rules": rules,
            "size_hint": size_hint,
            "cite": cite,
            "confidence": round(conf, 3),
            "needs_human": needs_human,
            "agent": "AD-SuperAgent-v1",
        }
        return judgment
