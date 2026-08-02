"""Rule-based coach using TRADING_STRATEGY + EventStore stats/lessons.

Never invent fills, news, or event counts — only use provided memory.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def format_brief(
    *,
    recent_events: List[dict],
    open_trades: List[dict],
    learning_on: bool,
    stats: Optional[Dict[str, Any]] = None,
    lessons: Optional[List[dict]] = None,
    pending: Optional[List[dict]] = None,
) -> str:
    lines = ["SESSION BRIEF (AD Desk coach)", ""]
    if not learning_on:
        lines.append("Learning flag is off.")
        return "\n".join(lines)

    if pending:
        lines.append(f"Needs you ({len(pending)} pending):")
        for p in pending[:5]:
            lines.append(f"  Q#{p.get('id')}: {(p.get('question') or '')[:100]}")
        lines.append("")

    if open_trades:
        lines.append("Open journal trades:")
        for t in open_trades[:10]:
            entry = t.get("entry_avg")
            entry_s = f" @ {entry}" if entry is not None else ""
            lines.append(
                f"  #{t['id']} [{t.get('market','?')[:1].upper()}] "
                f"{t['symbol']}{entry_s}"
            )
    else:
        lines.append("Open journal trades: (none)")

    lines.append("")
    lines.append("Recent sensor events:")
    if not recent_events:
        lines.append("  (none yet — fires log when movers/targets trigger)")
    else:
        for e in recent_events[:12]:
            band = e.get("velocity_band") or "—"
            mode = e.get("mode") or e.get("source")
            drop = e.get("drop_pct")
            drop_s = f"{drop:.1f}%" if drop is not None else "?"
            act = e.get("last_action") or "unlabeled"
            lines.append(
                f"  #{e['id']} [{(e.get('market') or '?')[:1].upper()}] "
                f"{e['symbol']} {drop_s} {mode} {band} · {act}"
            )

    if stats and stats.get("events"):
        lines.append("")
        lines.append(_format_stats_line(stats))

    if lessons:
        lines.append("")
        lines.append("Approved lessons:")
        for les in lessons[:5]:
            lines.append(f"  · {(les.get('text') or '')[:120]}")

    lines.extend(
        [
            "",
            "Quality filter (Rule 2): prefer PANIC + volume + market-wide heat.",
            "Avoid: GRIND, isolated dumps, no volume.",
            "Desk: answer pending · teach lessons · approve coach drafts.",
        ]
    )
    return "\n".join(lines)


def _format_stats_line(stats: Dict[str, Any]) -> str:
    med = stats.get("median_bounce_pct")
    med_s = f"{med:.1f}%" if isinstance(med, (int, float)) else "n/a"
    return (
        f"Memory (from your log): events={stats.get('events', 0)} "
        f"took={stats.get('took', 0)} skip={stats.get('skip', 0)} "
        f"partial={stats.get('partial', 0)} late={stats.get('late', 0)} "
        f"panic_band={stats.get('panic_band', 0)} "
        f"median_bounce={med_s} outcomes={stats.get('outcome_n', 0)} "
        f"lessons={stats.get('approved_lessons', 0)}"
    )


def format_coach_pulse(
    *,
    stats: Optional[Dict[str, Any]] = None,
    lessons: Optional[List[dict]] = None,
    pending_n: int = 0,
    drafts_n: int = 0,
) -> str:
    """Short 2–4 line pulse for Overview — store-backed only."""
    lines: List[str] = []
    if pending_n or drafts_n:
        lines.append(
            f"Needs you: {pending_n} question(s), {drafts_n} draft(s) to review."
        )
    if stats and stats.get("events"):
        took = int(stats.get("took") or 0)
        skip = int(stats.get("skip") or 0)
        total_lab = took + skip
        take_rate = f"{100.0 * took / total_lab:.0f}%" if total_lab else "n/a"
        med = stats.get("median_bounce_pct")
        med_s = f"{med:.1f}%" if isinstance(med, (int, float)) else "n/a"
        lines.append(
            f"Log: {stats.get('events')} fires · take-rate {take_rate} "
            f"(took={took} skip={skip}) · median bounce {med_s}."
        )
        band = stats.get("by_band") or {}
        panic = band.get("PANIC") or {}
        if panic.get("n"):
            lines.append(
                f"PANIC band: n={panic.get('n')} took={panic.get('took', 0)} "
                f"skip={panic.get('skip', 0)}."
            )
        beh = stats.get("behaviors") or {}
        if beh:
            top = sorted(beh.items(), key=lambda x: -x[1])[:3]
            lines.append("Behaviors: " + ", ".join(f"{k}×{v}" for k, v in top) + ".")
    else:
        lines.append("Memory: no fires logged yet — coach cites only real log data.")
    if lessons:
        lines.append(f"Lesson: {(lessons[0].get('text') or '')[:100]}")
    lines.append("Rule: panic + breadth > grind; process over lucky PnL.")
    return "\n".join(lines[:5])


def format_coach_reply(
    question: str,
    *,
    recent_events: List[dict],
    stats: Optional[Dict[str, Any]] = None,
    lessons: Optional[List[dict]] = None,
    pending: Optional[List[dict]] = None,
) -> str:
    """Rule-based only. Never invent event counts, fills, or news."""
    q = (question or "").strip().lower()
    lines = [
        "COACH (AD Desk — not financial advice)",
        "Facts only from your event log + labels + approved lessons.",
        "Walk AD Rules 1–8 before size.",
        "",
    ]

    if any(w in q for w in ("grind", "slow", "chop")):
        lines.append(
            "GRIND / slow dumps = low conviction (Rule 2). "
            "Prefer no-trade or micro scout only — not full exponential layers."
        )
    elif any(w in q for w in ("panic", "dump", "crash", "cascade")):
        lines.append(
            "PANIC checklist: sharp velocity, volume into low, market-wide heat, "
            "familiar AD range or Initial Drop defined. "
            "If yes → scale in exponentially (5–10 layers), powder for extension. "
            "If isolated + bad news → no-trade (Rule 6)."
        )
    elif any(w in q for w in ("pride", "hold", "exit", "tp")):
        lines.append(
            "Pride risk: do not marry a wrong thesis. "
            "Failed AD (no bounce in expected time) → exit to preserve capital. "
            "Free coins: prompt partial TPs on strength (greed weakness)."
        )
    elif any(w in q for w in ("fomo", "chase", "late")):
        lines.append(
            "FOMO / late: if engagement is after the panic wick and price already "
            "reclaimed, treat as higher risk. Prefer planned deeper layers over chase. "
            "Tag late entries honestly so the log stays clean."
        )
    elif any(w in q for w in ("hesitant", "missed", "skip")):
        lines.append(
            "Hesitant skips on clean PANIC + heat are process debt. "
            "AFK is fine — mark process_skip when intentional Rule 6. "
            "Discuss skip why on desk when you return."
        )
    elif any(w in q for w in ("layer", "size", "entry", "percent", "%")):
        lines.append(
            "Default layers: 5–10, exponential (small first, larger deeper). "
            "Size language: % of account primary (notes may use $). "
            "Deep extension past AD = best entries; defensive on exits."
        )
    elif any(w in q for w in ("news", "hack", "delist", "scam")):
        lines.append(
            "Fatal news (delist/hack/closure/scam) → treat as isolated/destructive. "
            "Prefer no-trade. Check desk intel when available."
        )
    elif any(w in q for w in ("brief", "summary", "overview", "desk")):
        return format_brief(
            recent_events=recent_events,
            open_trades=[],
            learning_on=True,
            stats=stats,
            lessons=lessons,
            pending=pending,
        )
    else:
        lines.append(
            "Pre-flight: (1) familiar AD range? (2) panic vs grind? market-wide? "
            "(3) volume? (4) layers + failed-AD criteria? "
            "(5) if low conviction → No trade only."
        )

    if stats and stats.get("events"):
        lines.append("")
        lines.append(_format_stats_line(stats))
        beh = stats.get("behaviors") or {}
        if beh:
            lines.append(
                "Behavior tags (confirmed): "
                + ", ".join(f"{k}={v}" for k, v in sorted(beh.items()))
            )
    else:
        lines.append("")
        lines.append(
            "Memory: none logged yet (or empty stats) — no invented take-rates."
        )

    if lessons:
        lines.append("")
        lines.append("Your approved lessons:")
        for les in lessons[:4]:
            lines.append(f"  · {(les.get('text') or '')[:140]}")

    if pending:
        lines.append("")
        lines.append(f"Open desk questions ({len(pending)}): answer on Learning.")
        for p in pending[:2]:
            lines.append(f"  Q#{p.get('id')}: {(p.get('question') or '')[:90]}")

    if recent_events:
        e = recent_events[0]
        lines.append("")
        lines.append(
            f"Latest event: #{e['id']} {e.get('market')}:{e.get('symbol')} "
            f"band={e.get('velocity_band') or '—'} "
            f"label={e.get('last_action') or 'unlabeled'}"
        )
    else:
        lines.append("")
        lines.append("Latest event: (none logged yet)")

    lines.append("")
    lines.append(
        "Desk Learning: Needs you · trade reviews (PnL/layers/hold) · "
        "by ticker · teach · approve drafts. Voice uses the same memory."
    )
    return "\n".join(lines)


def propose_behavior_draft(
    *,
    event: Optional[dict],
    stats: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Optional coach draft text for owner approval — not auto-confirmed."""
    if not event:
        return None
    act = (event.get("last_action") or "").lower()
    band = (event.get("velocity_band") or "").upper()
    bounce = event.get("outcome_bounce")
    text = None
    code = None
    if act == "late":
        code = "fomo"
        text = (
            f"Draft: #{event.get('id')} {event.get('symbol')} labeled late — "
            "consider behavior fomo if entry chased after structure spent."
        )
    elif act == "skip" and band == "PANIC":
        code = "hesitant"
        text = (
            f"Draft: skipped PANIC fire #{event.get('id')} {event.get('symbol')} — "
            "tag hesitant unless intentional process_skip / AFK."
        )
    elif act == "took" and band == "GRIND":
        code = "rule_break"
        text = (
            f"Draft: took GRIND #{event.get('id')} {event.get('symbol')} — "
            "Rule 2 low conviction; consider rule_break unless micro scout."
        )
    elif act == "took" and bounce is not None:
        try:
            if float(bounce) < 0.5:
                code = "pride"
                text = (
                    f"Draft: took #{event.get('id')} with weak bounce "
                    f"({bounce}%) — watch pride if still holding failed AD."
                )
        except (TypeError, ValueError):
            pass
    if not text:
        return None
    return {
        "text": text,
        "tags": [code] if code else [],
        "kind": "behavior_draft",
        "needs_approval": True,
        "behavior": code,
        "evidence_event_ids": [int(event["id"])] if event.get("id") else [],
    }
