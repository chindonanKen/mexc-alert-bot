"""P2-ready setup case buckets (closed set of four).

Buckets classify the *setup decision*, not PnL:
  ad_take   — valid AD scale-in (rules met, take/scale)
  ad_press  — late vol / under-AD → size up / add hard
  ad_wait   — false start / first candles / need more structure
  ad_skip   — no AD, delist/intel ban, FOMO above AD, correct no-trade
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

CASE_BUCKETS = (
    "ad_take",
    "ad_press",
    "ad_wait",
    "ad_skip",
)

# Owner-curated map for the first 19 teach rows (prod lesson ids).
# Used by normalize_learning_index so early memory is bucket-clean for P2.
OWNER_LESSON_BUCKETS = {
    2: "ad_skip",  # KORU — panic, AD not reached
    5: "ad_take",  # SYN weekly/5d long AD
    6: "ad_take",  # SYN still AD + RSI add
    9: "ad_wait",  # AXTI fire — wait 3rd 15m into zone
    10: "ad_take",  # AXTI trade — layered AD + scale-out
    12: "ad_skip",  # ASTEROID — slept; structure note only
    14: "ad_skip",  # 1000RATS — panic, no AD
    15: "ad_take",  # SYN book — multi-TF AD
    16: "ad_take",  # LAB — 3 red 15m + vol into zone
    17: "ad_skip",  # SYN fut FOMO above AD
    18: "ad_press",  # HFT — missed 2nd buy / size
    19: "ad_take",  # HFT fire — good AD bounce chart
    20: "ad_take",  # HFT free coins AD
    21: "ad_wait",  # HFT — extreme 1st 15m; wait 3rd; failed AD
    22: "ad_press",  # BTW late-vol AD; hesitant under-size
    23: "ad_wait",  # BLUAI — first candles; wait 3+ reds
    24: "ad_wait",  # BTW re-top — patience to deeper AD
    25: "ad_skip",  # HFT delist intel ban
    26: "ad_skip",  # BANANAS — no real panic / not in zone
}

BUCKET_HELP = {
    "ad_take": "Valid AD zone + structure — scale in / hold plan",
    "ad_press": "Late vol climax under AD — press size / add",
    "ad_wait": "Too early / first candles / need more reds — patience",
    "ad_skip": "No AD, delist, FOMO, or correct skip",
}


def normalize_bucket(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    s = str(raw).strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "take": "ad_take",
        "true_ad": "ad_take",
        "true_ad_scale": "ad_take",
        "scale": "ad_take",
        "press": "ad_press",
        "aggressive": "ad_press",
        "late_vol": "ad_press",
        "late_vol_aggressive": "ad_press",
        "wait": "ad_wait",
        "false_start": "ad_wait",
        "false_start_wait": "ad_wait",
        "patience": "ad_wait",
        "skip": "ad_skip",
        "no_ad": "ad_skip",
        "panic_no_ad": "ad_skip",
        "delist": "ad_skip",
        "fomo": "ad_skip",
        "failed_ad": "ad_skip",  # structural fail path → skip/fail bucket for P2
    }
    if s in CASE_BUCKETS:
        return s
    return aliases.get(s)


def infer_case_bucket(
    *,
    chips: Optional[Sequence[str]] = None,
    features: Optional[Dict[str, Any]] = None,
    note: Optional[str] = None,
    explicit: Optional[str] = None,
) -> str:
    """Infer one of four buckets. Explicit tag wins when valid."""
    ex = normalize_bucket(explicit)
    if ex:
        return ex

    chip_set = {str(c).lower().strip() for c in (chips or []) if c}
    note_l = (note or "").lower()
    feats = features or {}

    # Hard skips
    if "rule_break" in chip_set and (
        "delist" in note_l or "intel" in note_l or "binance" in note_l
    ):
        return "ad_skip"
    if "fomo" in chip_set:
        return "ad_skip"
    if "ad_missed" in chip_set and "ad_met" not in chip_set:
        # panic without AD, or correct skip
        if "false_panic" in chip_set:
            return "ad_wait"
        return "ad_skip"

    # Wait / false start
    if "false_panic" in chip_set:
        return "ad_wait"
    if any(
        x in note_l
        for x in (
            "first candle",
            "first red",
            "never enter on first",
            "patience needed",
            "wait until 3rd",
            "wait for the third",
            "too early",
        )
    ):
        return "ad_wait"

    # Press size on late vol AD
    if "ad_met" in chip_set and (
        "hesitant" in chip_set
        or "process_skip" in chip_set
        or "size up" in note_l
        or "press size" in note_l
        or "under-ad" in note_l
        or "under ad" in note_l
        or "vol climax" in note_l
        or "late" in note_l
        and "ad" in note_l
    ):
        return "ad_press"

    # Features: at AD + surge vol → take or press
    ad_zone = str(feats.get("ad_zone") or "")
    vol_flag = str(feats.get("vol_flag") or "")
    if "ad_met" in chip_set or ad_zone in ("at_ad", "through_ad", "near_ad"):
        if vol_flag == "surge" and feats.get("ad_ready"):
            if "hesitant" in chip_set:
                return "ad_press"
            return "ad_take"
        if "ad_met" in chip_set:
            return "ad_take"

    if "plan_ok" in chip_set and "ad_met" in chip_set:
        return "ad_take"

    if "plan_ok" in chip_set and "ad_missed" in chip_set:
        return "ad_skip"

    return "ad_take" if "ad_met" in chip_set else "ad_skip"


def bucket_tag(bucket: str) -> str:
    b = normalize_bucket(bucket) or "ad_skip"
    return f"bucket:{b}"


def ensure_bucket_in_chips_or_tags(
    tags: List[str],
    *,
    chips: Optional[Sequence[str]] = None,
    features: Optional[Dict[str, Any]] = None,
    note: Optional[str] = None,
    explicit: Optional[str] = None,
) -> List[str]:
    """Return tags with exactly one bucket: tag."""
    ex = normalize_bucket(explicit)
    out = []
    for t in tags or []:
        ts = str(t)
        if ts.lower().startswith("bucket:"):
            if ex is None:
                ex = ts.split(":", 1)[1]
            continue
        out.append(ts)
    b = infer_case_bucket(
        chips=chips, features=features, note=note, explicit=ex
    )
    out.append(bucket_tag(b))
    return out
