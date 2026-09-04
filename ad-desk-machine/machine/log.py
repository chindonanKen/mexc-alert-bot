"""Machine log: only decision changes. No wait spam."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any

MANILA = timezone(timedelta(hours=8))

# SPEC-uncluster tape actions only
LOGGABLE = frozenset(
    {
        "kill",
        "met",
        "sit-out",
        "paper-buy",
        "paper-sell",
        "add-panic",
        "flatten-news",
        "recut",
        "sell-layers",
        "exit-live",
        "board-grind",
        "board-panic",
    }
)


@dataclass
class LogEntry:
    ts: datetime
    action: str
    name: str | None
    price: float | None
    size_pct: float | None
    why: str

    def to_dict(self) -> dict[str, Any]:
        local = self.ts.astimezone(MANILA)
        return {
            "ts": self.ts.isoformat(),
            "manila": local.strftime("%H:%M:%S"),
            "action": self.action,
            "name": self.name,
            "price": self.price,
            "size_pct": self.size_pct,
            "why": self.why,
        }


@dataclass
class MachineLog:
    entries: list[LogEntry] = field(default_factory=list)
    _last_key: dict[str, str] = field(default_factory=dict)

    def append(
        self,
        action: str,
        why: str,
        *,
        name: str | None = None,
        price: float | None = None,
        size_pct: float | None = None,
        ts: datetime | None = None,
        force: bool = False,
    ) -> LogEntry | None:
        """Only log decision changes. Skip wait / duplicate spam."""
        if action in ("wait", "pull-pack", "enter", "exit", "miss", "grind-on", "grind-off", "panic-on", "panic-off"):
            return None
        if action not in LOGGABLE and not force:
            return None
        key_name = name or "_board"
        key = f"{key_name}:{action}:{why}"
        if not force and self._last_key.get(key_name) == key:
            return None
        self._last_key[key_name] = key
        entry = LogEntry(
            ts=ts or datetime.now(timezone.utc),
            action=action,
            name=name,
            price=price,
            size_pct=size_pct,
            why=why,
        )
        self.entries.append(entry)
        return entry

    def as_list(self) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self.entries]

    def last_sell_why(self, name: str) -> str | None:
        """Latest paper-sell why for this name. None when no paper-sell yet — do not invent."""
        for e in reversed(self.entries):
            if e.action == "paper-sell" and e.name == name:
                return e.why
        return None
