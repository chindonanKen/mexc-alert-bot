# The Machine — brain map plan (draft)

Kenneth ask 2026-09-08. Visual interactive overview of **The Machine** decision brain — not a ticker board.

**Status:** Kenneth approved 2026-09-08. Build open: generator `scripts/build_machine_brain_map.py` → `static/brain-map/`. Slate owns futuristic UI. Upgrade area = staff_proposed only. Prove locally first; droplet when green. `live_orders_allowed` false.

**Name lock:** say **The Machine**. Do not say “here-build” in the product or this plan’s user-facing lines.

**Hard limits:** `live_orders_allowed` stays false. Process book stays ticker-free. Two books never mix (process rules vs play files).

---

## Purpose

1. See Chart / Path / Size / Fail / Exit / Feed / Machine log ownership as layers Kenneth can open.
2. Catch rules he did not recut (staff-proposed vs Kenneth-locked).
3. Catch overlapping seats that make bad decisions (two seats claiming the same buy/sit/wait).
4. Refresh when The Machine changes (code, locked process rules, or decision-owner table).

Pass bar for this plan: Kenneth can approve **screen shape** and **sync method**. Nothing else ships until that yes.

---

## Screen shape (proposed — for Kenneth)

Elegant and simple. Interactive drill-in. Instant sync with The Machine.

### Top view — ownership layers (not tickers)

One stack of seats Kenneth already uses:

| Layer | What it owns (summary) | Drill-in shows |
| --- | --- | --- |
| Chart | AD T/B/L, met band, met stays met | Locked Chart sentences; which engine modules read them |
| Path | Buy when print tags hung AD buy layer, or board panic; wait otherwise | Path RECUT lock; wait/buy why shapes |
| Size | Which buy layers fill; volume / grind wait; AD-side scale; panic shares | Size locks; H1 band note |
| Fail | Break of AD → add panic half (not flatten) | Fail locks vs Path sit |
| Exit | Sell into hung sells; unmet bases; remaining-cost / bar-high fills | Exit gate checklist; unmet_bases shape |
| Feed | Prints price / low / reds / volume / 5m into Path and Size | Feed fields only — no decisions |
| Machine log | What The Machine wrote (enter / sit / wait / sell / kill why) | Log line shapes; no invent |

Each layer opens to: **locked rule text** · **code modules that enforce it** · **scenario seats that prove it** · **overlap warnings**.

### Side mark — Kenneth-locked vs not recut

Every rule node must show one of:

- **Kenneth-locked** — Master sent the recut sentence; Lock wrote it into the process book.
- **Staff-proposed / not recut** — appears in staff draft, play file, or code comment only; Kenneth has not locked it. Map must make this obvious so Kenneth can catch it.

*(Lock section filled below: locked process seats vs play files + tag rules.)*

### Overlap board

A short panel that lists pairs where two seats could both say buy or both say sit. Source of truth starts from `docs/MACHINE_SCENARIO_PASS_PLAN.md` decision-owner table (Chart / Path / Size / Fail / Exit / Feed). Structure FAIL if ownership is unclear. Fix ownership before more UI polish.

### Drill-in depth (optional nodes)

- Process book only at the top (no ticker).
- Play-file drill-in is a separate branch under “plays” if Kenneth opens it — never mixed into the process stack.
- Reed / Gauge: exit or entry facts appear only as a drill-in node under Exit / Size when the play file already has those facts. Do not invent UI for missing facts.

### What this is not

- Not a live ticker board.
- Not a hang console.
- Not order placement.
- Not a rewrite of Lock’s process book.


---

## Lock — locked process seats vs play files (2026-09-08)

Process book stays ticker-free. Two books never mix.

### Process seats (Kenneth-locked when Master sent the recut and Lock wrote the book)

| Seat | Process home | Map must show as Kenneth-locked when… |
| --- | --- | --- |
| Chart | `ad-desk-rules` Chart + full-history-first + discovery-first + HISTORY_TRUNCATED guard + Measure AD on MEXC | Sentence is in the Chart / history sections with a Kenneth date lock |
| Path | `ad-desk-rules` Path (2026-09-07 Path RECUT) | Path RECUT sentence: buy when current price tags a hung AD buy layer; Size owns volume/grind; habit_ready / red-count buy gates deleted |
| Size | `ad-desk-rules` Size (incl. H1 hang gate: P1 above met-band high → FAIL/warn before hang) | Dump-depth / panic Qi / 50-50 / volume-grind / H1 sentences are in Size |
| Fail | `ad-desk-rules` Fail | Break → add panic half; grind/short-copy fail lines are in Fail |
| Exit | `ad-desk-rules` Exit + `ad-exit-strategy` Exit gate workflow | unmet_bases_above_B ready gate; clip map high to nearest unmet big base; Lock checklist FAIL rules |
| Feed | Decision-owner table only (no trade rule book of its own) | Owners table says Feed prints fields; no decisions |
| Machine log | Staff / Master handoff + Machine log rule | Decision-change lines only; no spam wait |
| Staff handoff / token-light | `ad-desk-rules` Staff handoff + Token-light AD find | Mute FYI; named-field Lock score; one FAIL reason; Size after kenneth_T_B_locked |

Also locked process (not a trade seat, but map nodes): Pass bar (expectancy / payoff / tail vs Kenneth, not win rate); Auto AD find = Measure AD on MEXC only; spot-truncated → futures fill; history_guard HISTORY_TRUNCATED = hard BLOCKED.

### Play files (never in the process stack)

| Play home | What belongs here | Map treatment |
| --- | --- | --- |
| `data/.grokbot/plays/*.json` | One name, one TF, T/B/L, buy layers, panic layers, sell layers, hang status, outcomes | Separate **plays** branch only. Never mixed into Chart/Path/Size/Fail/Exit process layers |
| `data/.grokbot/*_exit_facts.json`, hang facts, walks | Reed facts for that name | Drill-in under Exit / Chart for that play only when the file already exists. No invent UI for missing facts |
| Scenario / Lock score files under `data/.grokbot/lock_*.json` | Process scores of a pack or hung list | Optional audit drill-in; not process law |

### How the map marks Kenneth-locked vs staff-proposed / not recut

**Kenneth-locked** — all of these are true:

1. Master sent Kenneth’s recut sentence to Lock (or Kenneth locked via Master in channel).
2. Lock wrote that exact intent into the process book (`ad-desk-rules` and/or `ad-exit-strategy`), ticker-free.
3. The map node cites the process-book section (and Kenneth date when present).

**Staff-proposed / not recut** — any of these:

1. Appears only in a play file, staff draft, chat proposal, or code comment.
2. In The Machine code or a plan doc but **not** yet written into the process book by Lock after a Master recut.
3. Teach-and-hold / pending lock (Master said hold until Kenneth locks) — map must show **not recut**, never as locked law.

**Map UI rule:** every rule node has exactly one tag: `kenneth_locked` or `staff_proposed`. Ambiguous = treat as `staff_proposed` until Lock confirms the process-book write. Overlap of two seats claiming the same buy/sit = structure FAIL panel (owners table), not a third tag.

### Overlap refuse (for the overlap board)

Path owns tag-buy / wait / board-panic buy. Size owns which layers fill and volume/grind. Chart owns met band. Fail owns add-panic under a met break. Exit owns sells. Feed owns prints only. If Path and Size both claim buy or both claim sit for the same print → structure FAIL; fix ownership before more map polish.

Source for owners freeze: `docs/MACHINE_SCENARIO_PASS_PLAN.md` + `data/.grokbot/lock_scenario_owners.json`.

---

## Sync method (Grok Build — 2026-09-08)

Goal: when The Machine changes, the map refreshes without hand-rewriting the site. No site ship until Kenneth approves this method.

### Inputs (read-only)

| Source | Path | Map use |
| --- | --- | --- |
| Decision owners + scenario seats | `docs/MACHINE_SCENARIO_PASS_PLAN.md` | Layer list, owns / does-not-own, overlap FAIL pairs, scenario IDs under each seat |
| Owners freeze file (if present) | `data/.grokbot/lock_scenario_owners.json` | Same ownership freeze Lock/Helm use |
| Process book (ticker-free) | Lock skills / process files: `ad-desk-rules`, `ad-exit-strategy` | Rule text nodes; `kenneth_locked` only when Lock’s write + Kenneth date is present |
| Engine modules | `machine/chart.py`, `path.py`, `size.py`, `exit.py`, `feeds.py`, `log.py`, `engine.py`, `loop.py`, `api.py` | “Code that enforces” links per seat (function / route names only — no invented rules) |
| Kill / board | `POST /api/machine/kill` in `machine/api.py` | Machine log / board pull node |
| Play drill-in (optional branch) | `data/.grokbot/plays/*.json`, `*_exit_facts.json` | Separate plays branch only when files exist; missing = show not ready, no invent |

Tag rule follows Lock’s section in this doc: each rule node is exactly `kenneth_locked` or `staff_proposed`. Code or plan text without a process-book write after Master’s recut = `staff_proposed`.

### Generate path

1. Script: `scripts/build_machine_brain_map.py` (to add after Kenneth approves this plan — not built yet).
2. Reads the inputs above. Parses the decision-owners markdown table and module file headers / known seat markers. Does not place orders. Does not invent rule text.
3. Emits:
   - `static/brain-map/brain-map.json` — layers, nodes, tags, module links, overlap pairs, scenario seat refs.
   - `static/brain-map/index.html` — one interactive page: stack of Chart / Path / Size / Fail / Exit / Feed / Machine log; click opens rule + modules + scenarios; side mark shows Kenneth-locked vs staff-proposed; overlap panel lists structure FAIL pairs.
4. Optional play branch: only if Kenneth opens “plays”; lists existing play / exit-fact files, never mixes into the process stack.

### Refresh trigger

| When | What runs |
| --- | --- |
| Local prove | After process-book or engine change: `python scripts/build_machine_brain_map.py` then open `static/brain-map/index.html` |
| Test gate | `pytest` stays the prove bar for decisions; map build is a separate command so a red test does not invent a green map |
| Git trunk | Commit regenerated `brain-map.json` + `index.html` with the code/rules change that caused them (same PR / same overnight ship) |
| Droplet (later) | Pull trunk; serve `static/brain-map/` from The Machine process or static mount. Restart not required for static files. `live_orders_allowed` stays false |

No hand-edit of the HTML after the first generator lands. If the map is wrong, fix the source (owners table, process book, or module) and regenerate.

### What Kenneth approves here

1. **Screen shape** — ownership stack + locked/not-recut marks + overlap panel + optional plays branch (Helm/Lock sections above).
2. **Sync method** — generate from The Machine tree via `scripts/build_machine_brain_map.py` → `static/brain-map/*`; refresh on source change; droplet later.

### Out of scope until yes

- Building the script or shipping the page.
- Live ticker board, hang console, or orders.
- Mixing play files into the process stack.

---

## Seats and schedule (Helm)

| Who | Deliverable for this plan | Due |
| --- | --- | --- |
| Helm | This doc path; chase; compile Lock + Grok Build into one draft for Master | Today 2026-09-08 |
| Lock | Locked process seats vs play files; how the map marks Kenneth-locked vs staff-proposed / not recut | Today |
| Grok Build | How the site is generated from The Machine tree so it updates when code/rules change | Today |
| Reed / Gauge | Note only if exit/entry facts need a drill-in node; no invent UI | As needed |
| Master | Carry approved plan to Kenneth; no site ship until Kenneth yes | After draft |

No site ship. Droplet later. Live orders stay off.

---

## Open asks (block plan PASS until filled)

1. **Lock** — DONE in this doc (section “Lock — locked process seats vs play files”).
2. **Grok Build** — DONE in this doc (section “Sync method”).
3. **Kenneth (via Master)** — approve or recut screen shape + sync method.

---

## Reed / Gauge drill-in notes (2026-09-08)

- **Reed:** under Exit, open `data/.grokbot/*_exit_facts.json` when it exists (`exit_gates`, `unmet_bases_above_B`, bounce, candles). Under Chart for that play, hang facts / T→B only when those files exist. Missing facts or `exit_gates.ready` false = show not ready; no node invent.
- **Gauge:** under Size, open play-file buy / panic layers when present (`data/.grokbot/plays/*_live.json`). Under Exit, open that play’s sell layers after Lock PASS (prices clipped to Reed unmet bases — do not invent from bounce % alone). Missing sells or not ready = show not ready. ZEC stays out until bounce exists.

---

## Related existing docs

- `docs/MACHINE_SCENARIO_PASS_PLAN.md` — decision owners and scenario seats.
- Lock process book (standing Chart / Path / Size / Fail / Exit files) — no ticker.
- Play files under `data/.grokbot/plays/` — one play = one file.

---

## Changelog

- 2026-09-08 — Helm draft opened; Lock and Grok Build seats chased; no site ship.
- 2026-09-08 — Lock: locked process seats vs play files + Kenneth-locked vs staff-proposed tag rules.
- 2026-09-08 — Grok Build: sync method — `scripts/build_machine_brain_map.py` → `static/brain-map/`; droplet later; no site ship until Kenneth yes.
- 2026-09-08 — Reed / Gauge: drill-in only notes for exit/entry facts.
- 2026-09-08 — Kenneth approved screen shape + sync; build seat opened (Slate UI, Upgrade area staff_proposed).
