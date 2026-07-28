"""Fatal-class news classification — keyword rules, anti false-flag.

Never push price opinions, analyst takes, or vague FUD alone.
"""

from __future__ import annotations

import re
from typing import Optional, Set, Tuple

# In-scope classes only
CLASS_DELIST = "DELIST"
CLASS_CLOSURE = "CLOSURE"
CLASS_HACK = "HACK"
CLASS_SCAM = "SCAM"

# High-trust source tags
TRUST_OFFICIAL = "official"
TRUST_REKT = "rekt"
TRUST_AGGREGATE = "aggregate"

_DELIST = re.compile(
    r"\b(delist|delisting|will remove|remov(e|ing) from|suspend(ed|ing)? trading|"
    r"trading suspension|remove the trading pair)\b",
    re.I,
)
_CLOSURE = re.compile(
    r"\b(wind[- ]?down|shutting down|cease operations|shut down|project closure|"
    r"token sunset|discontinu(e|ing)|insolvent|bankrupt)\b",
    re.I,
)
_HACK = re.compile(
    r"\b(hack(ed|ing)?|exploit(ed|ation)?|drained|drain of|security breach|"
    r"flash loan attack|protocol exploit|funds stolen|stolen funds)\b",
    re.I,
)
_SCAM = re.compile(
    r"\b(rug\s?pull|exit scam|confirmed scam|honeypot|ponzi)\b",
    re.I,
)

# Soft / opinion language → reject push
_DENY = re.compile(
    r"\b(could|might|maybe|analyst|price prediction|bullish|bearish outlook|"
    r"technical analysis|community fud|rumor(s)?|allegedly)\b",
    re.I,
)


def classify_headline(
    title: str,
    *,
    body: str = "",
    source_trust: str = TRUST_AGGREGATE,
) -> Optional[Tuple[str, str]]:
    """
    Returns (class, severity) or None if not fatal-class / false-flag risk.

    severity: 'fatal' if confirmable, 'unconfirmed' for weak sources.
    """
    text = f"{title or ''} {body or ''}".strip()
    if not text:
        return None
    if _DENY.search(text) and source_trust == TRUST_AGGREGATE:
        # Soft language on aggregate feeds → drop
        return None

    cls = None
    if _DELIST.search(text):
        cls = CLASS_DELIST
    elif _HACK.search(text):
        cls = CLASS_HACK
    elif _SCAM.search(text):
        cls = CLASS_SCAM
    elif _CLOSURE.search(text):
        cls = CLASS_CLOSURE
    else:
        return None

    if source_trust in (TRUST_OFFICIAL, TRUST_REKT):
        return cls, "fatal"
    # Aggregate needs stronger wording and no deny — still unconfirmed for push gate
    return cls, "unconfirmed"


def extract_symbol_hints(text: str, known_bases: Set[str]) -> Set[str]:
    """Match known base tickers as whole tokens in headline."""
    found: Set[str] = set()
    if not text or not known_bases:
        return found
    upper = text.upper()
    for base in known_bases:
        b = base.upper().strip()
        if len(b) < 2:
            continue
        # word boundary-ish
        if re.search(rf"(?<![A-Z0-9]){re.escape(b)}(?![A-Z0-9])", upper):
            found.add(b)
    return found


def should_push(
    severity: str,
    source_trust: str,
    *,
    push_unconfirmed: bool = False,
) -> bool:
    if severity == "fatal" and source_trust in (TRUST_OFFICIAL, TRUST_REKT):
        return True
    if push_unconfirmed and severity in ("fatal", "unconfirmed"):
        return True
    return False
