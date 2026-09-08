# The Machine — Upgrade bundle

Kenneth 2026-09-08 beat 2. **Kenneth locked implement** via Master: decision-print shapes + handoff rule are good — implement correctly. Process-book write is Lock’s seat only.

**Status:** IMPLEMENTING. Plan: `docs/MACHINE_DECISION_PRINT_IMPLEMENT.md`. Source `ad-desk-machine/data/.grokbot/lock_decision_prints_G4_G7.json`.

**Hard limits:** `live_orders_allowed` false. Process score and money score stay separate. Pass bar for money: expectancy, payoff, and tail versus Kenneth, not win rate.

---

## Decision-print shapes (for diagram + Machine log)

**Lock confirmed 2026-09-08** (staff_proposed for Upgrade/diagram until Kenneth locks via Master). Match Machine log LOGGABLE actions. Plan row always holds `last_decision` + `last_why`. Tape prints decision changes only — `wait` stays on the row, not the tape.

| Seat | When it speaks | Print shape (what The Machine writes) |
| --- | --- | --- |
| Chart | First met-band entry | `met` + why (low entered band). No buy/sit/size/exit. |
| Path | Tag buy, board panic, or sit at AD | Tape: `paper-buy` only after Size fills (why = Path tag/panic) · or `sit-out` + Path why at AD. Row: `wait` + why not tagged (no tape line). |
| Size | Fill or wait/cancel after Path buy | Tape: `paper-buy` on fill · or `sit-out` + Size why when Path bought and Size waits at AD / board grind (`Size grind wait` / volume / no AD layer). Off-AD Size miss: why on row; tape mute matches sit-out-at-AD. |
| Fail | Met already; price under B | `add-panic` + why Fail — current price broke AD; add panic half. Path sit must not block. |
| Exit | Sell / adapt | `paper-sell` + layer why or into named base · `exit-live` + adapt reasons. Named unmet base clips bounce map high; Exit sells into that base. |
| Feed | Never a trade decision | prints only — no buy/sit/sell |
| Machine log | Carrier | one line per change: `{action, name, price, size_pct?, why}`; no wait spam |
| Kill | Play done / out | `kill` + why intentional out |

**Handoff rule (staff_proposed):** when a seat takes over, the edge on the graph and the tape line must look like that seat’s print shape, not abstract “owns” text.

---

## G4–G7 hold vs process book (Lock fills)

Source: `docs/MACHINE_SCENARIO_PASS_PLAN.md` known gaps.

| Id | Candidate hole | Lock score (PASS open / CLOSE fixed / REJECT not a hole) | Broken lock named if PASS | Notes |
| --- | --- | --- | --- | --- |
| G4 | Path says buy; Size cancels or waits; no clear why on the decision tape | **CLOSE** | — | Size why on row; sit-out on tape at AD / board grind (S4). Off-AD Size miss mute matches sit-out-at-AD. Not a process hole. |
| G5 | Fail under B blocked by Path sit (must still add panic) | **CLOSE** | — | F1 + Fail add-panic tests PASS. Path RECUT: Fail under met AD still adds panic. |
| G6 | watch_only blocks even board panic (must sit with why) | **REJECT** | — | Correct locked behavior: sit with `watch_only` why. Not an Upgrade fix candidate. |
| G7 | Exit into named base vs bounce map conflict | **CLOSE** | — | Unmet base clips bounce map; Exit sells into named base (E1). No dual Exit owners. |

---

## One bundled staff_proposed solution (Helm sealed)

**Goal:** structure of decisions so Path / Size / Fail / Exit cannot conflict on the same print; every takeover writes a clear decision print. G4–G7 are not open process holes (CLOSE / REJECT). This bundle asks Kenneth to lock the decision-print shapes and handoff rule for the brain map + Machine log, not to reopen G4–G7 as fixes.

### What staff proposes Kenneth lock (via Master)

1. **One speaker per print.** Path may tag buy or sit/wait. Size may only fill / wait / cancel with Size why — never a second Path buy. Fail may add-panic under a met AD break even if Path would sit. Exit may sell only into hung sells under the Exit gate; named unmet base clips bounce map high.
2. **Why required.** Any sit / wait / cancel / fill / sell / add-panic / kill must name the seat and the why in the decision-print shapes above. Plan row always holds `last_decision` + `last_why`. Machine log tape prints decision changes only — `wait` stays on the row, not the tape.
3. **Handoff on the brain map.** When a seat takes over, the graph edge and the tape line must show that seat’s print shape (not abstract “owns” text). Upgrade chips stay `staff_proposed` until Kenneth locks.
4. **G4–G7 closed as process holes.** G4 CLOSE (Size why on row; sit-out on tape at AD / board grind). G5 CLOSE (Fail add-panic not blocked by Path sit). G6 REJECT (watch_only sit-with-why is correct locked behavior). G7 CLOSE (unmet base clips bounce map; Exit sells into named base). No Reed/Gauge chase for G7.
5. **Tests that stay green:** S4 Size wait why; F1 Fail add-panic; watch_only board-panic sit-with-why; E1 into named base. No new open-hole tests for G4–G7.
6. **Money score stays separate.** No expectancy / payoff / tail claim from process PASS alone. Score money only from finished fills versus Kenneth.

### Out of scope for this bundle

- Writing new sentences into the process book (Kenneth via Master only).
- Live orders (`live_orders_allowed` false).
- Invented Exit/Size prices.
- Treating G6 watch_only as a profitability fix.

---

## Chase order (after compile)

1. ~~Lock — score G4–G7; confirm decision-print shapes.~~ DONE.
2. ~~Helm — compile one staff_proposed suggestion.~~ DONE this file.
3. Master — show Kenneth this file for lock or recut of print shapes + handoff rule only.
4. Slate — design pass 2 (decision-print presentation on the brain graph). Unblocked.
5. Grok Build — redraw SVG from Slate + Lock-confirmed print shapes after Slate PASS.
6. Droplet brain map — hold until Kenneth yes.

---

## Changelog

- 2026-09-08 — Helm opened draft; Lock seat chased on G4–G7; decision-print shapes named (staff_proposed).
- 2026-09-08 — Lock: shapes confirmed (Machine log LOGGABLE verbs). G4 CLOSE, G5 CLOSE, G6 REJECT, G7 CLOSE. Source `ad-desk-machine/data/.grokbot/lock_decision_prints_G4_G7.json`. Reed/Gauge not needed for G7.
- 2026-09-08 — Helm COMPILED one staff_proposed solution. Slate design pass 2 unblocked. Live orders off.
- 2026-09-08 — Kenneth locked implement (via Master). Helm opened `docs/MACHINE_DECISION_PRINT_IMPLEMENT.md`. Live orders off.
