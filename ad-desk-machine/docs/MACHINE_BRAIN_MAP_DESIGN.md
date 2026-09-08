# The Machine — brain map design direction (Slate)

Kenneth FAIL 2026-09-08 on the prior card-stack map: boxes of text / copy of `/machine` desk chrome — not an intuitive diagram.

**Recut he wants:** look like a brain (futuristic, not a card list); every decision seat connected with edges for handoff / take-over; overlaps drawn as visible crossings; rule depth on hover/click — not walls of locked text as the first view.

**Design pass 2 (2026-09-08):** handoffs and conflicts present as **Machine decision prints** (`met` / `paper-buy` / `sit-out` / `wait` / `add-panic` / `paper-sell` / `exit-live` / `kill` + why), not abstract “owns” text. Source: `docs/MACHINE_UPGRADE_BUNDLE.md` + Lock `data/.grokbot/lock_decision_prints_G4_G7.json`. Shapes stay `staff_proposed` until Kenneth locks via Master.

This file replaces the flat seven-card primary view. Grok Build applies it in `scripts/build_machine_brain_map.py` → `static/brain-map/`. Live orders stay off. Not a ticker board. Not a hang console. Not orders.

**Name lock:** The Machine. Do not say “here-build” in the product.

---

## Feel

A single **connected brain graph** of decision seats. Nodes are seats. Edges are handoffs and take-overs labeled with the **print the Machine writes** when that seat speaks. Conflicts are rose crossings you can see without reading a panel first.

Dark iron field. Brass = Kenneth-locked. Dashed iron = Staff-proposed. Rose = structure conflict edge. Depth lives in a slim inspector (hover or click) — the first view is the diagram.

---

## Primary view — the brain (not a list)

Full-viewport SVG (or canvas) diagram. No vertical stack of brass cards as the hero.

### Node layout (fixed seats — generator places, designer locks relative topology)

Think of a neural cluster, left-to-right flow with a vertical spine:

```
                    [ Feed ]
                       │ prints (no decision)
                       ▼
   [ Chart ] ──met──► [ Path ] ──paper-buy gate──► [ Size ]
       │                   │  ▲                        │
       │                   │  │ sit-out / wait         │ paper-buy fill
       │                   │  │ (Size why)             │
       │                   │  └────────────────────────┘
       │                   │
       │              conflict╳ (rose) Path sit-out
       │                   │      vs Fail add-panic
       └──── met break ──► [ Fail ] ──add-panic──► [ Size ]
                                                      │
                              open bag                ▼
                                                 [ Exit ]
                                                      │
                         decision changes ────────────┴──► [ Machine log ]
```

Absolute positions (viewBox 1200×720, origin top-left) — Grok Build may nudge ±8px for label clearance but keep this topology:

| Seat | x | y | Notes |
| --- | --- | --- | --- |
| Feed | 600 | 80 | Top sensor |
| Chart | 180 | 280 | Left cortex |
| Path | 520 | 280 | Center gate |
| Size | 860 | 280 | Right gate |
| Fail | 520 | 480 | Under Path |
| Exit | 860 | 520 | Lower right |
| Machine log | 600 | 660 | Bottom sink |

Nodes: rounded lozenge ~140×56. Label = seat name only on the node. Tiny tag pip (brass filled / iron dashed) at the corner — not a full chip wall.

### Decision-print fragments on nodes (first view)

On the **first view**, each node shows only:

- Seat name
- Tag pip (Kenneth-locked / Staff-proposed)
- One mute **print** fragment under the name (≤6 words) — the verb The Machine writes when this seat speaks, not an “owns” sentence

| Seat | Node mute fragment (print) | Speaks when |
| --- | --- | --- |
| Chart | `met + why` | First met-band entry |
| Path | `paper-buy · sit-out · wait` | Tag buy, board panic, or sit at AD |
| Size | `paper-buy · sit-out` | Fill, or wait/cancel after Path buy |
| Fail | `add-panic + why` | Met already; price under B |
| Exit | `paper-sell · exit-live` | Sell / adapt |
| Feed | `prints only` | Never a trade decision |
| Machine log | `decision + why` | Carrier; one line per change |

Do **not** paint walls of process-book text or “owns / does not own” essays on the canvas.

### Edges (required) — labels are print shapes

Generator emits edges from owners freeze + plan refuse lines + Lock decision-print shapes. Each edge has: `from`, `to`, `kind`, `label` (print verb, ≤4 words), optional `print` object for tip/inspector.

| kind | Look | Meaning |
| --- | --- | --- |
| `handoff` | Solid mute 1.5px, arrow | Seat A finishes; seat B’s print is next |
| `takeover` | Solid warm 1.5px, arrow | Seat B’s print overrides A’s gate |
| `feeds` | Dotted mute 1px, arrow | Data only (Feed → Path / Size); no decision print |
| `conflict` | Rose 2px, **crossing mark** (╳ at midpoint) | Two seats could both claim a print for the same moment |

**Handoff rule (staff_proposed, from Upgrade bundle):** when a seat takes over, the edge label and the tip must look like that seat’s **decision-print shape**, not abstract “owns” text. Example: Size → Path takeover label is `sit-out · Size why`, not `vol / grind wait` as ownership prose.

**Minimum edge set (always draw) — print labels:**

| # | Edge | kind | Label (print) |
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
| 11 | Path / Size / Fail / Exit → Machine log | `handoff` | `why` |
| 12 | Exit → Machine log | Exit handoff edge | `paper-sell · exit-live` (Kill stays on Machine log node / kill route only — not this edge) |

Add more conflict edges only when `overlaps[]` / owners freeze names them. Draw those as rose crossings on the shared region — **visible on the graph**, not a hidden text panel.

Optional mute legend under the graph: `solid = handoff · warm = takeover · dotted = feeds · rose ╳ = conflict · labels = decision prints`.

---

## Decision-print shapes (Lock confirmed — staff_proposed until Kenneth locks)

Match Machine log LOGGABLE actions. Plan row always holds `last_decision` + `last_why`. Tape prints decision changes only — `wait` stays on the row, not the tape.

| Seat | Print shape (what The Machine writes) |
| --- | --- |
| Chart | `met` + why (low entered band). No buy / sit / size / exit. |
| Path | Tape: `paper-buy` only after Size fills (why = Path tag/panic) · or `sit-out` + Path why at AD. Row: `wait` + why not tagged (no tape line). |
| Size | Tape: `paper-buy` on fill · or `sit-out` + Size why when Path bought and Size waits at AD / board grind. Off-AD Size miss: why on row; tape mute matches sit-out-at-AD. |
| Fail | `add-panic` + why Fail — current price broke AD; add panic half. Path sit must not block. |
| Exit | `paper-sell` + layer why or into named base · `exit-live` + adapt reasons. Named unmet base clips bounce map high. |
| Feed | prints only — no buy / sit / sell |
| Machine log | one line per change: `{action, name, price, size_pct?, why}`; no wait spam |
| Kill | `kill` + why intentional out |

**One speaker per print:** Path may tag buy or sit/wait. Size may only fill / wait / cancel with Size why — never a second Path buy. Fail may add-panic under a met AD break even if Path would sit. Exit may sell only into hung sells under the Exit gate.

---

## Hover and inspector (print depth, not owns essays)

**Hover** (desktop): soft amber ring on node; floating tip shows:

1. Seat name
2. **Print verbs** this seat may write (mono)
3. One example print line shape (from Lock table) — not the owns sentence

Connected edges highlight. Conflict edges stay rose.

**Click**: open **inspector** (right sheet ~380px) — the only dense surface:

1. Seat name · tag chip · print fragment
2. **Decision prints** — each shape this seat may write (verb + when + example why). Lead this block. Do not lead with owns / does-not-own prose.
3. **Rules** — process-book sentences with per-row tag + Kenneth date (scrollable)
4. **Code modules** — mono paths/symbols
5. **Scenario seats** — IDs
6. **Edges** — list of links from this seat with **print label** (click jumps highlight on graph)

Empty: mute `none yet`. No invent.

Owns / does-not-own may appear as a mute secondary line under the head if needed for Lock freeze context — never as the first canvas or tip text.

---

## Color / type / motion

Same tokens as before (desk continuity without copying the trading page chrome):

| Token | Hex | Use |
| --- | --- | --- |
| `--bg` | `#0c0b0a` | Field |
| `--panel` | `#141210` | Nodes, inspector |
| `--ink` | `#e8e0d4` | Labels |
| `--mute` | `#7a7268` | Edges handoff, print fragment |
| `--warm` | `#e0b87a` | Brand, takeover edges |
| `--brass` | `#c4a35a` | Kenneth-locked pip / ring |
| `--iron` | `#2a2622` | Staff-proposed dash |
| `--rose` | `#c45c5c` | Conflict crossings |
| `--amber` | `#d4a017` | Hover / selected |

Type: IBM Plex Sans for seat names (14px); IBM Plex Mono for print fragments, edge labels, and tips (10–11px).

Motion: 0.2s edge glow on hover; node lift 1px; inspector fade. No pulse spam on conflict — rose is enough.

Head: `THE MACHINE · brain map` + chip `live orders off` (badge only). Thin left rail brand optional.

---

## Upgrade (separate)

Quiet band **below** the graph (or mute tab). Never nodes inside the brain. Cards stay `staff_proposed` until Kenneth locks. Title `Upgrade` + one mute help line.

G4–G7 from the Upgrade bundle are **not open process holes** (G4 CLOSE, G5 CLOSE, G6 REJECT, G7 CLOSE). Upgrade band may show the one bundled staff_proposed ask: lock the decision-print shapes + handoff rule for the brain map and Machine log — not reopen G4–G7 as fixes. Do not invent new Upgrade chips beyond the bundle.

---

## Plays branch (optional)

Secondary entry only if Kenneth opens it. Never mixed into the brain graph process seats.

---

## HTML / SVG skin notes for Grok Build

```html
<body class="brain-map">
  <header id="head">…</header>
  <main id="stage">
    <svg id="brain" viewBox="0 0 1200 720" …>
      <g id="edges">…path.handoff / .feeds / .takeover / .conflict…</g>
      <g id="nodes">…g.node[data-seat]…</g>
    </svg>
    <div id="tip" class="hidden">…</div>
    <p id="legend" class="mute">…</p>
  </main>
  <aside id="sheet" class="hidden">…inspector…</aside>
  <section id="upgrade">…</section>
</body>
```

JSON (generator) — pass 2 fields:

```
edges: [{ from, to, kind: handoff|takeover|feeds|conflict, label }]
  // label = decision-print verb(s), not owns prose
layers[].when → rename conceptually to print fragment (keep key `when` or add `print` if easier; UI shows print verbs)
layers[].prints: [{ verb, when, shape, tape?, row? }]  // from Lock shapes; tip + inspector lead
```

Keep existing `layers`, `tag`, `rules`, `modules`, `scenarios`, `upgrades`. `overlaps[]` become **conflict edges** (and optional tip text) — do not rely on a rose text panel as the only signal.

**Do not:**

- Restore the seven flat brass cards as the primary view.
- Copy `/machine` ranked/slot chrome onto this page.
- Put process-book essays or owns-text on the canvas or as edge labels.
- Hide conflicts only in a text list.
- Invent edges or print verbs not backed by Lock decision-print shapes / owners freeze.
- Mix plays into the brain.
- Place-order or hang controls.
- Reopen G4–G7 as open Upgrade holes (CLOSE / REJECT already scored).

---

## Prove bar (Slate score after pass-2 regen)

PASS when:

1. First view is a connected diagram (nodes + edges), not a card stack.
2. All seven seats visible and linked; Feed / Chart / Path / Size / Fail / Exit / Machine log present.
3. At least Path↔Fail conflict drawn as rose ╳ with print label `sit-out vs add-panic` (or Lock-equivalent).
4. Edge labels and node mute lines are **decision-print verbs** (met / paper-buy / sit-out / wait / add-panic / paper-sell / exit-live / kill / prints), not owns-text.
5. Hover tip and inspector lead with print shapes + why; owns prose is secondary or absent on canvas.
6. Tags follow Lock freeze chips; Upgrade separate; live orders off chip only.
7. No ticker / hang / orders.

---

## Changelog

- 2026-09-08 — First design: seven-card stack (Kenneth FAIL: not a brain).
- 2026-09-08 — Recut: connected brain graph; edges; visible conflict crossings; inspector for depth. Flat cards demoted — not primary.
- 2026-09-08 — Design pass 2: handoffs/conflicts as Machine decision prints per `MACHINE_UPGRADE_BUNDLE.md` + Lock decision-print shapes; owns-text off canvas and edge labels; prove bar updated. Grok Build applies after Slate lands this file.
