# V1 complete — trading assistant (sensors + memory + UX)

**Status:** Shipped in code (flags default OFF except staging example)  
**Date:** 2026-07-28  
**Next product phase:** **V2** multi-device desk UI (see [ASSISTANT_UX.md](ASSISTANT_UX.md))

---

## What V1 is

Telegram-first **living trading assistant** on top of MEXC sensors:

| Area | Capability |
|------|------------|
| **Sensors** | Spot/futures one-shot targets, downside movers (peak+step), heat/velocity/volume, optional klines |
| **Memory** | Event log, labels, outcomes, journal, MEXC fill sync (opt-in) |
| **UX** | Fire buttons Took/Skip/Later, bounce taps, `/desk`, plain text, optional voice |
| **News** | Fatal-class only (delist/hack/closure/scam), anti false-flag |
| **Coach** | Rule-based `/coach` + `/brief` + memory stats |
| **Safety** | Flags OFF by default; staging isolation; never delete `alerts` from learning |

---

## Feature flags (all default false in `.env.example`)

| Flag | Role |
|------|------|
| `FEATURE_FUTURES_ALERTS` | `/af` futures targets |
| `FEATURE_MOVER_SCANNER` | Movers + `/mw` |
| `FEATURE_LEARNING` | Event log, buttons, desk, coach, outcomes |
| `FEATURE_NEWS_MONITOR` | Fatal news watcher |
| `FEATURE_VOICE` | Voice notes → STT → intents |
| `FEATURE_MEXC_PRIVATE_READ` | Read-only myTrades → journal_fills |

---

## Staging recommended set

```bash
FEATURE_FUTURES_ALERTS=true
FEATURE_MOVER_SCANNER=true
FEATURE_LEARNING=true
FEATURE_NEWS_MONITOR=true
FEATURE_VOICE=false          # true only with VOICE_STT_API_KEY
FEATURE_MEXC_PRIVATE_READ=false  # true only with read-only key + user id
```

---

## How Kenneth uses V1 day-to-day

1. Movers/targets fire with **buttons** → tap Took / Skip  
2. Type `brief` / `coach` / `took` when useful  
3. `/desk` for home  
4. Optional: enable MEXC read-only so fills appear without `/trade`  
5. Optional: fatal news push on watchlist names  

Power commands remain for levels (`/a`, `/mw`, …).

---

## Explicitly deferred to V2+

- Full multi-device web/PWA desk  
- Fluent multi-turn LLM coach with tool use as default  
- Auto-trading / order placement  
- Named mover buckets, bounce-only bots, full OI bots  

---

## Verify

```bash
./scripts/verify_build.sh
# or make test
```

<!-- agents: search V1_COMPLETE -->
