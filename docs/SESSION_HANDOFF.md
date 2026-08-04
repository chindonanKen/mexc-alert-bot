# Session handoff — pick up here

**Last updated:** 2026-08-04 (AD Agent plan locked — full focus P1 case factory)  
**GitHub:** `chindonanKen/mexc-alert-bot` · branch `main`  
**Primary guides:** [START_HERE.md](../START_HERE.md) · [AGENTS.md](../AGENTS.md) · **[AD_AGENT_PLAN.md](AD_AGENT_PLAN.md)** · [TRADING_STRATEGY.md](TRADING_STRATEGY.md)

This file is a **dated snapshot** so the next human/agent (including a **new MacBook + new Grok session**) does not rediscover the same ground.

---

## Product baseline (what works — trust this)

Owner assessment accepted **2026-08-03**, then Learning V1 shipped same day.

| Surface | Status |
|---------|--------|
| **Positions** | **Solid** — exchange money truth (futures open/history, spot balances, discrete closes, layers) |
| **Targets / movers** | **Solid** — shared DB with Telegram sensors/alarms |
| **Overview** | **Partial** — Needs you · targets · movers · positions · agent memory strip |
| **Learning** | **V1 live** — you teach, agent student; **not** a coach product |
| **Voice** | **Turn-based beta** — fluent Voice 2.0 not shipped |

### Two products (do not blur)

| Product | Role |
|---------|------|
| **Telegram alarm bot** | Sensors + push for targets/movers — **leave as-is** |
| **AD Desk** | Positions + Learning V1 teach + voice tools + Overview |

Shared SQLite is plumbing. Owner: `DESK_USER_ID=8630949601`.

---

## Learning V1 (shipped 2026-08-03) — teach the agent

**Model:** You teach · agent is student · no coach product. Super-Agent theater removed.

| Surface | Role |
|---------|------|
| **Learning** nav | Pending · Teach (trade-first) · What I’ve learned (+ delete) · Recent · Ask agent |
| **Overview** | Needs you (pending only) · Agent memory strip |
| **Voice** | `what_have_you_learned`, `teach`, pending, fires/trades, `agent_ask`, `delete_lesson` |

Key rules:
- Money truth: `teach_ok` / `LEARNING_TEACH_SINCE` for $ claims  
- Teach **bound to selected trade** (open or closed); open then close = new lesson chapters  
- Chips include **`ad_met` / `ad_missed`** + process set  
- Module: `mexc_bot/webapi/learning_v1.py`  
- Roadmap API: `GET /api/roadmap` (desk UI)

---

## AD Desk ops (still true)

- **No sound-file upload.** Mic needs HTTPS secure context.
- Dual entry: `./scripts/desk_up.sh` (HTTP) · `./scripts/desk_https_up.sh` (HTTPS mic)
- Requires `.env`: `DESK_API_TOKEN`, `DESK_USER_ID`, `XAI_API_KEY` (voice)
- DO firewall: **8080** + **443**

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

Recent commits on `main` (newest first, approximate — confirm with `git log`):

| Commit (prefix) | What |
|-----------------|------|
| `fc17c2d` | Delete lesson (UI + API + voice tool) |
| `fa5a4ab` | Voice longer end-silence; closed chips + AD met/missed |
| `a5a53f6` | Learning trade-first teach flow with bound context |
| `787d998` | Learning V1: teach the agent, not a coach product |
| `7c0f900` | Handoff: platform baseline; wipe coach product plan |
| `35ae2f3`…`1bf99d0` | `LEARNING_TEACH_SINCE`, money_truth / teach_ok seal |
| `f4504a7`…`4ba7039` | Spot balances authority; ignore GOONC dust |
| `eb9d769`… | Futures open layers + history closed cycles |

Older movers/V3 history still on main; tip moves — always: `git log -1 --oneline` after `git pull`.

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
| **Canonical plan** | **[AD_AGENT_PLAN.md](AD_AGENT_PLAN.md)** — path to fully autonomous AD agent |
| **Shipped (P0)** | Money truth · Learning V1 teach · multi-set movers · Binance CMS delist · turn-based voice |
| **ACTIVE** | **P1 Case factory core shipped** — freeze on fire/teach + Learning snapshot UX; next = P2 decide+log |
| **Later** | P2 decide+log → P3 grade → P4 AD policy → P5 paper → P6 advise (gated) → P7 gated live |
| **Desk** | Small edits only unless phase-blocking |
| **Owner teach** | Light, high-quality on real trades while P1 builds — do **not** block on more P0 soak |
| **Playbook** | [TRADING_STRATEGY.md](TRADING_STRATEGY.md) |
| **Constitution** | [AGENTS.md](../AGENTS.md) · roadmap `GET /api/roadmap` |
| **Ops** | [DROPLET_OPS.md](DROPLET_OPS.md) · `ssh mexc-droplet` · `./scripts/droplet.sh` |
| **Verify** | `make test` · [VERIFY_BUILD.md](VERIFY_BUILD.md) |
| Backlog (not focus) | Voice 2.0 · desk visual Imagine · TG buttons · mover buckets |
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
