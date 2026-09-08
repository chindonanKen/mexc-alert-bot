# The Machine — Decision-print implement plan

Kenneth 2026-09-08 via Master: decision-print shapes + handoff rule from `docs/MACHINE_UPGRADE_BUNDLE.md` are good — implement all of them correctly.

**Owner of plan:** Helm. Chase until proven. Report to Master in Machine build.

**Hard limits:** two books (no ticker in process book); `live_orders_allowed` false; no invented sizes; process score and money score stay separate; no SQLite wipe. Money pass bar: expectancy, payoff, and tail versus Kenneth, not win rate.

**Source of shapes:** `docs/MACHINE_UPGRADE_BUNDLE.md` + `/workspace/ad-desk-machine/data/.grokbot/lock_decision_prints_G4_G7.json`.

---

## Prove bar (all must be true)

1. Lock wrote print shapes + handoff rule into the process book only (no ticker). File path named. G4 CLOSE, G5 CLOSE, G6 REJECT, G7 CLOSE still held.
2. Machine log + engine write exact print shapes: `met`, `paper-buy`, `sit-out`, `add-panic`, `paper-sell`, `exit-live`, `kill`. One speaker per print. Why required. `wait` on plan row only — not on tape.
3. Tests green: S4 (Size wait why), F1 (Fail add-panic), watch_only sit-with-why, E1 (into named base). Suite path + count named by Grok Build.
4. Slate paint PASS (or FAIL with broken item named) that brain-map graph edges match Lock’s print shapes after the process-book write.
5. Droplet brain map: Kenneth YES 2026-09-08 — port after Slate step 3 PASS (brain map + decision-print Machine prove).

---

## Ordered steps

| Step | Owner | Task | Done when |
| --- | --- | --- | --- |
| 1 | **Lock** | Write decision-print shapes + handoff rule into the process book only. Confirm G4–G7 stay CLOSE/REJECT. Hold every lock at once. No ticker. | Process-book path + PASS note filed under `data/.grokbot/`; G4–G7 reaffirm |
| 2 | **Grok Build** | Code only: Machine log + engine write those exact print shapes; one speaker per print; why required; wait on row not tape. Keep `live_orders_allowed` false. | Tests S4, F1, watch_only sit-with-why, E1 green; prove path named |
| 3 | **Slate** | After Lock book write (and any graph regen if needed): confirm brain-map edges match Lock print shapes. | Paint PASS or broken items listed |
| 4 | **Reed / Gauge** | Mute unless Lock names a chart-fact or layer mismatch while proving prints. | — |
| 5 | **Helm** | Chase each step; compile prove; report to Master in Machine build. | All prove-bar rows true or blocked with owner named |
| 6 | **Master / Kenneth** | Droplet brain map — wait Kenneth yes after here-build prove. | Kenneth yes |

---

## Status

| Step | Status |
| --- | --- |
| 1 Lock process-book write | PASS — see data/.grokbot/lock_decision_print_implement_PASS.json |
| 2 Grok Build engine/log | PASS — data/.grokbot/grok_build_decision_print_step2_PASS.json; 4/4 named tests green |
| 3 Slate paint | **PASS** sealed — exit→machine_log = paper-sell · exit-live; all edges Lock print verbs |
| 4 Reed / Gauge | MUTE |
| 5 Helm compile report | WAIT — after droplet port prove |
| 6 Droplet | **OPEN** — Kenneth YES; Grok Build port chased |

---

## Changelog

- 2026-09-08 — Helm opened implement plan after Kenneth lock via Master. Live orders off.
- 2026-09-08 — Lock PASS step 1: wrote Machine decision prints into ad-desk-rules; G4 CLOSE / G5 CLOSE / G6 REJECT / G7 CLOSE held.
- 2026-09-08 — Grok Build step 2 PASS: engine print shapes; S4 F1 watch_only E1 green (4). live_orders_allowed false. Droplet held.
- 2026-09-08 — Helm chased Slate step 3 after Grok Build PASS. Live orders off.
- 2026-09-08 — Slate step 3 FAIL: exit→machine_log edge kill vs paper-sell · exit-live. Grok Build one-label regen chased. Live orders off.
- 2026-09-08 — Kenneth YES droplet brain map. Exit edge label fixed on disk to paper-sell · exit-live. Slate re-score chased; port after PASS. Live orders off.
- 2026-09-08 — Slate step 3 PASS sealed. Grok Build droplet port chased (Kenneth YES). Live orders off.
