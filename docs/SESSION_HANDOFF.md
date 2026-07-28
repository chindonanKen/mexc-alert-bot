# Session handoff — pick up here

**Last updated:** 2026-07-28  
**GitHub:** `chindonanKen/mexc-alert-bot` · branch `main`  
**Primary guides:** [START_HERE.md](../START_HERE.md) · [AGENTS.md](../AGENTS.md)

This file is a **dated snapshot** so the next human/agent (including a **new MacBook + new Grok session**) does not rediscover the same ground.

---

## Machine / workspace move (Mac mini → MacBook)

| Fact | Detail |
|------|--------|
| **Dev machine** | Owner moving exclusive Grok Build work to **MacBook** |
| **Sessions** | Grok chats **do not** follow the machine — only **git + docs** do |
| **Repo source of truth** | `https://github.com/chindonanKen/mexc-alert-bot` (`main`) |
| **Live bot** | Stays on **DigitalOcean** Docker (`~/mexc-alert-bot` typical) |
| **Laptop role** | Clone → code with Grok → `git push` → droplet pull/rebuild |

**On MacBook:**

```bash
git clone https://github.com/chindonanKen/mexc-alert-bot.git ~/mexc-bot
cd ~/mexc-bot && make test
# New Grok session: open ~/mexc-bot, read START_HERE.md + AGENTS.md + this file
```

Do **not** rely on copying `.grok` session folders from the mini unless you explicitly want old transcripts for reference.

---

## Latest build (as of handoff)

Recent commits on `main` (newest first, approximate):

| Commit (prefix) | What |
|-----------------|------|
| `9382793` | AGENTS + SESSION_HANDOFF docs refresh |
| `e3f48eb` | TSLA resolve also tries `TESLA_USDT` contract id |
| `80d7639` | Compact stock UI resolve (`TSLAUSDT`); bot gets futures client when client exists |
| `409a450` | Heat board, velocity, volume, optional kline reds |
| `990af4e` | Step-down re-arm (cascade dumps; short min-gap) |
| `3498ac4` | Peak high→now drawdown + faster poll |

**Always confirm tip:** `git log -1 --oneline` after `git pull` (MacBook and droplet).

---

## Production posture (owner)

- DigitalOcean: Docker service `mexc-bot` (path often `~/mexc-alert-bot`).
- V3 **on** in prod `.env` (futures + movers).
- Edge: **panic downside** (AD-style scale-in). Movers = main tool; target alerts = safety net.

### Recommended prod mover env (verify on droplet)

```bash
FEATURE_FUTURES_ALERTS=true
FEATURE_MOVER_SCANNER=true
MOVER_POLL_SECONDS=3
MOVER_COOLDOWN_SECONDS=45     # NOT 1800
MOVER_RECOVERY_PERCENT=3
MOVER_ENRICH_VELOCITY=true
MOVER_ENRICH_VOLUME=true
MOVER_ENRICH_KLINES=false     # OFF for now; enable later for red-candle tags
MOVER_HEAT_AUTO=true
```

---

## What works (user-confirmed)

- Movers + step-down cascade useful live  
- Heat / velocity / volume enrichments  
- **TSLA** via `/p f TSLA`, `/af TSLA`, `/mw add f TSLA` after resolve fixes  
- Spot targets must remain intact  

---

## Deploy (droplet)

```bash
ssh <droplet>
cd ~/mexc-alert-bot
git pull origin main
docker compose up -d --build mexc-bot
docker logs --tail 80 mexc-alert-bot
```

Smoke: `/s` · `/l` · `/p f TSLA` · `/mw`.

---

## Open / next

| Status | Item |
|--------|------|
| **Next (owner)** | **Major alarm-system upgrade** — plan carefully; keep V1 spot path safe |
| Later | `MOVER_ENRICH_KLINES=true` (code ready) |
| Backlog | Named mover buckets, bounce/reclaim, layer planner, TG buttons |
| Deferred | Full web UI; Buzz as primary chat (Telegram stays primary for push alerts) |
| Separate bots | [FUTURE_STRATEGY_BOTS.md](FUTURE_STRATEGY_BOTS.md) |

### Buzz (context only)

Owner asked whether **Buzz** (Block agent chat) could replace Telegram. **Feasible for dual-notify / agent workspace; not recommended as primary** for mobile panic alerts (poll-based inbound, early product). Telegram remains production path. See prior plan discussion in Grok history if needed; no Buzz code in this repo yet.

---

## Trading context (feature design)

- **AD (Average Drop)** mean-reversion; prefer **sharp panic** dumps + volume  
- Scale in layers; holds often day–week  
- Product should support discipline (cascade legs, heat board, not spam)  

Related (not this repo): `~/ad-theory-trading-agent`.

---

## Agent checklist (every new session)

1. Read **START_HERE.md** → **AGENTS.md** → **this file**.  
2. `git status` / `git log -5 --oneline` / sync with `origin/main`.  
3. `make test` before/after behavior changes.  
4. Never commit `.env`; never break spot `stable_id` crossing.  
5. After shipping: update this file’s **date**, commit table, and open/next.  

---

## Quick movers file map

```
mexc_bot/movers/
  scanner.py   # fire loop, cascade, enrichments, heat auto
  history.py   # peak_drawdown
  storage.py   # SQLite mover tables
  velocity.py  # PANIC/FAST/GRIND
  heat.py      # board
  klines.py    # red tags (gated)
```

<!-- agents: search SESSION_HANDOFF or START_HERE -->
