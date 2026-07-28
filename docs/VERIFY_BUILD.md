# Verify build — workflow & agent prompt

**Use this after every meaningful change** (learning, movers, news, coach, deploy).  
Goal: **nothing broken**, **learning is honest**, **no false info**.

Related: [V4_TRADING_ASSISTANT.md](V4_TRADING_ASSISTANT.md) · [AGENTS.md](../AGENTS.md) · [TRADING_STRATEGY.md](TRADING_STRATEGY.md)

---

## Quick local gate (every PR / session)

From repo root:

```bash
./scripts/verify_build.sh
# or:
make test
```

Must stay green:

| Suite | Guards |
|-------|--------|
| `test_crossing_and_remove_logic.py` | Spot targets, **stable_id** crossing — **never regress** |
| `test_v3_futures_and_movers.py` | Futures resolve, movers cascade, isolation |
| `test_mover_enrichment.py` | Velocity / heat |
| `test_learning_events.py` | Event log, labels, outcomes, journal, **alerts not deleted** |

---

## Recommended agent prompt (copy-paste)

Use this as the **first message** (or `/workflow verify-build`) when reviewing or finishing a change:

```text
You are verifying mexc-alert-bot after recent changes.

READ FIRST:
- AGENTS.md (safety rules)
- docs/V4_TRADING_ASSISTANT.md
- docs/VERIFY_BUILD.md
- docs/TRADING_STRATEGY.md (coach must follow rules; no inventing fills)

MANDATORY CHECKS (do all):
1. Run ./scripts/verify_build.sh (or make test with python3.11+).
2. Confirm git status — no .env secrets staged.
3. Grep: learning/coach/news must NOT contain DELETE FROM alerts or remove_alerts.
4. Confirm FEATURE_* defaults are false in .env.example for new flags.
5. Spot target invariant: PriceMonitor still keys _last_prices by (user_id, stable_id).
6. Learning honesty:
   - Events only written from real fires (mover_peak/step, target) or explicit journal.
   - Coach must not invent: fills, entry prices, confirmed hacks/delists without news module + stored news_events.
   - Empty log → coach says no events / no memory (see coach/engine.py).
7. If code changed monitor.py or storage.py: re-read crossing tests and explain why safe.
8. If news code exists: only DELIST/CLOSURE/HACK/SCAM with confirm threshold; unconfirmed never push.

OUTPUT format:
## Verdict: PASS | FAIL
## Tests
## Safety invariants
## Learning integrity (no false info)
## Deploy risk
## Required fixes (if any)

Do NOT mark PASS if crossing tests fail or learning deletes alerts.
Do NOT claim "agents learned X" without EventStore rows + labels as evidence.
```

---

## Staging Telegram checklist (after deploy)

Enable only on staging first:

```bash
FEATURE_LEARNING=true
# keep FEATURE_NEWS_MONITOR=false until V1.1 ready
```

| Step | Command / action | Expected |
|------|------------------|----------|
| 1 | `/s` | `learning=True`, targets still listed as count |
| 2 | `/l` | **All** previous target alerts present (same symbols/prices) |
| 3 | `/p BTC` · `/p f TSLA` | Prices resolve |
| 4 | `/mw` | Watchlist intact (if movers on) |
| 5 | Wait for real mover **or** inspect logs | `learning.event` in logs after fire |
| 6 | `/events` | New row: mover_peak/step or target |
| 7 | `/j took` | Labels that event; `/events` shows `took` |
| 8 | `/j skip SYMBOL` | Labels only matching symbol’s latest |
| 9 | `/brief` | Lists events + journal; no invented trades |
| 10 | `/coach panic` | Rule text; memory only if log has data |
| 11 | `/trade open f TEST 1` · `/trade list` · `/trade close` | Journal only; **`/l` unchanged** |
| 12 | After 15m+ | Outcomes may appear in DB (no spam required) |

**Fail conditions:**

- Any target missing from `/l` after learning work  
- Mass false fires  
- `/coach` invents “you bought at …” without journal  
- News push on analyst/price-take headlines (when news ships)  
- `FEATURE_LEARNING` on but `/events` never grows after confirmed mover fires  

---

## Production promote gate

1. Staging checklist green ≥ one real dump session.  
2. `make test` / `verify_build.sh` green on the **same commit**.  
3. Droplet: `git pull` → `docker compose up -d --build mexc-bot`.  
4. Immediately: `/s` · `/l` · `/p f TSLA` · `/mw`.  
5. Learning: enable `FEATURE_LEARNING` only when you want memory; can stay off if you only want sensors.  

Kill switch: set `FEATURE_LEARNING=false` (and news/voice off) → rebuild.

---

## What “agents learn” means (no false confidence)

| Claim is valid when | Claim is **false** if |
|---------------------|------------------------|
| `learning_events` has a row from a real fire | Coach says “you took X” with no `learning_labels.action=took` |
| Label exists for that event | Stats inferred from thin air |
| Outcome row after horizon + price sample | “Confirmed bounce +Y%” without `learning_outcomes` |
| News class + confirm rules (V1.1+) | Social rumor pushed as FATAL |

**Rule for agents/docs:** never say “the system learned that SYMBOL is good” without citing **event count + labels + outcomes** from the DB.

---

## Grok workflow

Project workflow: `.grok/workflows/verify-build.rhai`  

Run: `/workflow verify-build` or the workflow tool with `name: "verify-build"`.

---

## Post-change agent duties

After shipping:

1. Update [SESSION_HANDOFF.md](SESSION_HANDOFF.md) date + open/next.  
2. Keep this checklist in sync if new flags/commands appear.  

<!-- agents: search VERIFY_BUILD -->
