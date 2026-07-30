#!/usr/bin/env python3
"""Seed local AD Desk SQLite with realistic dummy alarms, movers, memory, journal.

Safe-by-default for laptop iteration only:
  - Writes to ALERTS_FILE / data/alerts.db under the repo
  - Refuses paths that look like production droplet data unless --i-know-this-is-prod
  - Never prints or writes secrets (tokens, API keys)

Usage:
  python3 scripts/seed_desk_local.py
  python3 scripts/seed_desk_local.py --force   # wipe this user_id's seed tables first
  make desk-seed
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env", override=False)
    except Exception:
        pass


def _db_path() -> Path:
    raw = os.getenv("ALERTS_FILE", str(ROOT / "data" / "alerts.json"))
    p = Path(raw)
    if not p.is_absolute():
        p = ROOT / p
    if str(p).endswith(".json"):
        p = p.with_suffix(".db")
    return p


def _user_id() -> int:
    raw = os.getenv("DESK_USER_ID") or os.getenv("MEXC_PRIVATE_TELEGRAM_USER_ID") or "1"
    return int(str(raw).strip())


def _looks_like_prod(path: Path) -> bool:
    s = str(path.resolve()).lower()
    markers = (
        "mexc-alert-bot/data",
        "/var/lib",
        "/app/data",
        "production",
    )
    # Repo-local ./data is fine
    try:
        path.resolve().relative_to((ROOT / "data").resolve())
        return False
    except ValueError:
        pass
    return any(m in s for m in markers)


def _wipe_user(conn: sqlite3.Connection, uid: int) -> None:
    """Remove only this user's rows so re-seed is clean (local sandbox)."""
    tables_user = (
        "alerts",
        "mover_watchlist",
        "mover_settings",
        "learning_events",
        "learning_labels",
        "journal_trades",
        "journal_fills",
        "investigations",
    )
    for t in tables_user:
        try:
            conn.execute(f"DELETE FROM {t} WHERE user_id = ?", (uid,))
        except sqlite3.OperationalError:
            pass
    # labels may remain orphaned if events deleted first — already deleted by user_id
    try:
        conn.execute(
            "DELETE FROM learning_outcomes WHERE event_id NOT IN (SELECT id FROM learning_events)"
        )
    except sqlite3.OperationalError:
        pass


def seed(*, force: bool, allow_prod: bool) -> int:
    _load_dotenv()
    path = _db_path()
    uid = _user_id()

    if _looks_like_prod(path) and not allow_prod:
        print(
            f"REFUSED: {path} looks outside local repo data/.\n"
            "Local seed only. Pass --i-know-this-is-prod only if intentional.",
            file=sys.stderr,
        )
        return 2

    path.parent.mkdir(parents=True, exist_ok=True)

    from mexc_bot.investigators.store import InvestigatorStore
    from mexc_bot.learning.store import EventStore
    from mexc_bot.movers.storage import MoverStore
    from mexc_bot.storage import AlertStore

    alerts = AlertStore(path)
    movers = MoverStore(path)
    events = EventStore(path)
    inv = InvestigatorStore(path)

    # Empty? or force
    existing = alerts.get_user_alerts(uid)
    wl = movers.get_watchlist(uid)
    if (existing or wl) and not force:
        print(
            f"Already has data for user_id={uid} "
            f"(alerts={len(existing)}, watchlist={len(wl)}).\n"
            "Re-run with --force to replace this user's dummy set."
        )
        return 0

    if force:
        conn = sqlite3.connect(str(path))
        try:
            _wipe_user(conn, uid)
            conn.commit()
        finally:
            conn.close()
        # re-open stores after wipe (caches)
        alerts = AlertStore(path)
        movers = MoverStore(path)
        events = EventStore(path)
        inv = InvestigatorStore(path)

    now = time.time()

    # --- Target alerts (spot + futures) — "alarms" ---
    target_specs = [
        ("BTCUSDT", 95000.0, "spot"),
        ("ETHUSDT", 3200.0, "spot"),
        ("SOLUSDT", 140.0, "spot"),
        ("BTC_USDT", 94000.0, "futures"),
        ("ETH_USDT", 3100.0, "futures"),
        ("TSLAUSDT", 250.0, "futures"),
        ("SIRENUSDT", 0.42, "spot"),
        ("DOGEUSDT", 0.11, "spot"),
    ]
    for sym, px, mkt in target_specs:
        alerts.add_alert(uid, sym, px, market=mkt)
    # One disabled alarm for toggle UX
    visuals = alerts.get_user_alerts(uid)
    if len(visuals) >= 2:
        alerts.toggle_alert(uid, visuals[-1]["id"])  # disable last

    # --- Movers ---
    movers.set_params(uid, threshold_percent=5.0, lookback_seconds=900, default_enabled=True)
    movers.set_enabled(uid, True, default_threshold=5.0, default_lookback=900)
    watch = [
        {"symbol": "BTC_USDT", "market": "futures"},
        {"symbol": "ETH_USDT", "market": "futures"},
        {"symbol": "SOL_USDT", "market": "futures"},
        {"symbol": "TSLAUSDT", "market": "futures"},
        {"symbol": "DOGE_USDT", "market": "futures"},
        {"symbol": "XRP_USDT", "market": "futures"},
        {"symbol": "SIRENUSDT", "market": "spot"},
        {"symbol": "PEPEUSDT", "market": "spot"},
    ]
    movers.set_watchlist(uid, watch)

    # --- Learning / memory (recent fires) ---
    fire_specs = [
        {
            "source": "mover_peak",
            "symbol": "SOL_USDT",
            "market": "futures",
            "age_s": 400,
            "price": 138.2,
            "ref_price": 152.0,
            "drop_pct": -9.1,
            "velocity_band": "PANIC",
            "heat_breadth": 5,
            "mode": "peak",
            "label": "took",
            "bounce_quality": "good",
        },
        {
            "source": "mover_step",
            "symbol": "SOL_USDT",
            "market": "futures",
            "age_s": 220,
            "price": 131.0,
            "ref_price": 138.2,
            "drop_pct": -5.2,
            "velocity_band": "FAST",
            "heat_breadth": 4,
            "mode": "step",
            "label": "took",
            "bounce_quality": "ok",
        },
        {
            "source": "mover_peak",
            "symbol": "TSLAUSDT",
            "market": "futures",
            "age_s": 1800,
            "price": 241.5,
            "ref_price": 262.0,
            "drop_pct": -7.8,
            "velocity_band": "FAST",
            "heat_breadth": 1,
            "mode": "peak",
            "label": "skip",
            "bounce_quality": "poor",
        },
        {
            "source": "mover_peak",
            "symbol": "PEPEUSDT",
            "market": "spot",
            "age_s": 3600,
            "price": 0.0000081,
            "ref_price": 0.0000095,
            "drop_pct": -14.7,
            "velocity_band": "PANIC",
            "heat_breadth": 1,
            "mode": "peak",
            "label": "skip",
            "bounce_quality": "none",
        },
        {
            "source": "mover_peak",
            "symbol": "ETH_USDT",
            "market": "futures",
            "age_s": 90,
            "price": 3010.0,
            "ref_price": 3220.0,
            "drop_pct": -6.5,
            "velocity_band": "GRIND",
            "heat_breadth": 3,
            "mode": "peak",
            "label": None,
            "bounce_quality": None,
        },
        {
            "source": "target",
            "symbol": "BTCUSDT",
            "market": "spot",
            "age_s": 7200,
            "price": 95100.0,
            "ref_price": 95000.0,
            "drop_pct": None,
            "velocity_band": None,
            "heat_breadth": None,
            "mode": "cross",
            "label": "took",
            "bounce_quality": "ok",
        },
    ]

    event_ids: list[int] = []
    for spec in fire_specs:
        eid = events.log_event(
            uid,
            spec["source"],
            spec["symbol"],
            spec["market"],
            ts=now - float(spec["age_s"]),
            price=spec["price"],
            ref_price=spec["ref_price"],
            drop_pct=spec["drop_pct"],
            velocity_band=spec["velocity_band"],
            heat_breadth=spec["heat_breadth"],
            mode=spec["mode"],
            payload={"seed": True, "note": "local dummy fire"},
        )
        event_ids.append(eid)
        if spec.get("label") and eid:
            events.label_event(
                eid,
                uid,
                action=spec["label"],
                bounce_quality=spec.get("bounce_quality"),
                notes="seed dummy",
            )

    # Outcomes on older events
    for eid, horizon, bounce, dd in (
        (event_ids[0], 900, 4.2, -2.1),
        (event_ids[0], 3600, 6.8, -3.0),
        (event_ids[2], 900, 1.1, -5.5),
        (event_ids[3], 3600, 0.4, -12.0),
    ):
        if eid:
            try:
                events.record_outcome(
                    event_id=eid,
                    horizon_seconds=horizon,
                    max_bounce_pct=bounce,
                    max_dd_pct=dd,
                    last_price=None,
                )
            except Exception:
                # older API name variants
                try:
                    conn = sqlite3.connect(str(path))
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO learning_outcomes
                        (event_id, horizon_seconds, max_bounce_pct, max_dd_pct, last_price, computed_at)
                        VALUES (?, ?, ?, ?, NULL, ?)
                        """,
                        (eid, horizon, bounce, dd, now),
                    )
                    conn.commit()
                    conn.close()
                except Exception:
                    pass

    # --- Journal positions ---
    events.journal_open(
        uid,
        "SOL_USDT",
        "futures",
        entry_avg=132.5,
        notes="seed AD layer 1–3 · panic SOL",
    )
    events.journal_open(
        uid,
        "BTC_USDT",
        "futures",
        entry_avg=94800.0,
        notes="seed scout size only",
    )
    closed_id = events.journal_open(
        uid,
        "DOGE_USDT",
        "futures",
        entry_avg=0.118,
        notes="seed closed winner",
    )
    events.journal_close(uid, closed_id, exit_avg=0.124, notes="seed TP bounce")

    # --- Isolated / intel dummies ---
    inv.save_investigation(
        user_id=uid,
        event_id=event_ids[3] if event_ids else None,
        symbol="PEPEUSDT",
        market="spot",
        drop_pct=-14.7,
        velocity_band="PANIC",
        heat_breadth=1,
        verdict="ISOLATED_RISK",
        confidence=0.78,
        evidence=[
            {"source": "seed", "kind": "isolated", "note": "single-name thin heat"},
            {"source": "mexc_ann", "kind": "delist", "note": "dummy delist watch"},
        ],
    )
    inv.upsert_delist(
        exchange="mexc",
        base="ZZZSEED",
        title="Seed: ZZZSEED monitoring notice (dummy)",
        url="https://example.invalid/seed-delist",
        kind="watch",
        ts=now - 86400,
        fingerprint="seed-zzz-1",
        raw={"seed": True},
    )
    # Source expertise sample rows
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO source_expertise
            (source, kind, hits, confirmed_moves, false_alarms, bounce_sum, bounce_n, weight, updated_at)
            VALUES
            ('mexc_ann', 'delist', 12, 9, 2, 18.0, 8, 1.35, ?),
            ('twitter_rumor', 'hack', 20, 4, 11, 6.0, 10, 0.55, ?)
            """,
            (now, now),
        )
        conn.commit()
    finally:
        conn.close()

    # Optional news row if table exists
    try:
        conn = sqlite3.connect(str(path))
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS news_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                class TEXT,
                severity TEXT,
                title TEXT,
                url TEXT,
                source TEXT,
                source_trust TEXT,
                ts REAL,
                raw_json TEXT,
                fingerprint TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO news_events
            (symbol, class, severity, title, url, source, source_trust, ts, raw_json, fingerprint)
            VALUES
            ('PEPE', 'token', 'info', 'Seed headline: meme liquidity note (dummy)',
             'https://example.invalid/seed-news', 'seed', 'low', ?, '{}', 'seed-news-1'),
            (NULL, 'exchange', 'high', 'Seed: venue maintenance window (dummy)',
             'https://example.invalid/seed-maint', 'seed', 'med', ?, '{}', 'seed-news-2')
            """,
            (now - 2000, now - 5000),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

    n_alerts = len(alerts.get_user_alerts(uid))
    n_wl = len(movers.get_watchlist(uid))
    n_ev = len(events.recent_events(uid, limit=50))
    n_open = len(events.journal_list(uid, open_only=True))

    print("Seeded local AD Desk sandbox")
    print(f"  db:         {path}")
    print(f"  user_id:    {uid}")
    print(f"  alerts:     {n_alerts}")
    print(f"  watchlist:  {n_wl}")
    print(f"  fires:      {len(event_ids)}")
    print(f"  journal open: {n_open}")
    print("Open desk: make desk-dev  → Overview / Targets / Tape / Memory / Positions")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Seed local desk dummy data")
    ap.add_argument(
        "--force",
        action="store_true",
        help="Replace this DESK_USER_ID's alerts/movers/memory/journal",
    )
    ap.add_argument(
        "--i-know-this-is-prod",
        action="store_true",
        help="Override safety check for non-repo data paths (dangerous)",
    )
    args = ap.parse_args()
    return seed(force=args.force, allow_prod=args.i_know_this_is_prod)


if __name__ == "__main__":
    raise SystemExit(main())
