# Machine scenario pass plan

Kenneth 2026-09-07. Full focus until every scenario below PASSes against the current here-build Machine. Live orders stay off. Do not call this done early.

## Goal

The Machine must react correctly on every locked Chart / Path / Size / Fail / Exit case. A true gap means the hung plan plus live tape should have produced a decision (buy, sit-out with why, wait with why, add-panic, sell, or flatten) and did not, or two seats claimed the same choice.

ANSEM trigger (facts only): hung first buy `0.203598`, met-band high `0.2015755`, `habit_ready` false, many reds, 5m volume spike. Old miss: silent watch “AD not met…”. Path RECUT: print that tags hung AD buy → Path may buy; Size owns volume. Covered by C3 / P10. H1 is Lock hang gate (do not hang until P1 fixed), not Path.

## Done bar

1. Scenario matrix below is complete (no blank expected outcome).
2. Decision owners table is locked (no overlapping Machine choices).
3. Every scenario has a major automated test that fails when the Machine is silent or wrong.
4. All those tests PASS on the here-build.
5. Lock scores process vs this plan. Helm tracks open / PASS / FAIL.
6. Master reports to Kenneth only when the full suite is green, or when a true gap needs his recut.

## Decision owners (no overlap)

| Seat | Owns | Does not own |
| --- | --- | --- |
| Chart | AD T/B/L, met band, met stays met, base_land_warn | Buy / sit / size / exit prices |
| Path | buy when print tags hung AD buy layer, or board panic; wait otherwise | Volume / grind wait (Size); met band (Chart); habit_ready / red habit match |
| Size | Which buy layers fill, real-volume gate, grind wait, AD-side scale, panic shares; optional 5m vol weigh | Met-band definition; Path tag / panic buy gate |
| Fail | Break of AD → add panic half (not flatten); grind fail / short-copy fail | Path tag buy; Size volume |
| Exit | Sell layers adapt, into-base, leftover, sideways-too-long when Reed number exists | Entry buy gates |
| Feed | Prints price/low/reds/volume/5m fields into Path and Size | Decisions |
| Machine log | Print only on decision change with why | Spam wait |

If two seats could both say buy or both say sit, that is a structure FAIL. Fix ownership before adding more tests.

## Known candidate gaps (prove or reject)

G1. ~~Hung AD buy above met-band → silent~~ — CLOSED: Path buys on tag; Size owns volume.
G2. ~~`habit_ready` false sit-block~~ — CLOSED by Kenneth 2026-09-07 Path RECUT: habit_ready / red habit are not buy gates; Path buys when AD layer tagged.
G3. ~~5m spike / habit fields ignored~~ — CLOSED Path RECUT: tag buy; optional 5m spike is Size weigh only, not Path sit.
G4. ~~Path says buy; Size cancels or waits; no clear why on the decision tape~~ — CLOSED (Lock 2026-09-08): Size why on row; sit-out on tape when at AD / board grind; off-AD Size miss mute matches sit-out-at-AD. Source: data/.grokbot/lock_decision_prints_G4_G7.json.
G5. ~~Fail under B blocked by Path sit (must still add panic)~~ — CLOSED: F1 + Fail add-panic tests PASS; Path sit does not veto Fail.
G6. ~~watch_only blocks even board panic (must sit with why)~~ — REJECTED AS GAP: correct locked behavior (sit with watch_only why). Not an Upgrade fix.
G7. ~~Exit into named base vs bounce map conflict~~ — CLOSED: unmet base clips bounce map; Exit sells into named base (E1). No dual Exit owners.

## Scenario matrix

Mark each: owner, expected Machine action, log line required (yes/no), test id, status (open / FAIL / PASS).

### Chart / met

| ID | Setup | Expected | Owner | Log | Status |
| --- | --- | --- | --- | --- | --- |
| C1 | Low enters met band first time | met true; state can move met; why met | Chart | yes | PASS |
| C2 | Bounce above band then re-enter | met stays met | Chart | no spam | PASS |
| C3 | Low never enters band; price tags hung buy above band | Path buy on tagged layer; Size owns volume | Path+Chart | yes | PASS |

### Path

Kenneth 2026-09-07 Path RECUT (locked): Path buys when print tags a hung AD buy layer (print ≤ empty/next AD-role layer) or board-wide panic. Size owns live volume and grind wait. habit_ready / red-count / faster-TF habit match are not buy gates. habit_ready false must not sit-block. Chart owns AD / met band. Fail under met AD still adds panic half. Optional require_5m_volume_spike is not a Path sit — Size may weigh 5m vol.

| ID | Setup | Expected | Owner | Log | Status |
| --- | --- | --- | --- | --- | --- |
| P1 | habit_ready false, at AD, reds 1–2, layer tagged, no board panic | Path buy on tag | Path | yes | PASS |
| P2 | habit_ready false, at AD, many reds mid/small TF, 5m spike, layer tagged | Path buy on tag | Path | yes | PASS |
| P3 | habit_ready false, board panic | buy | Path | yes | PASS |
| P4 | at AD, hung AD layer tagged (legacy habit fields ignored) | buy | Path | yes | PASS |
| P5 | at AD, first chosen red, layer tagged | buy | Path | yes | PASS |
| P6 | at AD, layer tagged, no legacy habit match | Path buy (Size may band-only / wait on quiet) | Path | yes | PASS |
| P7 | not at AD, not met, no layer tagged | wait; why clear | Path | no | PASS |
| P8 | require_5m_volume_spike true, 5m vol weak, layer tagged | Path buy; Size owns weak 5m (band-only / wait) — not Path sit | Path+Size | yes | PASS |
| P9 | require_5m_volume_spike true, 5m spike ok, layer tagged | buy | Path+Size | yes | PASS |
| P10 | ANSEM replay: hung layers, price through first buy, habit_ready false | Path buy on tag; Size fills with volume; never silent vague wait | Path+engine | yes | PASS |

### Size

| ID | Setup | Expected | Owner | Log | Status |
| --- | --- | --- | --- | --- | --- |
| S1 | Path buy, print through AD layer, real volume | fill that layer USD | Size | yes | PASS |
| S2 | Path buy, quiet volume at AD | Path take fills band-only / Size rule | Size | yes | PASS |
| S3 | Quiet early layers then late volume near B | cancel upper; 0.5× late | Size | yes | PASS |
| S4 | Board grind on, Path would buy, quiet | Size wait for volume; sit-out why | Size | yes | PASS |
| S5 | Path buy but no layer at/through print | wait with Size miss why | Size | yes | PASS |

### Fail

| ID | Setup | Expected | Owner | Log | Status |
| --- | --- | --- | --- | --- | --- |
| F1 | met, price under B, Path would sit, panic layers hung | add panic half | Fail | yes | PASS |
| F2 | price under B, not met yet | do not Fail-add | Fail | — | PASS |

### Exit

| ID | Setup | Expected | Owner | Log | Status |
| --- | --- | --- | --- | --- | --- |
| E1 | live, price into unmet base above B | sell into base / force fill | Exit | yes | PASS |
| E2 | under AD without board panic | defensive lower sells | Exit | yes | PASS |
| E3 | empty sell layers | no invent | Exit | — | PASS |
| E4 | candles_to_bounce passed at AD, no bounce | consider exit when Reed number set | Exit | yes | PASS |
| E5 | leftover above remaining cost on good bounce | full exit leftover | Exit | yes | PASS |

### Hang / Lock gates (process, then Machine)

| ID | Setup | Expected | Owner | Log | Status |
| --- | --- | --- | --- | --- | --- |
| H1 | AD buy P1 price > met-band high | Lock FAIL or warn before hang; do not hang until P1 fixed. Not Size. | Lock+Gauge | — | PASS |
| H2 | habit_ready false but Reed could fill habit fields from finished mets | Lock FAIL hang until habit filled or Kenneth accepts false | Lock+Reed | — | PASS |
| H3 | habit_ready / red fields missing | hang OK — not a Lock FAIL (Path RECUT) | Lock | — | PASS |

## Team roles

- **Helm**: keep this file’s Status column current; compile open / FAIL / PASS; no money score mixed into process score for this pass.
- **Lock**: decision-owner table; H1–H3; refuse overlapping Path/Size sentences; score patches vs AD desk rules.
- **Reed**: ANSEM and similar hung plays — were habit fields fillable from finished mets? Write facts into play files. Do not invent.
- **Gauge**: Layer prices vs Lock hang H1 band check; Size scenarios S1–S5 expected numbers.
- **Master**: owns this plan; here-build patches + major tests; one Kenneth bubble per real state change; no finish until suite green.

## Work order

1. Lock + Helm freeze decision owners (this file).
2. Reed + Lock close H2 on ANSEM (habit_ready true/false from history facts).
3. Gauge + Lock close H1 (layer vs band).
4. Master adds major tests for every open scenario ID (fail first).
5. Master patches only true gaps the failing tests prove.
6. Re-run full suite. Helm marks PASS.
7. Master reports Kenneth when all PASS, or asks one recut if a lock conflict remains.

## Language

Plain professional English. Say Machine / hung plan / buy layers / sell layers / Size layers. Do not say paper plan, paper decisions, or packs.

## Helm status compile

Updated: 2026-09-07 19:14 Asia/Manila. Process only. Live orders off. Money not scored here.

### Suite

**PASS** — `test_scenario_pass.py` 27 PASS / 0 FAIL / 0 SKIP. Related exit/path/size_volume 54 PASS. Matrix PASS.

### H1 process (Lock)

**PASS / held** — If AD-side P1 sits above met-band high, Lock FAIL or warn before hang; do not hang until P1 fixed. Written into Lock hang checks (Kenneth 2026-09-10). Size never owns this check. Gauge keeps P1 at or under band or flags Lock.

