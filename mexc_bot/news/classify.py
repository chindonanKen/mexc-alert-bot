"""Fatal-class news classification — keyword rules, anti false-flag.

Never push price opinions, analyst takes, or vague FUD alone.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Optional, Set, Tuple

from .book import collect_bases, news_touches_book

# In-scope classes only
CLASS_DELIST = "DELIST"
CLASS_CLOSURE = "CLOSURE"
CLASS_HACK = "HACK"
CLASS_SCAM = "SCAM"
CLASS_HALT = "HALT"

DEVASTATING_CLASSES = frozenset(
    {
        CLASS_DELIST,
        CLASS_CLOSURE,
        CLASS_HACK,
        CLASS_SCAM,
        CLASS_HALT,
    }
)

# High-trust source tags
TRUST_OFFICIAL = "official"
TRUST_REKT = "rekt"
TRUST_AGGREGATE = "aggregate"

_DELIST = re.compile(
    r"\b(delist|delisting|will remove|remov(e|ing) from|suspend(ed|ing)? trading|"
    r"trading suspension|remove the trading pair)\b",
    re.I,
)
_HALT = re.compile(
    r"\b(trading halt|halted|halt in trading|market halt)\b",
    re.I,
)
_CLOSURE = re.compile(
    r"\b(wind[- ]?down|shutting down|cease operations|shut down|project closure|"
    r"token sunset|discontinu(e|ing)|insolvent|insolvency|bankrupt)\b",
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

# Soft / opinion / rumor / spam → never classify as fatal, never alarm
_DENY = re.compile(
    r"\b(could|might|maybe|analyst|price prediction|bullish|bearish outlook|"
    r"technical analysis|community fud|rumor(s)?|allegedly|unconfirmed|"
    r"unverified|sources say|clickbait|just in:?\s*$)\b",
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
    if _DENY.search(text):
        return None

    cls = None
    if _HALT.search(text) and not _DELIST.search(text):
        cls = CLASS_HALT
    elif _DELIST.search(text):
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


def is_devastating_class(cls: Optional[str]) -> bool:
    return str(cls or "").upper() in DEVASTATING_CLASSES


def should_alarm(
    severity: str,
    source_trust: str,
    *,
    on_book: bool,
    cls: Optional[str] = None,
    title: str = "",
    push_unconfirmed: bool = False,
) -> bool:
    """Alarm Kenneth (Telegram) and Master (desk) only on devastating book news.

    Rumors / spam / generic headlines never alarm. Off-book names never alarm.
    """
    if not on_book:
        return False
    if _DENY.search(title or ""):
        return False
    if not is_devastating_class(cls):
        return False
    if not should_push(
        severity, source_trust, push_unconfirmed=push_unconfirmed
    ):
        return False
    return True


def evaluate_headline(
    title: str,
    *,
    body: str = "",
    source_trust: str = TRUST_AGGREGATE,
    symbol: Optional[str] = None,
    item_bases: Optional[Iterable[str]] = None,
    book_bases: Optional[Iterable[str]] = None,
    book_syms: Optional[Iterable[str]] = None,
    push_unconfirmed: bool = False,
) -> Dict[str, Any]:
    """Show vs alarm decision. Does not send Telegram."""
    classified = classify_headline(title, body=body, source_trust=source_trust)
    bases = set(collect_bases(*(item_bases or []), symbol or ""))
    on_book = news_touches_book(
        symbol=symbol,
        title=title,
        bases=bases,
        book_bases=book_bases,
        book_syms=book_syms,
    )
    if not classified:
        return {
            "cls": None,
            "severity": None,
            "on_book": on_book,
            "show": False,
            "alarm": False,
        }
    cls, severity = classified
    show = on_book and is_devastating_class(cls)
    alarm = should_alarm(
        severity,
        source_trust,
        on_book=on_book,
        cls=cls,
        title=title,
        push_unconfirmed=push_unconfirmed,
    )
    return {
        "cls": cls,
        "severity": severity,
        "on_book": on_book,
        "show": show,
        "alarm": alarm,
    }
