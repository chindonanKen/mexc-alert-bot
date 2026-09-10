#!/usr/bin/env python3
"""Generate The Machine brain map as an SVG connected graph under static/brain-map/."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
OUT = ROOT / "static" / "brain-map"
SCENARIO_PLAN = DOCS / "MACHINE_SCENARIO_PASS_PLAN.md"
BRAIN_PLAN = DOCS / "MACHINE_BRAIN_MAP_PLAN.md"
PROCESS_BOOK = Path("/home/box/agent-data/workflows/ad-desk-rules/SKILL.md")
EXIT_BOOK = Path("/home/box/agent-data/workflows/ad-exit-strategy/SKILL.md")
LOCK_TAGS = ROOT / "data" / ".grokbot" / "lock_brain_map_tags.json"
LOCK_TAGS_DOCS = DOCS / "LOCK_BRAIN_MAP_TAGS.json"

# KEEP seven + gate rail three (design pass 3). Fail stays one neuron.
LAYER_ORDER = [
    "Chart",
    "Path",
    "Size",
    "Fail",
    "Exit",
    "Feed",
    "Machine log",
    "Lock PASS",
    "Hang",
    "Outcome writeback",
]

NODE_POS = {
    "Feed": (600, 80),
    "Chart": (180, 280),
    "Path": (520, 280),
    "Size": (860, 280),
    "Fail": (520, 480),
    "Exit": (860, 520),
    "Lock PASS": (1120, 240),
    "Hang": (1120, 400),
    "Outcome writeback": (1120, 560),
    "Machine log": (700, 700),
}

WHEN = {
    "Feed": "prints only",
    "Chart": "met + why",
    "Path": "paper-buy · sit-out · wait",
    "Size": "paper-buy · sit-out",
    "Fail": "add-panic + why",
    "Exit": "paper-sell · exit-live",
    "Machine log": "decision + why",
    "Lock PASS": "PASS · FAIL",
    "Hang": "hung · watch-only",
    "Outcome writeback": "outcome on record",
}

# Decision-print / gate shapes — KEEP Kenneth-locked; gate rail Kenneth yes 2026-09-09
PRINTS = {
    "Chart": [
        {"verb": "met", "when": "First met-band entry", "shape": "met + why (low entered band). No buy / sit / size / exit.", "tape": True, "row": True},
    ],
    "Path": [
        {"verb": "paper-buy", "when": "After Size fills (why = Path tag/panic)", "shape": "Tape: paper-buy only after Size fills.", "tape": True, "row": False},
        {"verb": "sit-out", "when": "At AD with Path why", "shape": "sit-out + Path why at AD.", "tape": True, "row": True},
        {"verb": "wait", "when": "Not tagged", "shape": "wait + why not tagged (row only; no tape line).", "tape": False, "row": True},
    ],
    "Size": [
        {"verb": "paper-buy", "when": "On fill", "shape": "Tape: paper-buy on fill.", "tape": True, "row": True},
        {"verb": "sit-out", "when": "Path bought; Size waits at AD / board grind", "shape": "sit-out + Size why.", "tape": True, "row": True},
    ],
    "Fail": [
        {"verb": "add-panic", "when": "Met already; price under B", "shape": "add-panic + why Fail — current price broke AD; add panic half. Path sit must not block.", "tape": True, "row": True},
    ],
    "Exit": [
        {"verb": "paper-sell", "when": "Sell / into named base", "shape": "paper-sell + layer why or into named base.", "tape": True, "row": True},
        {"verb": "exit-live", "when": "Adapt", "shape": "exit-live + adapt reasons. Named unmet base clips bounce map high.", "tape": True, "row": True},
    ],
    "Feed": [
        {"verb": "prints", "when": "Always sensor", "shape": "prints only — no buy / sit / sell.", "tape": False, "row": False},
    ],
    "Machine log": [
        {"verb": "decision", "when": "Decision change", "shape": "one line per change: {action, name, price, size_pct?, why}; no wait spam.", "tape": True, "row": True},
        {"verb": "kill", "when": "Intentional out", "shape": "kill + why intentional out.", "tape": True, "row": True},
    ],
    "Lock PASS": [
        {"verb": "PASS", "when": "Layers + facts hang-ready", "shape": "PASS + Lock why. No invent prices.", "tape": True, "row": True},
        {"verb": "FAIL", "when": "Broken lock named", "shape": "FAIL + Lock why (hang not ready).", "tape": True, "row": True},
    ],
    "Hang": [
        {"verb": "hung", "when": "After Lock PASS only", "shape": "hung (watch-only until Kenneth unlocks live).", "tape": True, "row": True},
        {"verb": "watch-only", "when": "Hung plan watch", "shape": "watch-only on hung record until live unlock.", "tape": False, "row": True},
    ],
    "Outcome writeback": [
        {"verb": "outcome", "when": "Close / kill on same hung record", "shape": "Write outcome on the same hung record; then Machine log may print the change.", "tape": True, "row": True},
    ],
}

MODULE_MAP = {
    "Chart": [{"path": "machine/chart.py", "symbols": "is_in_met_band, update_met, at_ad"}],
    "Path": [{"path": "machine/path.py", "symbols": "evaluate_path"}],
    "Size": [{"path": "machine/size.py", "symbols": "build_buy_layers, gate_buy_layers, is_real_volume"}],
    "Fail": [{"path": "machine/engine.py", "symbols": "fail_add_panic"}],
    "Exit": [{"path": "machine/exit.py", "symbols": "live_read_exit, load_exit_facts"}],
    "Feed": [
        {"path": "machine/feeds.py", "symbols": "print_from_klines, fetch_mexc_klines"},
        {"path": "machine/loop.py", "symbols": "DecisionLoop, build_default_loop"},
    ],
    "Machine log": [
        {"path": "machine/log.py", "symbols": "MachineLog"},
        {"path": "machine/api.py", "symbols": "POST /api/machine/kill"},
    ],
    "Lock PASS": [{"path": "process book / Lock seat", "symbols": "Lock PASS|FAIL before hang"}],
    "Hang": [{"path": "machine/api.py", "symbols": "POST /api/machine/hang"}],
    "Outcome writeback": [{"path": "machine/engine.py", "symbols": "_write_outcome, _persist_play"}],
}

# KEEP 1–12 + gate rail 13–18 + refuse R1–R3 (waypoints on refuse rail x=1220)
MIN_EDGES = [
    {"from": "feed", "to": "path", "kind": "feeds", "label": "prints"},
    {"from": "feed", "to": "size", "kind": "feeds", "label": "prints"},
    {"from": "chart", "to": "path", "kind": "handoff", "label": "met"},
    {"from": "chart", "to": "fail", "kind": "handoff", "label": "met break"},
    {"from": "chart", "to": "exit", "kind": "handoff", "label": "AD / bases"},
    {"from": "path", "to": "size", "kind": "handoff", "label": "paper-buy gate"},
    {"from": "size", "to": "path", "kind": "takeover", "label": "sit-out · Size why"},
    {"from": "fail", "to": "size", "kind": "handoff", "label": "add-panic"},
    {"from": "path", "to": "fail", "kind": "conflict", "label": "sit-out vs add-panic"},
    {"from": "size", "to": "exit", "kind": "handoff", "label": "open bag"},
    {"from": "path", "to": "machine_log", "kind": "handoff", "label": "why"},
    {"from": "size", "to": "machine_log", "kind": "handoff", "label": "why"},
    {"from": "fail", "to": "machine_log", "kind": "handoff", "label": "why"},
    {"from": "exit", "to": "machine_log", "kind": "handoff", "label": "paper-sell · exit-live"},
    {"from": "size", "to": "lock_pass", "kind": "handoff", "label": "layers ready"},
    {"from": "exit", "to": "lock_pass", "kind": "handoff", "label": "sell layers"},
    {"from": "chart", "to": "lock_pass", "kind": "handoff", "label": "facts"},
    {"from": "lock_pass", "to": "hang", "kind": "handoff", "label": "PASS"},
    {"from": "hang", "to": "outcome_writeback", "kind": "handoff", "label": "same record"},
    {"from": "outcome_writeback", "to": "machine_log", "kind": "handoff", "label": "outcome"},
    # Wrong gates — refuse rail x≥1220; never through gate-rail bodies
    {"from": "size", "to": "hang", "kind": "refuse", "label": "skip Lock", "waypoints": [[1220, 280], [1220, 400]]},
    {"from": "lock_pass", "to": "outcome_writeback", "kind": "refuse", "label": "skip Hang", "waypoints": [[1220, 240], [1220, 560]]},
    {"from": "hang", "to": "machine_log", "kind": "refuse", "label": "skip writeback", "waypoints": [[1220, 400], [1220, 700]]},
]

GATE_SEATS = {"Lock PASS", "Hang", "Outcome writeback"}

def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def _parse_md_tables(text: str) -> list[list[list[str]]]:
    tables: list[list[list[str]]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if line.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s\-:|]+\|$", lines[i + 1].strip()):
            rows: list[list[str]] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                raw = lines[i].strip()
                if re.match(r"^\|[\s\-:|]+\|$", raw):
                    i += 1
                    continue
                rows.append([c.strip() for c in raw.strip("|").split("|")])
                i += 1
            if rows:
                tables.append(rows)
            continue
        i += 1
    return tables


def parse_decision_owners(text: str) -> dict[str, dict[str, str]]:
    owners: dict[str, dict[str, str]] = {}
    for table in _parse_md_tables(text):
        header = [h.lower() for h in table[0]]
        if "seat" not in header or "owns" not in header:
            continue
        if not any("does not own" in h for h in header):
            continue
        si = header.index("seat")
        oi = next(i for i, h in enumerate(header) if h == "owns")
        di = next(i for i, h in enumerate(header) if "does not own" in h)
        for row in table[1:]:
            if len(row) <= max(si, oi, di):
                continue
            seat = row[si].strip()
            if seat in LAYER_ORDER:
                owners[seat] = {"owns": row[oi].strip(), "does_not_own": row[di].strip()}
        if owners:
            break
    return owners


def parse_scenario_ids(text: str) -> dict[str, list[str]]:
    seats = {n: [] for n in LAYER_ORDER}
    aliases = {
        "Chart": ["Chart"],
        "Path": ["Path"],
        "Size": ["Size"],
        "Fail": ["Fail"],
        "Exit": ["Exit"],
        "Feed": ["Feed"],
        "Machine log": ["Machine log", "MachineLog", "log"],
    }
    for table in _parse_md_tables(text):
        header = [h.lower() for h in table[0]]
        if "id" not in header or "owner" not in header:
            continue
        idi, oi = header.index("id"), header.index("owner")
        for row in table[1:]:
            if len(row) <= max(idi, oi):
                continue
            sid = row[idi].strip()
            if not re.match(r"^[CPFSEH]\d+$", sid):
                continue
            owner = row[oi]
            for seat, als in aliases.items():
                if any(re.search(rf"\b{re.escape(a)}\b", owner) for a in als):
                    if sid not in seats[seat]:
                        seats[seat].append(sid)
    return seats


def _kenneth_date_in(text: str) -> str | None:
    m = re.search(r"Kenneth\s+(\d{4}-\d{2}-\d{2})", text)
    return m.group(1) if m else None


def _split_sections(book: str) -> dict[str, str]:
    out = {n: "" for n in LAYER_ORDER}
    for part in re.split(r"^##\s+", book, flags=re.M)[1:]:
        lines = part.splitlines()
        if not lines:
            continue
        title, body = lines[0].strip(), "\n".join(lines[1:])
        for seat in ("Chart", "Path", "Size", "Fail", "Exit"):
            if title == seat or title.startswith(seat + " "):
                out[seat] = body
        # Kenneth 2026-09-10: H1 hang gate lives under Lock hang checks → Lock PASS neuron
        if title.startswith("Lock hang"):
            out["Lock PASS"] = body
    return out


def _rules_from_section(body: str, source: str) -> list[dict]:
    rules = []
    if not body.strip():
        return rules
    for raw in re.findall(r"(?m)^-\s+(.+(?:\n(?!- |^## ).+)*)", body):
        line = re.sub(r"\s+", " ", raw.strip())
        date = _kenneth_date_in(line)
        if not date and not re.search(r"\(locked\)|locked this", line, re.I):
            continue
        if len(line) > 420:
            line = line[:417] + "…"
        rules.append({"text": line, "tag": "kenneth_locked", "kenneth_date": date, "source": source})
    return rules


def extract_rules(scenario: str, process: str, exit_book: str) -> dict[str, list[dict]]:
    rules = {n: [] for n in LAYER_ORDER}
    for seat, body in _split_sections(process).items():
        rules[seat].extend(_rules_from_section(body, "ad-desk-rules process book"))
    if exit_book.strip():
        for r in _rules_from_section(exit_book, "ad-exit-strategy process book"):
            if all(r["text"] != x["text"] for x in rules["Exit"]):
                rules["Exit"].append(r)
    if "Path RECUT" in scenario and "hung AD buy layer" in scenario:
        rules["Path"].insert(
            0,
            {
                "text": (
                    "Path buys when print tags a hung AD buy layer "
                    "(print ≤ empty/next AD-role layer) or board-wide panic. "
                    "Size owns live volume and grind wait."
                ),
                "tag": "kenneth_locked",
                "kenneth_date": "2026-09-07",
                "source": "MACHINE_SCENARIO_PASS_PLAN.md Path RECUT",
            },
        )
    owners = parse_decision_owners(scenario)
    for seat, od in owners.items():
        for label, key in (("Owns", "owns"), ("Does not own", "does_not_own")):
            line = od.get(key, "").strip()
            if line:
                rules[seat].append(
                    {
                        "text": f"{label}: {line}",
                        "tag": "staff_proposed",
                        "kenneth_date": None,
                        "source": "MACHINE_SCENARIO_PASS_PLAN.md decision owners",
                        "context_only": True,
                    }
                )
    return rules


def extract_upgrades(scenario: str) -> list[dict]:
    # G4–G7 CLOSE/REJECT. Decision-print shapes Kenneth-locked 2026-09-08 — no open lock-ask.
    _ = scenario
    return []


def load_lock_tags() -> dict:
    for p in (LOCK_TAGS, LOCK_TAGS_DOCS):
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    return {"seat_tags": {}}


def build_payload() -> dict:
    scenario = _read(SCENARIO_PLAN)
    process = _read(PROCESS_BOOK)
    exit_book = _read(EXIT_BOOK)
    owners = parse_decision_owners(scenario)
    scenarios = parse_scenario_ids(scenario)
    rules = extract_rules(scenario, process, exit_book)
    upgrades = extract_upgrades(scenario)
    seat_tags = (load_lock_tags().get("seat_tags") or {})

    layers = []
    for name in LAYER_ORDER:
        lid = name.lower().replace(" ", "_")
        x, y = NODE_POS[name]
        od = owners.get(name, {"owns": "", "does_not_own": ""})
        freeze = seat_tags.get(lid)
        if freeze in ("kenneth_locked", "staff_proposed"):
            tag = freeze
        elif name in GATE_SEATS:
            # Kenneth yes on implement 2026-09-09 — until Lock freeze files these seats
            tag = "kenneth_locked"
        else:
            tag = "staff_proposed"
        layers.append(
            {
                "id": lid,
                "name": name,
                "x": x,
                "y": y,
                "when": WHEN[name],
                "prints": PRINTS.get(name, []),
                "owns": od.get("owns", ""),
                "does_not_own": od.get("does_not_own", ""),
                "tag": tag,
                "rules": rules.get(name, []),
                "modules": MODULE_MAP.get(name, []),
                "scenarios": scenarios.get(name, []),
            }
        )

    return {
        "name": "The Machine",
        "title": "brain map",
        "live_orders_allowed": False,
        "head": "THE MACHINE · brain map",
        "layers": layers,
        "edges": list(MIN_EDGES),
        "regenerated_at": __import__("datetime").datetime.now(__import__("datetime").timezone(__import__("datetime").timedelta(hours=8))).strftime("%Y-%m-%d %H:%M PHT"),
        "overlaps": [],
        "upgrades": upgrades,
        "upgrade_help": "suggestions stay staff-proposed until Kenneth locks",
        "legend": "solid = handoff · warm = takeover · dotted = feeds · rose ╳ = conflict · rose dash = wrong gate · labels = prints / gates",
    }


STYLE_CSS = """\
:root {
  --bg: #0c0b0a; --panel: #141210; --ink: #e8e0d4; --mute: #7a7268;
  --warm: #e0b87a; --brass: #c4a35a; --iron: #2a2622; --rose: #c45c5c; --amber: #d4a017;
  --mono: "IBM Plex Mono", ui-monospace, monospace;
  --sans: "IBM Plex Sans", system-ui, sans-serif;
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; background: var(--bg); color: var(--ink);
  font-family: var(--sans); font-size: 13px; min-height: 100vh; }
body.brain-map { padding: 0 24px 48px 24px; }
#head { height: 52px; display: flex; align-items: center; gap: 14px;
  border-bottom: 1px solid var(--iron); margin-bottom: 12px;
  letter-spacing: 0.16em; font-size: 13px; color: var(--warm); font-weight: 600; }
#head .live-off { margin-left: auto; letter-spacing: 0.08em; font-size: 11px; font-weight: 400;
  color: var(--mute); border: 1px solid var(--iron); padding: 4px 8px; border-radius: 3px;
  font-family: var(--mono); }
#stage { position: relative; max-width: 1320px; margin: 0 auto; }
#brain { width: 100%; height: auto; display: block; background: radial-gradient(ellipse at 50% 40%, #161310 0%, var(--bg) 70%);
  border: 1px solid var(--iron); border-radius: 6px; }
#legend { color: var(--mute); font-size: 11px; letter-spacing: 0.06em; margin: 10px 0 0; font-family: var(--mono); }
#tip { position: absolute; z-index: 8; max-width: 280px; background: var(--panel); border: 1px solid var(--amber);
  border-radius: 4px; padding: 10px 12px; font-size: 12px; color: var(--ink); pointer-events: none;
  box-shadow: 0 8px 24px rgba(0,0,0,.45); }
#tip.hidden { display: none; }
#tip .t-name { color: var(--warm); font-weight: 560; margin-bottom: 4px; }
#tip .t-owns { color: var(--mute); font-family: var(--mono); font-size: 11px; line-height: 1.4; }

.node-hit { cursor: pointer; }
.node-body { fill: var(--panel); stroke: var(--iron); stroke-width: 1.25; }
.node-body.locked { stroke: var(--brass); }
.node-body.proposed { stroke: var(--iron); stroke-dasharray: 4 3; }
.node-body.hot { stroke: var(--amber); stroke-width: 2; }
.node-label { fill: var(--ink); font-family: var(--sans); font-size: 14px; font-weight: 560; text-anchor: middle; }
.node-when { fill: var(--mute); font-family: var(--mono); font-size: 10px; text-anchor: middle; }
.pip { stroke-width: 1.25; }
.pip.locked { fill: var(--brass); stroke: var(--brass); }
.pip.proposed { fill: none; stroke: var(--iron); stroke-dasharray: 2 2; }

.edge { fill: none; stroke-linecap: round; }
.edge.handoff { stroke: var(--mute); stroke-width: 1.5; }
.edge.takeover { stroke: var(--warm); stroke-width: 1.5; }
.edge.feeds { stroke: var(--mute); stroke-width: 1; stroke-dasharray: 3 4; }
.edge.conflict { stroke: var(--rose); stroke-width: 2; }
.edge.refuse { stroke: var(--rose); stroke-width: 1.5; stroke-dasharray: 5 4; }
.edge.hot { filter: drop-shadow(0 0 3px var(--amber)); opacity: 1; }
.edge-label { fill: var(--mute); font-family: var(--mono); font-size: 10px; text-anchor: middle; }
.edge-label.conflict, .edge-label.refuse { fill: var(--rose); }
.conflict-x, .refuse-x { fill: var(--rose); font-family: var(--mono); font-size: 14px; text-anchor: middle; font-weight: 600; }
.refuse-x { font-size: 11px; }
.marker path { fill: var(--mute); }
.marker.takeover path { fill: var(--warm); }
.marker.feeds path { fill: var(--mute); }
.marker.conflict path { fill: var(--rose); }
.marker.refuse path { fill: var(--rose); }

#sheet { position: fixed; top: 0; right: 0; width: 380px; height: 100vh; background: #12100e;
  border-left: 1px solid var(--iron); padding: 20px 18px; overflow-y: auto; z-index: 20;
  transition: opacity .2s, transform .2s; }
#sheet.hidden { display: none; opacity: 0; transform: translateX(12px); }
#sheet-close { position: absolute; top: 10px; right: 12px; background: none; border: 0;
  color: var(--mute); font-size: 22px; cursor: pointer; }
#sheet-head .name { color: var(--warm); font-size: 16px; font-weight: 560; display: flex; align-items: center; gap: 10px; }
#sheet-head .when { color: var(--mute); font-family: var(--mono); font-size: 11px; margin-top: 6px; }
#sheet-head .owns { color: var(--mute); font-size: 12px; margin-top: 10px; line-height: 1.45; }
.block { margin-top: 20px; }
.block .kicker { letter-spacing: .14em; color: var(--mute); font-size: 11px; margin-bottom: 8px; }
.tag { font-family: var(--mono); font-size: 10px; letter-spacing: .08em; padding: 3px 7px; border-radius: 2px; white-space: nowrap; }
.tag-locked { color: var(--brass); border: 1px solid var(--brass); }
.tag-proposed { color: var(--mute); border: 1px dashed var(--iron); }
.rule-row, .mod-row, .scen-row, .edge-row { font-family: var(--mono); font-size: 11px; padding: 8px 0;
  border-bottom: 1px solid var(--iron); color: var(--mute); }
.rule-row { display: grid; grid-template-columns: auto 1fr; gap: 10px; align-items: start; }
.rule-row .rule-text { color: var(--ink); line-height: 1.45; }
.rule-row .rule-date { color: var(--mute); display: block; margin-top: 4px; font-size: 10px; }
.mod-row .sym, .edge-row .sym { color: var(--ink); }
.empty { color: var(--mute); font-family: var(--mono); font-size: 12px; }
#sheet-foot { margin-top: 24px; border-top: 1px solid var(--iron); padding-top: 14px;
  color: var(--mute); font-size: 11px; letter-spacing: .1em; cursor: pointer; }

#upgrade { max-width: 1320px; margin: 28px auto 0; background: var(--panel); border: 1px solid var(--iron);
  border-radius: 4px; padding: 14px 16px; opacity: .88; }
#upgrade .kicker { color: var(--mute); font-size: 11px; letter-spacing: .14em; margin-bottom: 6px; }
#upgrade .help { color: var(--mute); font-size: 12px; margin-bottom: 12px; }
.upgrade-card { border: 1px dashed var(--iron); border-radius: 3px; padding: 10px 12px; margin-bottom: 8px; }
.upgrade-card .title { color: var(--ink); font-family: var(--mono); font-size: 12px; margin-bottom: 4px;
  display: flex; align-items: center; gap: 10px; }
.upgrade-card .body { color: var(--mute); font-size: 12px; }
body.sheet-open #stage { margin-right: 400px; }
"""

APP_JS = r"""
(function () {
  const data = window.BRAIN_MAP;
  if (!data) return;
  const svg = document.getElementById("brain");
  const tip = document.getElementById("tip");
  const sheet = document.getElementById("sheet");
  const sheetBody = document.getElementById("sheet-body");
  const sheetClose = document.getElementById("sheet-close");
  const upgrade = document.getElementById("upgrade");
  const legend = document.getElementById("legend");
  const byId = {};
  (data.layers || []).forEach(function (l) { byId[l.id] = l; });

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function tagChip(tag) {
    return tag === "kenneth_locked"
      ? '<span class="tag tag-locked">Kenneth-locked</span>'
      : '<span class="tag tag-proposed">Staff-proposed</span>';
  }
  function mid(a, b) { return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }; }
  function edgePath(a, b, kind, waypoints) {
    if (waypoints && waypoints.length) {
      var d = "M " + a.x + " " + a.y;
      waypoints.forEach(function (p) { d += " L " + p[0] + " " + p[1]; });
      d += " L " + b.x + " " + b.y;
      return d;
    }
    const dx = b.x - a.x, dy = b.y - a.y;
    if (kind === "conflict") {
      const mx = (a.x + b.x) / 2 + 36, my = (a.y + b.y) / 2;
      return "M " + a.x + " " + a.y + " Q " + mx + " " + my + " " + b.x + " " + b.y;
    }
    if (kind === "takeover") {
      const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2 - 28;
      return "M " + a.x + " " + a.y + " Q " + mx + " " + my + " " + b.x + " " + b.y;
    }
    if (Math.abs(dx) < 8 || Math.abs(dy) < 8) {
      return "M " + a.x + " " + a.y + " L " + b.x + " " + b.y;
    }
    const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
    return "M " + a.x + " " + a.y + " Q " + mx + " " + (my + (dy > 0 ? 10 : -10)) + " " + b.x + " " + b.y;
  }
  function labelPoint(a, b, kind, waypoints) {
    if (waypoints && waypoints.length) {
      var midWp = waypoints[Math.floor((waypoints.length - 1) / 2)];
      return { x: midWp[0] + (kind === "refuse" ? 14 : 0), y: midWp[1] - 8 };
    }
    var m = mid(a, b);
    if (kind === "conflict") return { x: m.x + 18, y: m.y - 6 };
    return { x: m.x, y: m.y - 8 };
  }

  function draw() {
    const defs =
      '<defs>' +
      ['handoff','takeover','feeds','conflict','refuse'].map(function (k) {
        return '<marker id="arrow-' + k + '" class="marker ' + k + '" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z"/></marker>';
      }).join("") +
      "</defs>";
    let edges = '<g id="edges">';
    (data.edges || []).forEach(function (e, i) {
      const a = byId[e.from], b = byId[e.to];
      if (!a || !b) return;
      const d = edgePath(a, b, e.kind, e.waypoints);
      const m = mid(a, b);
      const lp = labelPoint(a, b, e.kind, e.waypoints);
      const marker = e.kind === "conflict" ? "" : ' marker-end="url(#arrow-' + e.kind + ')"';
      edges += '<path class="edge ' + e.kind + '" data-i="' + i + '" data-from="' + e.from + '" data-to="' + e.to + '" d="' + d + '"' + marker + "/>";
      edges += '<text class="edge-label ' + (e.kind === "conflict" || e.kind === "refuse" ? e.kind : "") + '" x="' + lp.x + '" y="' + lp.y + '">' + esc(e.label || "") + "</text>";
      if (e.kind === "conflict") {
        edges += '<text class="conflict-x" x="' + m.x + '" y="' + (m.y + 5) + '">╳</text>';
      }
      if (e.kind === "refuse" && e.waypoints && e.waypoints.length) {
        var wx = e.waypoints[Math.floor(e.waypoints.length / 2)][0];
        var wy = e.waypoints[Math.floor(e.waypoints.length / 2)][1];
        edges += '<text class="refuse-x" x="' + wx + '" y="' + (wy + 4) + '">╳</text>';
      }
    });
    edges += "</g>";

    let nodes = '<g id="nodes">';
    (data.layers || []).forEach(function (l) {
      const locked = l.tag === "kenneth_locked";
      const cls = locked ? "locked" : "proposed";
      const pip = locked ? "locked" : "proposed";
      const wide = (l.name || "").length > 12;
      const w = wide ? 150 : 140;
      const hx = w / 2;
      nodes +=
        '<g class="node-hit" data-seat="' + l.id + '" transform="translate(' + l.x + " " + l.y + ')">' +
          '<rect class="node-body ' + cls + '" x="' + (-hx) + '" y="-28" rx="18" ry="18" width="' + w + '" height="56"/>' +
          '<circle class="pip ' + pip + '" cx="' + (hx - 12) + '" cy="-18" r="4"/>' +
          '<text class="node-label" x="0" y="-2">' + esc(l.name) + "</text>" +
          '<text class="node-when" x="0" y="16">' + esc(l.when || "") + "</text>" +
        "</g>";
    });
    nodes += "</g>";
    svg.innerHTML = defs + edges + nodes;

    svg.querySelectorAll(".node-hit").forEach(function (g) {
      const id = g.getAttribute("data-seat");
      const layer = byId[id];
      g.addEventListener("mouseenter", function (ev) { showTip(layer, ev); highlight(id, true); });
      g.addEventListener("mousemove", function (ev) { placeTip(ev); });
      g.addEventListener("mouseleave", function () { hideTip(); highlight(id, false); });
      g.addEventListener("click", function () { openSheet(layer); });
    });
  }

  function highlight(id, on) {
    svg.querySelectorAll(".edge").forEach(function (p) {
      const hit = p.getAttribute("data-from") === id || p.getAttribute("data-to") === id;
      p.classList.toggle("hot", on && hit);
    });
    svg.querySelectorAll(".node-hit").forEach(function (g) {
      const body = g.querySelector(".node-body");
      body.classList.toggle("hot", on && g.getAttribute("data-seat") === id);
    });
  }

  function showTip(layer, ev) {
    tip.classList.remove("hidden");
    const verbs = (layer.prints || []).map(function (p) { return p.verb; }).join(" · ") || (layer.when || "");
    const shape = (layer.prints && layer.prints[0] && layer.prints[0].shape) || "";
    tip.innerHTML =
      '<div class="t-name">' + esc(layer.name) + "</div>" +
      '<div class="t-owns">' + esc(verbs) + "</div>" +
      (shape ? '<div class="t-owns" style="margin-top:6px">' + esc(shape) + "</div>" : "");
    placeTip(ev);
  }
  function placeTip(ev) {
    const stage = document.getElementById("stage").getBoundingClientRect();
    tip.style.left = (ev.clientX - stage.left + 14) + "px";
    tip.style.top = (ev.clientY - stage.top + 14) + "px";
  }
  function hideTip() { tip.classList.add("hidden"); }

  function openSheet(layer) {
    document.body.classList.add("sheet-open");
    sheet.classList.remove("hidden");
    const rules = layer.rules || [];
    const prints = layer.prints || [];
    const edges = (data.edges || []).filter(function (e) { return e.from === layer.id || e.to === layer.id; });
    function printsBlock(list) {
      if (!list.length) return '<div class="empty">none yet</div>';
      return list.map(function (p) {
        return (
          '<div class="rule-row">' +
            '<span class="tag tag-proposed">' + esc(p.verb) + "</span>" +
            '<div class="rule-text">' + esc(p.when || "") + "<br>" + esc(p.shape || "") + "</div>" +
          "</div>"
        );
      }).join("");
    }
    function rulesBlock(list) {
      if (!list.length) return '<div class="empty">none yet</div>';
      return list.map(function (r) {
        const date = r.kenneth_date ? '<span class="rule-date">Kenneth ' + esc(r.kenneth_date) + "</span>" : "";
        return '<div class="rule-row">' + tagChip(r.tag) + '<div class="rule-text">' + esc(r.text) + date + "</div></div>";
      }).join("");
    }
    function modsBlock(mods) {
      if (!mods || !mods.length) return '<div class="empty">none yet</div>';
      return mods.map(function (m) {
        return '<div class="mod-row"><span class="sym">' + esc(m.path) + "</span>" + (m.symbols ? " · " + esc(m.symbols) : "") + "</div>";
      }).join("");
    }
    function scenBlock(ids) {
      if (!ids || !ids.length) return '<div class="empty">none yet</div>';
      return '<div class="scen-row">' + ids.map(esc).join(" · ") + "</div>";
    }
    function edgeBlock(list) {
      if (!list.length) return '<div class="empty">none yet</div>';
      return list.map(function (e) {
        return '<div class="edge-row"><span class="sym">' + esc(e.from) + " → " + esc(e.to) + "</span> · " + esc(e.kind) + " · " + esc(e.label || "") + "</div>";
      }).join("");
    }
    const ownsMute = layer.owns
      ? '<div class="owns" style="opacity:.65">' + esc(layer.owns) + "</div>"
      : "";
    sheetBody.innerHTML =
      '<div id="sheet-head"><div class="name">' + esc(layer.name) + " " + tagChip(layer.tag) + '</div>' +
      '<div class="when">' + esc(layer.when || "") + "</div>" +
      ownsMute + "</div>" +
      '<div class="block"><div class="kicker">' + (["lock_pass","hang","outcome_writeback"].indexOf(layer.id) >= 0 ? "Gate" : "Decision prints") + '</div>' + printsBlock(prints) + "</div>" +
      '<div class="block"><div class="kicker">Rules</div>' + rulesBlock(rules) + "</div>" +
      '<div class="block"><div class="kicker">Code modules</div>' + modsBlock(layer.modules) + "</div>" +
      '<div class="block"><div class="kicker">Scenario seats</div>' + scenBlock(layer.scenarios) + "</div>" +
      '<div class="block"><div class="kicker">Edges</div>' + edgeBlock(edges) + "</div>" +
      '<div id="sheet-foot">Close</div>';
    document.getElementById("sheet-foot").addEventListener("click", closeSheet);
  }

function closeSheet() {
    sheet.classList.add("hidden");
    document.body.classList.remove("sheet-open");
  }

  function renderUpgrade() {
    const items = data.upgrades || [];
    const help = data.upgrade_help || "suggestions stay staff-proposed until Kenneth locks";
    const cards = items.length
      ? items.map(function (u) {
          return '<div class="upgrade-card"><div class="title">' + esc(u.title) + " " + tagChip("staff_proposed") + '</div><div class="body">' + esc(u.text) + "</div></div>";
        }).join("")
      : '<div class="empty">none yet</div>';
    upgrade.innerHTML = '<div class="kicker">Upgrade</div><div class="help">' + esc(help) + "</div>" + cards;
  }

  legend.textContent = data.legend || "";
  sheetClose.addEventListener("click", closeSheet);
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeSheet(); });
  draw();
  renderUpgrade();
})();
"""


def render_index(payload: dict) -> str:
    embedded = json.dumps(payload, ensure_ascii=False)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>THE MACHINE · brain map</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet" />
  <link rel="stylesheet" href="style.css" />
</head>
<body class="brain-map">
  <header id="head">
    <span>THE MACHINE · brain map</span>
    <span class="live-off">live orders off</span>
  </header>
  <main id="stage">
    <svg id="brain" viewBox="0 0 1320 760" role="img" aria-label="The Machine decision brain graph"></svg>
    <div id="tip" class="hidden"></div>
    <p id="legend"></p>
  </main>
  <aside id="sheet" class="hidden">
    <button type="button" id="sheet-close" aria-label="Close">×</button>
    <div id="sheet-body"></div>
  </aside>
  <section id="upgrade"></section>
  <script>window.BRAIN_MAP = {embedded};</script>
  <script src="app.js"></script>
</body>
</html>
"""


def main() -> int:
    payload = build_payload()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "brain-map.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (OUT / "style.css").write_text(STYLE_CSS, encoding="utf-8")
    (OUT / "app.js").write_text(APP_JS, encoding="utf-8")
    (OUT / "index.html").write_text(render_index(payload), encoding="utf-8")
    names = ", ".join(sorted(p.name for p in OUT.iterdir() if p.is_file()))
    print(f"wrote static/brain-map/ ({names})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
