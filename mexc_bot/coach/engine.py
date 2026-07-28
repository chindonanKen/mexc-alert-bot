"""Rule-based coach replies using TRADING_STRATEGY principles + event memory."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def format_brief(
    *,
    recent_events: List[dict],
    open_trades: List[dict],
    learning_on: bool,
) -> str:
    lines = ["SESSION BRIEF (learning)", ""]
    if not learning_on:
        lines.append("Learning flag is off.")
        return "\n".join(lines)

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
        lines.append("  (none yet — fires will log when movers/targets trigger)")
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

    lines.extend(
        [
            "",
            "Quality filter (Rule 2): prefer PANIC + volume + market-wide heat.",
            "Avoid: GRIND, isolated dumps, no volume.",
            "Label: /j took | /j skip | /j bounce strong|weak|none|failed",
            "Journal: /trade open f SYMBOL [price] | /trade list | /trade close",
        ]
    )
    return "\n".join(lines)


def format_coach_reply(
    question: str,
    *,
    recent_events: List[dict],
    stats: Optional[Dict[str, Any]] = None,
) -> str:
    """Rule-based only. Never invent event counts, fills, or news.

    Memory lines appear only when `stats` / `recent_events` come from EventStore.
    """
    q = (question or "").strip().lower()
    lines = [
        "COACH (rule-based V1 — not financial advice)",
        "Facts only from your event log + labels. No invented fills or news.",
        "Walk AD Rules 1–8 before size.",
        "",
    ]

    # Cheap intent routing without LLM
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
    elif any(w in q for w in ("layer", "size", "entry")):
        lines.append(
            "Default layers: 5–10, exponential (small first, larger deeper). "
            "Deep extension past AD = best entries; defensive on exits. "
            "Near major bases: wider spacing, smaller early size."
        )
    elif any(w in q for w in ("news", "hack", "delist", "scam")):
        lines.append(
            "Fatal news (delist/hack/closure/scam) → treat as isolated/destructive. "
            "Prefer no-trade. News monitor is V1.1; until then check announcements manually."
        )
    else:
        lines.append(
            "Pre-flight: (1) familiar AD range? (2) panic vs grind? market-wide? "
            "(3) volume? (4) layers + failed-AD criteria? "
            "(5) if low conviction → No trade only."
        )

    if stats and stats.get("events"):
        lines.append("")
        lines.append(
            f"Memory (from your log): events={stats['events']} "
            f"took={stats.get('took', 0)} skip={stats.get('skip', 0)} "
            f"panic_band={stats.get('panic_band', 0)}"
        )
    else:
        lines.append("")
        lines.append("Memory: no labeled history for that symbol yet (or no symbol in question).")

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
    lines.append("Ask: /coach panic | layers | pride | news | grind")
    lines.append("Label after fires: /j took | /j skip — coach improves only with real labels.")
    return "\n".join(lines)
