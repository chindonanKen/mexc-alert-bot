# Staging environment — test new builds while prod keeps running

**Preferred host:** DigitalOcean **droplet** (see [DROPLET_OPS.md](DROPLET_OPS.md)).  
Local Mac staging was removed as the default path (ISP/MEXC blocks, no Docker on some laptops).

**Goal:** Run the V4 learning build (and any experimental flags) **without** touching production alerts, prod Telegram bot, or prod SQLite.

| | Production | Staging |
|--|------------|---------|
| Env file | `.env` | `.env.staging` |
| Data dir | `./data` | `./data-staging` |
| Container | `mexc-alert-bot` | `mexc-alert-bot-staging` |
| Telegram | Live bot token | **Second** BotFather bot |
| Learning | Usually off until promoted | **ON** in staging example |
| Compose | default service | `--profile staging` |

---

## One-time setup

### 1. Second Telegram bot (required)

1. Open [@BotFather](https://t.me/BotFather) → `/newbot`  
2. Name e.g. `MEXC Alerts Staging`  
3. Copy the token into **`.env.staging` only** (never into prod `.env`)

### 2. Create staging env + data dir

```bash
cd ~/mexc-bot   # or droplet path
cp .env.staging.example .env.staging
# edit TELEGRAM_BOT_TOKEN=
mkdir -p data-staging
```

Or just:

```bash
./scripts/staging_up.sh   # creates .env.staging from example if missing, then exits until token is set
```

---

## Start / stop

```bash
./scripts/staging_up.sh      # Docker if available, else local Python
./scripts/staging_up.sh --local
./scripts/staging_up.sh --docker

./scripts/staging_down.sh    # staging ONLY — prod left alone
```

### Network note (local Mac)

Some ISP filters (e.g. PLDT Smart “prohibited access”) block `api.mexc.com`.  
Staging bot can still **poll Telegram**, but **prices / movers need MEXC**. Use a **VPN** or run staging on the **droplet** for full sensor tests.

### Docker (droplet / Mac with Docker)

```bash
docker compose --profile staging up -d --build mexc-bot-staging
docker logs -f mexc-alert-bot-staging
# prod:
docker compose up -d mexc-bot   # separate; not stopped by staging scripts
```

Both can run **at the same time** — different tokens, different volumes.

### Local Python (this MacBook — Docker not required)

- Process writes `data-staging/alerts.db`
- Logs: `.staging.log`
- PID: `.staging.pid`
- Env vars exported from `.env.staging` so app never opens `./data`

---

## Smoke checklist (staging bot chat)

```text
/s          → learning=True, futures/movers on
/l          → empty or only staging test alerts (NOT prod list)
/a BTC 999999 → appears [S]
/events     → empty until a fire
/brief      → session brief
/coach panic
/mw add f BTC
/movers on
# after a dump or target cross:
/events → row
/j took
/events → labeled
```

Then confirm **prod** bot still has original `/l` unchanged.

---

## Promote to prod (later)

1. Staging stable + `./scripts/verify_build.sh` green  
2. Deploy code to droplet  
3. Prod `.env`: enable flags only when ready (`FEATURE_LEARNING=true` optional)  
4. **Never** copy `data-staging` over `data`

---

## Safety rules

- Never point staging `ALERTS_FILE` at `./data` or prod path  
- Never put prod token in `.env.staging`  
- Never `docker compose down` without checking — prefer `staging_down.sh` or stop **only** `mexc-bot-staging`  
- `.env.staging` and `data-staging/*` are gitignored  

<!-- agents: search STAGING -->
