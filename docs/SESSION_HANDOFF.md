# Session handoff — pick up here

**Last updated:** 2026-07-27  
**GitHub:** `chindonanKen/mexc-alert-bot` · branch `main`  
**Primary guide:** [AGENTS.md](../AGENTS.md)

This file is a **dated snapshot** of what shipped recently and what is still open so the next human/agent does not rediscover the same ground.

---

## Latest build (as of handoff)

Recent commits on `main` (newest first, approximate):

| Commit (prefix) | What |
|-----------------|------|
| `e3f48eb` | TSLA resolve also tries `TESLA_USDT` contract id |
| `80d7639` | Compact stock UI resolve (`TSLAUSDT`); bot always gets futures client when constructed |
| `409a450` | Heat board, velocity, volume, optional kline reds |
| `990af4e` | Step-down re-arm (cascade dumps; short min-gap) |
| `3498ac4` | Peak high→now drawdown + faster poll |
| `8f462d8` | Initial `AGENTS.md` |

**Always confirm live tip:** `git log -1 --oneline` on Mac and droplet after `git pull`.

---

## Production posture (owner)

- DigitalOcean droplet runs Docker service `mexc-bot` (compose project path often `~/mexc-alert-bot`).
- V3 features **enabled in prod `.env`** (futures + movers) after staged validation.
- Owner trades **panic downside dumps** (AD / average-drop style). Movers are the main edge tool; target alerts remain safety net.

### Recommended prod mover-related env (verify on droplet)

```bash
FEATURE_FUTURES_ALERTS=true
FEATURE_MOVER_SCANNER=true
MOVER_POLL_SECONDS=3          # or 5
MOVER_COOLDOWN_SECONDS=45     # NOT 1800 — that blocked cascade legs
MOVER_RECOVERY_PERCENT=3
MOVER_ENRICH_VELOCITY=true
MOVER_ENRICH_VOLUME=true
MOVER_ENRICH_KLINES=false     # user chose OFF for now; enable later for chart triage
MOVER_HEAT_AUTO=true
```

User explicit decision: **leave kline red-candle tags off for now**; turn on soon for faster chart overview before entries.

---

## What works today (user-confirmed)

- Movers + step-down cascade useful in live trading  
- Heat / velocity / volume enrichments deployed  
- **TSLA / stock perps** addable via `/af TSLA`, `/mw add f TSLA`, `/p f TSLA` after resolve fixes  
- Spot targets remain the non-negotiable path  

---

## How to deploy updates (droplet)

```bash
ssh <droplet>
cd ~/mexc-alert-bot    # adjust if different
git pull origin main
# edit .env only if new vars needed
docker compose up -d --build mexc-bot
docker logs --tail 80 mexc-alert-bot
```

Telegram smoke: `/s` · `/l` · `/p f TSLA` · `/mw` · wait for a real dump.

---

## Open / next (not built)

See also [FUTURE_STRATEGY_BOTS.md](FUTURE_STRATEGY_BOTS.md).

| Priority-ish | Idea | Notes |
|--------------|------|--------|
| User later | `MOVER_ENRICH_KLINES=true` | Code ready; env flip + restart |
| Discussed | Named mover **buckets** (per-group %/lookback) | Not started |
| Discussed | Bounce/reclaim after dump | Backlog |
| Discussed | Layer planner on cascade steps | Backlog |
| Discussed | Deep links / Telegram buttons | Backlog |
| Explicitly deferred | Full web UI | Commands + heat first |
| Optional | Kline high/low as **fire** input | Different from red tags |

---

## Trading context (for feature design)

Owner style (from AD agent + trade bible, high level):

- **Average Drop (AD)** mean-reversion: scale into dips expecting bounce  
- Prefers **sharp / panic** dumps with volume over slow grinds  
- Multi-layer scale-in; holds often day–week  
- Weaknesses to support with product: holding losers, early exit on good ADs, isolated “news” dumps vs market panic  

Do **not** auto-trade from this bot without a separate, explicit project decision.

Related local project (not this repo): `~/ad-theory-trading-agent` (chart AD analysis / learning).

---

## Agent checklist when starting a new session

1. Read **AGENTS.md** safety rules.  
2. `git status` / `git log -5 --oneline` / compare to `origin/main`.  
3. Skim this handoff + `.env.example` for current knobs.  
4. Run `make test` before and after behavior changes.  
5. Never commit `.env`; never break spot target stable_id crossing.  
6. After shipping: update **this file’s date**, commit table, and “open/next” if priorities change.

---

## Quick file map for movers

```
mexc_bot/movers/
  scanner.py   # fire loop, cascade, enrichment hooks, heat auto
  history.py   # peak_drawdown
  storage.py   # SQLite mover tables
  velocity.py  # PANIC/FAST/GRIND
  heat.py      # board snapshot + format
  klines.py    # consecutive reds (gated)
```

<!-- agents: search SESSION_HANDOFF or "pick up here" -->
