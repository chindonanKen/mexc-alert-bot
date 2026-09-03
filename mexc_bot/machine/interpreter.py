"""Walk the process pack. Higher (lower number) wins. No play numbers here."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence


def interpret(
    pack: Dict[str, Any],
    facts: Dict[str, Any],
) -> Dict[str, Any]:
    """Return the winning rule. Recut of the pack changes the next call."""
    rules = list(pack.get("rules") or [])
    rules.sort(key=lambda r: (int(r.get("priority") or 99), str(r.get("id") or "")))
    matched: List[Dict[str, Any]] = []
    for rule in rules:
        when = rule.get("when") or []
        if not isinstance(when, Sequence):
            continue
        if all(bool(facts.get(str(name))) for name in when):
            matched.append(rule)
    winner = matched[0] if matched else {
        "id": "size.wait",
        "family": "size",
        "priority": 5,
        "action": "wait",
        "why": "No matching rule, wait.",
    }
    return {
        "action": str(winner.get("action") or "wait"),
        "rule_id": str(winner.get("id") or ""),
        "rule_ids": [str(r.get("id") or "") for r in matched[:4]],
        "family": str(winner.get("family") or ""),
        "priority": int(winner.get("priority") or 99),
        "why": str(winner.get("why") or "Wait."),
        "matched": [str(r.get("id") or "") for r in matched],
    }


def why_sentence(result: Dict[str, Any], *, extra: Optional[str] = None) -> str:
    ids = result.get("rule_ids") or [result.get("rule_id")]
    ids = [i for i in ids if i]
    base = str(result.get("why") or "Wait.")
    if extra:
        base = f"{base} {extra}".strip()
    if ids:
        return f"{base} ({', '.join(ids[:3])})"
    return base
