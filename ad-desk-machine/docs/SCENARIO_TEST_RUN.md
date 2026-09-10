# Scenario test run

Timestamp: 2026-09-07 19:01 Asia/Manila  
Live orders: off  
Product patch: none (fail-first only)

## Command

```bash
cd /workspace/ad-desk-machine && python -m pytest tests/test_scenario_pass.py -v --tb=short
```

## Summary

21 collected — **17 passed**, **3 failed**, **1 skipped**

## PASS

- `test_C1_low_enters_met_band_first_time`
- `test_C2_bounce_above_band_met_stays_met`
- `test_P1_habit_ready_false_at_ad_few_reds_sits`
- `test_P2_habit_ready_false_at_ad_many_reds_5m_spike_sits_with_why`
- `test_P3_habit_ready_false_board_panic_buys`
- `test_P4_habit_ready_true_at_ad_chosen_tf_reds_match_buys`
- `test_P5_habit_ready_true_first_chosen_red_faster_match_buys`
- `test_P6_habit_ready_true_at_ad_no_match_sits`
- `test_P7_not_at_ad_not_met_waits_clear_why`
- `test_P8_require_5m_spike_weak_sits`
- `test_P9_require_5m_spike_ok_buys`
- `test_S1_path_buy_real_volume_through_layer_fills`
- `test_S2_quiet_at_ad_path_take_band_only`
- `test_S3_late_volume_near_B_half_scale`
- `test_S4_board_grind_quiet_size_wait_why`
- `test_S5_path_buy_no_layer_at_print_size_miss_why`
- `test_F1_met_under_B_path_would_sit_adds_panic_half`

## FAIL (assertion text)

All three share the same gap: ANSEM-class hung buy P1 `0.203598` above met band high `0.2015755`, `habit_ready` false, many reds + 5m spike → Machine returns wait with vague why only and no sit-out/decision naming buy-layer vs band.

### `test_C3_price_tags_hung_buy_above_band_must_name_layer_or_band`

```
AssertionError: Machine stayed silently on watch with vague why only: action='wait' why='AD not met or current price not at AD'; expected sit/wait why naming buy-layer vs band (or habit_ready), or sit-out/decision on the Machine log for the tagged layer
```

### `test_P10_ansem_replay_tagged_layer_above_band_not_silent`

```
AssertionError: Machine stayed silently on watch with vague why only: action='wait' why='AD not met or current price not at AD'; expected sit/wait why naming buy-layer vs band (or habit_ready), or sit-out/decision on the Machine log for the tagged layer
```

### `test_H1_lock_hang_gate_p1_above_band_do_not_hang_until_fixed`

```
AssertionError: Machine stayed silently on watch with vague why only: action='wait' why='AD not met or current price not at AD'; expected sit/wait why naming buy-layer vs band (or habit_ready), or sit-out/decision on the Machine log for the tagged layer
```

## SKIPPED (open)

- `test_E1_live_price_into_unmet_base_sells` — Exit into unmet base not written yet

## Next patch targets (do not ship until tests green)

1. **Path + engine (C3 / P10)** — When a hung buy layer is tagged by the print but price/low never entered the met band (layer above `band_high`), Path may buy on tag (Size owns volume). H1 is separate: Lock hang gate before hang (Kenneth 2026-09-10), not Size.
2. Leave Size S1–S5 alone (already PASS). P2 already PASS at AD.
3. Then E1 + remaining open matrix (F2, E2–E5, H3).

---

# Scenario test run — hung-layer tape patch

Timestamp: 2026-09-07 19:03 Asia/Manila  
Live orders: off  
Product patch: `machine/engine.py` — when Path returns wait “AD not met or current price not at AD” but print tags a still-empty/next AD buy layer above met-band high, engine overrides why to name buy layer vs band (+ habit_ready) and appends sit-out on the Machine log. No paper-buy. Path `evaluate_path` unchanged.

## Commands

```bash
cd /workspace/ad-desk-machine && python -m pytest tests/test_scenario_pass.py -v --tb=short
cd /workspace/ad-desk-machine && python -m pytest tests/test_path.py tests/test_size_volume.py tests/test_chart.py -q --tb=line
```

## Summary

- `tests/test_scenario_pass.py`: 21 collected — **20 passed**, **0 failed**, **1 skipped**
- Related (`test_path` + `test_size_volume` + `test_chart`): **25 passed**

## Newly PASS (was FAIL)

- `test_C3_price_tags_hung_buy_above_band_must_name_layer_or_band`
- `test_P10_ansem_replay_tagged_layer_above_band_not_silent`
- `test_H1_lock_hang_gate_p1_above_band_do_not_hang_until_fixed`

## Still SKIPPED (open)

- `test_E1_live_price_into_unmet_base_sells` — Exit into unmet base not written yet

## Prior PASS retained

C1, C2, P1–P9, S1–S5, F1 still PASS.

---

# Scenario test run — Exit / Fail / H3 pass

Timestamp: 2026-09-07 19:07 Asia/Manila  
Live orders: off  
Product patches:
- `machine/engine.py` — Fail add-panic requires `was_met` (already met before this print); first under-B that first-enters met band is Chart met, not Fail-add (F2).
- `machine/engine.py` — hang_play Lock FAIL (ValueError) when `habit_ready` true and both `chosen_tf_reds_into_met` and `faster_tf_reds_at_low` missing (H3).

## Commands

```bash
cd /workspace/ad-desk-machine && python -m pytest tests/test_scenario_pass.py -v --tb=short
cd /workspace/ad-desk-machine && python -m pytest tests/test_exit.py tests/test_path.py tests/test_size_volume.py -q --tb=line
```

## Summary

- `tests/test_scenario_pass.py`: 27 collected — **27 passed**, **0 failed**, **0 skipped**
- Related (`test_exit` + `test_path` + `test_size_volume`): **54 passed**

## Newly PASS (was open / skipped)

- `test_E1_live_price_into_unmet_base_sells` (un-skipped; Exit into unmet base)
- `test_F2_under_B_not_met_yet_does_not_fail_add`
- `test_E2_under_ad_without_board_panic_defensive_lower_sells`
- `test_E3_empty_sell_layers_no_invent`
- `test_E4_candles_to_bounce_passed_at_ad_no_bounce_considers_exit`
- `test_E5_leftover_above_remaining_cost_on_good_bounce_full_exit`
- `test_H3_habit_ready_true_missing_red_fields_lock_fail`

## Prior PASS retained

C1–C3, P1–P10, H1, S1–S5, F1 still PASS. habit_ready false sit unchanged.

## FAIL / SKIP

None.

## Remaining open

- H1 Lock process warn-before-hang (separate from Machine react PASS).
- No Kenneth recut needed for this pass.

---

# Scenario test run — Kenneth Path RECUT

Timestamp: 2026-09-07 19:28 Asia/Manila  
Live orders: off  

## Product patches

- `machine/path.py` — Path buys when `tagged_hung_ad_buy` or board panic; wait otherwise. Deleted habit_ready / red-count / faster-TF habit match / require_5m_volume_spike as Path buy/sit gates.
- `machine/engine.py` — sets `tagged_hung_ad_buy` when print ≤ empty/next AD buy; removed habit_ready hang Lock FAIL (H3); Fail under already-met AD always adds panic half; Path-tag fills AD-only (Fail/board panic may fill panic); removed above-band sit-out override (Path buys on tag).
- Tests + `docs/MACHINE_SCENARIO_PASS_PLAN.md` Path section rewritten to RECUT.
- Process book `ad-desk-rules/SKILL.md` Path section already matched RECUT; how-master-talks has no habit_ready buy-gate text.

## Commands

```bash
cd /workspace/ad-desk-machine && .venv/bin/python -m pytest \
  tests/test_scenario_pass.py tests/test_path.py tests/test_size_volume.py tests/test_chart.py -v --tb=short
```

## Summary

- Collected **50** — **50 passed**, **0 failed**, **0 skipped**

## Behavior under RECUT

- habit_ready false no longer sit-blocks (P1/P2/P10/C3 buy on tag).
- require_5m_volume_spike is Size weigh only (P8).
- H3 hang succeeds without red habit fields.
- Fail under met AD still adds panic half (F1).
- Live orders remain false.

## FAIL / SKIP

None.
