"""AD Machine constants, seed names, locked teaches. No live-order flags here."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple

FEATURE_AD_MACHINE = "FEATURE_AD_MACHINE"

# Account model (paper / simulated book only)
EQUITY_USD = 200.0
MAX_PER_PLAY_USD = 100.0
MAX_LIVE_PLAYS = 2
LEVERAGE = 1.0  # 1x only

DEFAULT_LAYER_COUNT = 5
DEFAULT_REDS_REQUIRED = 3
PANIC_BREADTH_MIN = 3  # board-wide exception to first-candle sit-out

MANILA_TZ = "Asia/Manila"

# Slow → fast rank (higher = slower). Used only for two-TF ties.
TF_SLOW_RANK: Dict[str, int] = {
    "1w": 90,
    "1d": 80,
    "12h": 70,
    "8h": 60,
    "4h": 50,
    "1h": 40,
    "15m": 30,
    "5m": 20,
    "1m": 10,
}

# Failed-AD / bounce window scales with TF (lower TF = less time).
TF_BOUNCE_SECONDS: Dict[str, int] = {
    "1m": 5 * 60,
    "5m": 20 * 60,
    "15m": 45 * 60,
    "1h": 3 * 3600,
    "4h": 12 * 3600,
    "8h": 18 * 3600,
    "12h": 24 * 3600,
    "1d": 3 * 86400,
    "1w": 7 * 86400,
}

# Week-1 seed book. Plans only — not Kenneth's live Positions book.
SEED_NAMES: Tuple[Dict[str, str], ...] = (
    {"symbol": "USUSDT", "market": "spot", "display": "US"},
    {"symbol": "BPUSDT", "market": "spot", "display": "BP"},
    {"symbol": "AXTISTOCK_USDT", "market": "futures", "display": "AXTI"},
    {"symbol": "MRNASTOCK_USDT", "market": "futures", "display": "MRNA"},
    {"symbol": "ANSEMUSDT", "market": "spot", "display": "ANSEM"},
    {"symbol": "PUMPUSDT", "market": "spot", "display": "PUMP"},
)

# Owner-locked teaches. Prices only — bar times stay unknown unless a
# named official MEXC kline matches. Do not invent ticks.
LOCKED_TEACHES: Dict[Tuple[str, str], Dict[str, Any]] = {
    ("ANSEMUSDT", "spot"): {
        "ad_top": 0.356,
        "ad_bottom": 0.145,
        "zones": [0.200, 0.185, 0.145],
        "source": "locked_teach",
        "note": "ANSEM 0.356→0.145/0.185/0.200",
        "tf": None,
    },
    ("AXTISTOCK_USDT", "futures"): {
        "ad_top": 97.97,
        "ad_bottom": 65.58,
        "initial_drop_top": 113.49,
        "initial_drop_bottom": 81.10,
        "source": "locked_teach",
        "note": "AXTI 97.97→65.58 from 113.49→81.10",
        "tf": "4h",
    },
}

NEWS_KILL_CLASSES = frozenset({"DELIST", "SCAM", "HACK", "CLOSURE"})

MACHINE_TABLES = (
    "machine_plans",
    "machine_orders",
    "machine_closes",
    "machine_kb",
    "machine_needs_you",
)

# Tables the machine must never INSERT/UPDATE/DELETE.
FORBIDDEN_WRITE_TABLES = frozenset(
    {
        "alerts",
        "mover_settings",
        "mover_watchlist",
        "mover_sets",
        "learning_events",
        "learning_labels",
        "learning_outcomes",
        "learning_lessons",
        "learning_pending_questions",
        "journal_trades",
        "journal_fills",
        "agent_setup_cases",
        "position_flags",
        "news_events",
    }
)


def feature_ad_machine() -> bool:
    raw = os.getenv(FEATURE_AD_MACHINE)
    if raw is None:
        return False
    return raw.strip().lower() in ("1", "true", "yes", "on")


def machine_user_id() -> int:
    """Desk owner id only. Do not infer from alerts / positions rows."""
    env = (
        os.getenv("DESK_USER_ID") or os.getenv("MEXC_PRIVATE_TELEGRAM_USER_ID") or ""
    ).strip()
    if env.isdigit():
        return int(env)
    return 8630949601


def seed_key(row: Dict[str, str]) -> Tuple[str, str]:
    return (str(row["symbol"]).upper(), str(row["market"]).lower())


def is_seed(symbol: str, market: str) -> bool:
    key = (str(symbol).upper(), str(market).lower())
    return any(seed_key(s) == key for s in SEED_NAMES)


def bounce_seconds(tf: str | None) -> int:
    if not tf:
        return TF_BOUNCE_SECONDS["15m"]
    return int(TF_BOUNCE_SECONDS.get(str(tf).strip(), TF_BOUNCE_SECONDS["15m"]))


def tf_slow_rank(tf: str | None) -> int:
    if not tf:
        return 0
    return int(TF_SLOW_RANK.get(str(tf).strip(), 0))


def fmt_unknown(value: Any, fallback: str = "unknown") -> Any:
    if value is None or value == "":
        return fallback
    return value
