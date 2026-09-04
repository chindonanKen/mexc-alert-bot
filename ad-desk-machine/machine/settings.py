"""Machine constants. Live exchange orders stay OFF — no env override."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

# Hard lock. Do not read DESK_ALLOW_LIVE_ORDERS. Do not send to MEXC.
LIVE_ORDERS_ALLOWED = False

EQUITY_USD = 200.0
MAX_PER_PLAY_USD = 100.0
MAX_LIVE_PLAYS = 2
PLAY_AD_HALF = 0.50

AD_SIDE_HALF_PCTS = (10.0, 15.0, 20.0, 25.0, 30.0)
PANIC_HALF_PCTS = (20.0, 30.0, 50.0)

# Dump-depth high_magnet: cluster at B, not equal fifths from T.
# P_i = B + L × frac. Forbidden: T − L × i / 5.
AD_SIDE_L_FRACS = tuple(0.065 + (-0.008 - 0.065) * i / 4 for i in range(5))

MET_FRAC = 0.05
THROUGH_FRAC = 0.03
VOL_SPIKE = 1.2
PANIC_BREADTH_MIN = 3

# Panic Q_i = B − B × (0.10 + 0.18 × (i−1) / 2)  — % of B, not L.
PANIC_B_FRACS = tuple(0.10 + 0.18 * (i - 1) / 2.0 for i in range(1, 4))

MANILA_TZ = "Asia/Manila"
MEXC_SPOT_API = "https://api.mexc.com/api/v3"

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

FASTER_TF = {
    "1w": "1d",
    "1d": "15m",
    "12h": "15m",
    "8h": "15m",
    "4h": "15m",
    "1h": "5m",
    "15m": "1m",
    "5m": "1m",
    "1m": "1m",
}

TF_BAR_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "8h": 28800,
    "12h": 43200,
    "1d": 86400,
    "1w": 604800,
}

NEWS_KILL_CLASSES = frozenset({"DELIST", "SCAM", "HACK", "CLOSURE"})

# Public copy must use these words. Never "paper plan" / "paper pack(s)".
PUBLIC_NOUNS = (
    "Machine",
    "hung plan",
    "buy layers",
    "sell layers",
    "Size layers",
    "current price",
)

FORBIDDEN_PUBLIC = ("paper plan", "paper pack", "paper packs", "paper-pack")


def live_orders_allowed() -> bool:
    """Always false. Env cannot enable live MEXC orders."""
    return False


def machine_token() -> str:
    return (os.getenv("MACHINE_TOKEN") or "").strip()


def tf_slow_rank(tf: Optional[str]) -> int:
    if not tf:
        return 0
    return int(TF_SLOW_RANK.get(str(tf).strip(), 0))


def faster_tf_for(play_tf: Optional[str]) -> str:
    key = str(play_tf or "").strip()
    return FASTER_TF.get(key, "1m")


def is_faster_tf(tf: Optional[str], play_tf: Optional[str]) -> bool:
    if not tf or not play_tf:
        return False
    a, b = tf_slow_rank(str(tf)), tf_slow_rank(str(play_tf))
    if a <= 0 or b <= 0:
        return False
    return a < b


def fmt_unknown(value: Any, fallback: str = "unknown") -> Any:
    if value is None or value == "":
        return fallback
    return value
