"""Integrity helpers: ensure learning never corrupts target alerts; coach facts check.

Used by tests and optional /diag-style checks.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


# Sources the system is allowed to claim without external news (V1.0)
ALLOWED_EVENT_SOURCES = frozenset(
    {
        "mover_peak",
        "mover_step",
        "target",
        "manual",
        "heat",  # reserved; not logged by default
        "news",  # V1.1+
    }
)

ALLOWED_ACTIONS = frozenset(
    {"took", "skip", "watch", "partial", "late", None}
)
ALLOWED_BOUNCE = frozenset({"strong", "weak", "none", "failed", None})
ALLOWED_BEHAVIOR = frozenset(
    {
        "pride",
        "greed",
        "plan_ok",
        "false_panic",
        "hesitant",
        "fomo",
        "rule_break",
        "process_skip",
        None,
    }
)


def assert_alerts_table_intact(db_path: Path, expected_stable_ids: Optional[Set[int]] = None) -> dict:
    """Read alerts table; optionally verify stable ids still present."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, user_id, symbol, price, enabled, market FROM alerts"
        ).fetchall()
        ids = {int(r["id"]) for r in rows}
        if expected_stable_ids is not None and not expected_stable_ids.issubset(ids):
            missing = expected_stable_ids - ids
            raise AssertionError(f"alerts rows missing stable ids: {missing}")
        return {"alert_count": len(rows), "stable_ids": sorted(ids)}
    finally:
        conn.close()


def validate_event_row(row: Dict[str, Any]) -> List[str]:
    """Return list of integrity problems (empty = OK). Does not invent data."""
    problems: List[str] = []
    src = row.get("source")
    if src not in ALLOWED_EVENT_SOURCES:
        problems.append(f"unknown source: {src}")
    if not row.get("symbol"):
        problems.append("missing symbol")
    if row.get("market") not in ("spot", "futures"):
        problems.append(f"bad market: {row.get('market')}")
    price = row.get("price")
    if price is not None and float(price) <= 0:
        problems.append(f"non-positive price: {price}")
    band = row.get("velocity_band")
    if band is not None and band not in ("PANIC", "FAST", "GRIND", "—", ""):
        problems.append(f"unexpected velocity_band: {band}")
    return problems


def coach_must_not_claim_unlogged(
    reply: str,
    *,
    has_events: bool,
    has_stats: bool,
) -> List[str]:
    """
    Soft checks on coach text: if no events, must not claim specific fire ids
    or fake memory counts.
    """
    problems: List[str] = []
    lower = reply.lower()
    if not has_events:
        if "latest event: #" in lower:
            problems.append("coach cited event id without events")
        if "memory (from your log): events=" in lower and "events=0" not in lower:
            # format_coach_reply uses different empty path
            pass
    if not has_stats and "memory (from your log): events=" in lower:
        # Should use empty memory line instead
        if "events=0" not in lower:
            problems.append("coach claimed memory stats without stats dict")
    if "filled at" in lower or "your entry was" in lower:
        problems.append("coach invented fill language")
    if "confirmed delist" in lower or "confirmed hack" in lower:
        if "v1.1" not in lower and "until then" not in lower:
            problems.append("coach claimed confirmed news without news module")
    return problems
