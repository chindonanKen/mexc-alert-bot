# Isolated dump agent (async news specialist)

**Status:** Implemented  
**Flag:** `FEATURE_ISOLATED_DUMP_AGENT` (default **false**; **true** in staging example)

## Goal

When a coin dumps **hard and alone** (not market-wide panic), run a **specialist** that checks multi-CEX delists + exploit feeds and sends a **second** Telegram message: news-related or not.

**Does not delay** mover/target alerts. Fire path only `queue.put_nowait`.

## Trigger (extreme only)

All must pass (`investigators/triggers.py`):

| Rule | Default |
|------|---------|
| Drop magnitude | ≥ max(8%, 1.6 × user threshold) |
| Velocity | PANIC or FAST (GRIND rejected) |
| Isolation | heat dumping count ≤ 2 (and not ≥ half watchlist) |
| Cooldown | 900s per user+market+symbol |

## Pipeline

```text
Mover FIRE (unchanged HTML message + buttons)
    └─ maybe_enqueue (non-blocking)
            │
DelistRadar (background 180s) → delist_cache (Binance, OKX, Bybit, MEXC)
            │
Worker → cache lookup + Rekt match → investigation row
       → Telegram: 🔍 ISOLATED DUMP CHECK
            │
Outcome bridge → learning_outcomes → source_expertise weights
```

## Learning (cause → effect)

Table `source_expertise`: per `(source, kind)`  
- hits, confirmed_moves, false_alarms, weight  
- Updated when investigation has `event_id` and learning outcome exists at the **4h** horizon (`ISOLATED_OUTCOME_HORIZON_SECONDS=14400` by default)

Commands: `/inv` recent checks · `/inv sources` learned weights

## Safety

- Never touches `alerts` delete path  
- Soft-fail all network  
- Queue drops if full (logs warning)  
- Separate message — does not edit the mover fire  

<!-- agents: search ISOLATED_DUMP_AGENT -->
