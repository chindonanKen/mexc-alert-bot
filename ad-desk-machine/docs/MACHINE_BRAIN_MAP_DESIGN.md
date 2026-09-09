# The Machine — brain map design direction (Slate)

Kenneth FAIL 2026-09-08 on the prior card-stack map: boxes of text / copy of `/machine` desk chrome — not an intuitive diagram.

**Recut he wants:** look like a brain (futuristic, not a card list); every decision seat connected with edges for handoff / take-over; overlaps drawn as visible crossings; rule depth on hover/click — not walls of locked text as the first view.

**Design pass 2 (2026-09-08):** handoffs and conflicts present as **Machine decision prints** (`met` / `paper-buy` / `sit-out` / `wait` / `add-panic` / `paper-sell` / `exit-live` / `kill` + why), not abstract “owns” text. Print shapes are Kenneth-locked in the process book (2026-09-08).

**Design pass 3 (2026-09-09):** Kenneth yes — add **three neurons only** on the **same** live Brain (Tailscale `http://100.90.109.34:8787/brain-map`). No second Brain. No DecisionLoop redraw. No invented names. Live orders off.

New neurons (exact names): **Lock PASS** · **Hang** · **Outcome writeback**.

KEEP neurons (unchanged seats): Chart · Path · Size · Fail · Exit · Feed · Machine log. **Fail stays one neuron** (do not split this week). Keep Path↔Fail rose conflict ╳.

Staff path (process, not extra neurons): data → Reed facts → Gauge layers → **Lock PASS** → **Hang** → write **Outcome writeback** on the same record → Machine log. Reed and Gauge are **not** graph neurons this week — they feed Lock PASS through Chart / Size / Exit facts and layers already on the KEEP graph.

Slate owns design docs only. Grok Build codes. Pike reviews after this draft. Slate does not touch the droplet.

**Name lock:** The Machine. Do not say “here-build” in the product.

---

## Feel

A single **connected brain graph** of decision seats plus the hang gate rail. KEEP cortex stays left/center and readable. The three new neurons form a clear **gate rail** on the right so Lock → Hang → Outcome cannot be skipped without a visible wrong-gate mark.

Dark iron field. Brass = Kenneth-locked. Dashed iron = Staff-proposed. Rose = conflict or wrong-gate refuse. Depth in slim inspector. First view = diagram.

---

## Primary view — the brain (not a list)

Full-viewport SVG diagram. No vertical stack of brass cards as the hero. Same site as today’s Brain — widen the canvas; do not spawn a second page.

### Node layout (KEEP + gate rail)

```
                         [ Feed ]
                            │ prints
                            ▼
        [ Chart ] ──met──► [ Path ] ──paper-buy gate──► [ Size ] ──layers──► [ Lock PASS ]
            │                 │  ▲                          │                    │
            │                 │  │ sit-out · Size why       │                    │ PASS
            │                 │  └──────────────────────────┘                    ▼
            │            conflict╳ (rose)                                   [ Hang ]
            │            sit-out vs add-panic                                    │
            └─ met break ► [ Fail ] ──add-panic──► [ Size ]                      │ hung record
                                                 │                               ▼
                                                 └─ open bag ► [ Exit ] ► [ Outcome writeback ]
                                                      paper-sell · exit-live         │
                                                                                     ▼
                                                              all decision why ─► [ Machine log ]
```

Absolute positions — viewBox **1320×760** (KEEP cluster unchanged; gate rail on the right). Grok Build may nudge ±8px for label clearance; keep topology.

| Seat | x | y | Notes |
| --- | --- | --- | --- |
| Feed | 600 | 80 | Top sensor — KEEP |
| Chart | 180 | 280 | Left cortex — KEEP |
| Path | 520 | 280 | Center gate — KEEP |
| Size | 860 | 280 | Right KEEP gate |
| Fail | 520 | 480 | Under Path — **one neuron** — KEEP |
| Exit | 860 | 520 | Lower KEEP — KEEP |
| **Lock PASS** | **1120** | **240** | Gate rail 1 — after layers ready |
| **Hang** | **1120** | **400** | Gate rail 2 — only after Lock PASS |
| **Outcome writeback** | **1120** | **560** | Gate rail 3 — same hung record |
| Machine log | 700 | 700 | Bottom sink — KEEP; also from Outcome writeback |

Nodes: rounded lozenge ~140×56 (gate-rail nodes may use ~150×56 if the name needs it). Label = seat name only. Tiny tag pip (brass / iron dashed) — not a chip wall.

KEEP nodes must stay at the same relative cluster so the Path↔Fail rose ╳ and Size↔Path takeover remain readable. Do not shrink KEEP labels to fit the rail.

### Decision-print / gate fragments on nodes (first view)

| Seat | Node mute fragment | Speaks when |
| --- | --- | --- |
| Chart | `met + why` | First met-band entry |
| Path | `paper-buy · sit-out · wait` | Tag buy, board panic, or sit at AD |
| Size | `paper-buy · sit-out` | Fill, or wait/cancel after Path buy |
| Fail | `add-panic + why` | Met already; price under B |
| Exit | `paper-sell · exit-live` | Sell / adapt |
| Feed | `prints only` | Never a trade decision |
| Machine log | `decision + why` | Carrier; one line per change |
| **Lock PASS** | `PASS · FAIL` | Layers + facts scored; hang-ready or not |
| **Hang** | `hung · watch-only` | Master hang after Lock PASS only |
| **Outcome writeback** | `outcome on record` | Write outcome on the **same** hung record |

Do **not** paint process-book essays or owns-text on the canvas.

### Edges — KEEP prints + gate rail + wrong gates

| kind | Look | Meaning |
| --- | --- | --- |
| `handoff` | Solid mute 1.5px, arrow | Next legal seat / print |
| `takeover` | Solid warm 1.5px, arrow | Seat B overrides A’s gate |
| `feeds` | Dotted mute 1px, arrow | Data only |
| `conflict` | Rose 2px + ╳ | Two seats claim the same print moment |
| `refuse` | Rose **dashed** 1.5px + small ╳ | **Wrong gate** — illegal skip (must stay visible) |

**KEEP minimum edges (unchanged labels):**

| # | Edge | kind | Label |
| --- | --- | --- | --- |
| 1 | Feed → Path | `feeds` | `prints` |
| 2 | Feed → Size | `feeds` | `prints` |
| 3 | Chart → Path | `handoff` | `met` |
| 4 | Chart → Fail | `handoff` | `met break` |
| 5 | Chart → Exit | `handoff` | `AD / bases` |
| 6 | Path → Size | `handoff` | `paper-buy gate` |
| 7 | Size → Path | `takeover` | `sit-out · Size why` |
| 8 | Fail → Size | `handoff` | `add-panic` |
| 9 | Path ↔ Fail | `conflict` | `sit-out vs add-panic` — rose ╳ |
| 10 | Size → Exit | `handoff` | `open bag` |
| 11 | Path / Size / Fail → Machine log | `handoff` | `why` |
| 12 | Exit → Machine log | `handoff` | `paper-sell · exit-live` |

**Gate-rail edges (new):**

| # | Edge | kind | Label |
| --- | --- | --- | --- |
| 13 | Size → Lock PASS | `handoff` | `layers ready` |
| 14 | Exit → Lock PASS | `handoff` | `sell layers` |
| 15 | Chart → Lock PASS | `handoff` | `facts` (Reed facts land via Chart walk — no Reed neuron) |
| 16 | Lock PASS → Hang | `handoff` | `PASS` |
| 17 | Hang → Outcome writeback | `handoff` | `same record` |
| 18 | Outcome writeback → Machine log | `handoff` | `outcome` |

**Wrong gates (always draw — visible refuse):**

| # | Edge | kind | Label |
| --- | --- | --- | --- |
| R1 | Size → Hang | `refuse` | `skip Lock` |
| R2 | Lock PASS → Outcome writeback | `refuse` | `skip Hang` |
| R3 | Hang → Machine log | `refuse` | `skip writeback` |

No direct Size→Hang, Lock→Outcome, or Hang→Machine log as `handoff`. Those paths exist only as rose dashed `refuse` so a wrong gate is obvious on the first view.

**Refuse path geometry (Pike FAIL 2026-09-09 — required):** R1–R3 must **never** cross gate-rail node bodies (Lock PASS / Hang / Outcome writeback).

- Draw refuse edges on a **right refuse rail** at **x ≥ 1220** (viewBox still 1320×760), **or** use an elbow/quadratic whose mid control clears the nearest gate-rail node by **≥ 28px** (half of the 56px node height) in both x and y.
- **R2** (Lock PASS → Outcome writeback) must not share the gate-rail column `x = 1120` as a straight vertical through Hang. Route R2 as: Lock PASS → (1220, 240) → (1220, 560) → Outcome writeback — a U on the refuse rail that **bypasses Hang**.
- **R1** (Size → Hang): elbow right of Size then into Hang; do not cut through Lock PASS. Example: Size (860,280) → (1220, 280) → (1220, 400) → Hang (1120, 400).
- **R3** (Hang → Machine log): elbow down/left on or outside the refuse rail; do not cut Outcome writeback. Example: Hang (1120,400) → (1220, 400) → (1220, 700) → Machine log (700, 700).
- Legal handoffs **13–18** stay on the gate-rail column (short solid arrows between Lock PASS → Hang → Outcome writeback). KEEP Path↔Fail rose ╳ unchanged.
- Generator: `refuse` paths use explicit `d` waypoints or `rail_x: 1220`; do not auto-line-center between endpoints for R1–R3.

Optional mute legend: `solid = handoff · warm = takeover · dotted = feeds · rose ╳ = conflict · rose dash = wrong gate · labels = prints / gates`.

---

## Decision-print shapes (Kenneth-locked in process book)

Match Machine log LOGGABLE actions. Plan row always holds `last_decision` + `last_why`. Tape prints decision changes only — `wait` stays on the row, not the tape.

| Seat | Print shape (what The Machine writes) |
| --- | --- |
| Chart | `met` + why (low entered band). No buy / sit / size / exit. |
| Path | Tape: `paper-buy` only after Size fills (why = Path tag/panic) · or `sit-out` + Path why at AD. Row: `wait` + why not tagged (no tape line). |
| Size | Tape: `paper-buy` on fill · or `sit-out` + Size why when Path bought and Size waits at AD / board grind. |
| Fail | `add-panic` + why Fail — current price broke AD; add panic half. Path sit must not block. |
| Exit | `paper-sell` + layer why or into named base · `exit-live` + adapt reasons. |
| Feed | prints only — no buy / sit / sell |
| Machine log | one line per change: `{action, name, price, size_pct?, why}`; no wait spam |
| Kill | `kill` + why intentional out (Machine log node — not the Exit→log edge) |
| Lock PASS | Row/tape gate mark: `PASS` or `FAIL` + Lock why (hang-ready or not). No invent prices. |
| Hang | Record state: hung (watch-only until Kenneth unlocks live). Only after Lock PASS. |
| Outcome writeback | Write outcome fields on the **same** hung record; then Machine log may print the change. |

**One speaker per print** on KEEP seats unchanged. Gate rail: Lock PASS speaks before Hang; Hang before Outcome writeback; Outcome writeback before the outcome line hits Machine log.

---

## Hover and inspector

**Hover:** amber ring; tip = seat name + print/gate verbs + one example shape. Refuse edges stay rose when a connected wrong gate is hot.

**Click inspector (~380px):**

1. Seat name · tag chip · fragment
2. **Decision prints** / **Gate** (for Lock PASS / Hang / Outcome writeback) — lead this block
3. Rules · Code modules · Scenario seats · Edges (including refuse links)

Empty: `none yet`. No invent.

---

## Color / type / motion

| Token | Hex | Use |
| --- | --- | --- |
| `--bg` | `#0c0b0a` | Field |
| `--panel` | `#141210` | Nodes, inspector |
| `--ink` | `#e8e0d4` | Labels |
| `--mute` | `#7a7268` | Handoff, fragments |
| `--warm` | `#e0b87a` | Brand, takeover |
| `--brass` | `#c4a35a` | Kenneth-locked pip |
| `--iron` | `#2a2622` | Staff-proposed dash |
| `--rose` | `#c45c5c` | Conflict + refuse |
| `--amber` | `#d4a017` | Hover / selected |

Type: IBM Plex Sans seat names (13–14px); IBM Plex Mono fragments and edge labels (10–11px).

Motion: 0.2s edge glow; no pulse spam on refuse — rose dash + ╳ is enough.

Head: `THE MACHINE · brain map` + `live orders off` badge only. Standing shared URL for humans: Tailscale `http://100.90.109.34:8787/brain-map` (never localhost as the standing link).

---

## Upgrade (separate)

Quiet band below the graph. Empty unless Lock/Master open a new staff_proposed chip. Do not reopen G4–G7. Do not put Lock PASS / Hang / Outcome writeback in Upgrade — they are graph neurons now.

---

## Plays branch (optional)

Secondary only if Kenneth opens it. Never mixed into process neurons.

---

## HTML / SVG skin notes for Grok Build

```html
<body class="brain-map">
  <header id="head">…</header>
  <main id="stage">
    <svg id="brain" viewBox="0 0 1320 760" …>
      <g id="edges">…handoff / feeds / takeover / conflict / refuse…</g>
      <g id="nodes">…g.node[data-seat]…</g>
    </svg>
    <div id="tip" class="hidden">…</div>
    <p id="legend" class="mute">…</p>
  </main>
  <aside id="sheet" class="hidden">…</aside>
  <section id="upgrade">…</section>
</body>
```

JSON ids (exact — no invent): `lock_pass`, `hang`, `outcome_writeback` plus existing `chart` `path` `size` `fail` `exit` `feed` `machine_log`.

```
edges[].kind: handoff | takeover | feeds | conflict | refuse
layers[].when: mute fragment
layers[].prints: [{ verb, when, shape, tape?, row? }]
```

Lock freeze tags: add the three new seats when Lock files them. Until freeze lands, paint pip as Kenneth-locked for the implement Kenneth already yes’d, or follow Lock freeze the hour it updates — do not invent a third tag.

**Do not:**

- Add a second Brain page or DecisionLoop redraw.
- Split Fail this week.
- Add Reed or Gauge as neurons this week.
- Hide wrong-gate skips (must draw `refuse` edges R1–R3).
- Restore card stack; copy `/machine` chrome; owns-text on canvas.
- Invent product names or seat nicknames.
- Place-order controls; live orders.
- Droplet work from this design seat (Grok Build ports after Pike + Master).

---

## Prove bar (Slate score after pass-3 regen)

PASS when:

1. First view is one connected diagram on the same Brain site — not a card stack, not a second Brain.
2. All **ten** neurons present: KEEP seven + Lock PASS + Hang + Outcome writeback. Fail is still one node.
3. Path↔Fail rose ╳ `sit-out vs add-panic` still drawn.
4. Gate rail edges 13–18 present with print/gate labels (not owns-text).
5. Wrong gates R1–R3 drawn as rose dashed `refuse` with ╳ (skip Lock / skip Hang / skip writeback); refuse paths on right rail x≥1220 or elbows clearing gate-rail nodes by ≥28px — R2 must not draw through Hang.
6. KEEP decision-print edge labels held (including Exit→Machine log = `paper-sell · exit-live`).
7. Tip/inspector lead with prints/gates; live orders off badge only; no ticker / hang console / order controls.

---

## Changelog

- 2026-09-08 — First design: seven-card stack (Kenneth FAIL: not a brain).
- 2026-09-08 — Recut: connected brain graph; edges; visible conflict crossings; inspector for depth.
- 2026-09-08 — Design pass 2: decision-print edge/node labels; Exit→log = `paper-sell · exit-live`.
- 2026-09-09 — Design pass 3: three neurons Lock PASS · Hang · Outcome writeback on same Tailscale Brain; KEEP seven + Fail one + rose ╳ held; wrong-gate refuse edges; ready for Pike.
- 2026-09-09 — Pike FAIL fix: refuse R1–R3 offset to right refuse rail (x≥1220) / elbows; R2 bypasses Hang; legal handoffs 13–18 and KEEP rose ╳ unchanged.
