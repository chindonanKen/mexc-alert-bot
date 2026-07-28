# START HERE — new machine / new Grok session

**Read this first** when you open the repo on a laptop or start a **new Grok Build chat**.  
Grok sessions **do not** sync between Mac mini and MacBook — only **git (GitHub)** + these docs carry context.

| Doc | When |
|-----|------|
| **This file** | First open / machine move |
| **[AGENTS.md](AGENTS.md)** | Before **any** code change (safety + architecture) |
| **[docs/SESSION_HANDOFF.md](docs/SESSION_HANDOFF.md)** | What shipped recently, prod posture, open work |
| **[docs/FUTURE_STRATEGY_BOTS.md](docs/FUTURE_STRATEGY_BOTS.md)** | Separate bots *not* built yet |
| **[docs/V3_TESTING_AND_PROMOTION.md](docs/V3_TESTING_AND_PROMOTION.md)** | Staging → droplet deploy |

---

## Owner intent (Kenneth)

- Daytrades MEXC; **movers / panic dumps** are the money feature (AD-style scale-in).
- **Production bot stays on DigitalOcean** — laptop is for **dev with Grok Build**, not hosting the live bot.
- **Do not break spot target alerts** (large live set).
- Next phase: **major alarm-system upgrade** (design first; keep V1 path safe).

---

## Setup on a new Mac (MacBook)

### 1. Clone (do not copy random folders)

```bash
cd ~
git clone https://github.com/chindonanKen/mexc-alert-bot.git mexc-bot
cd mexc-bot
git log -1 --oneline    # should match origin/main tip
```

If you already cloned, just:

```bash
cd ~/mexc-bot && git pull origin main
```

### 2. Local env (optional — for running bot on laptop)

```bash
cp .env.example .env
# Set TELEGRAM_BOT_TOKEN (staging bot preferred — never put prod secrets in chat)
# Prefer FEATURE_*=false locally unless testing V3
```

**Never commit `.env`.** Prod secrets live only on the **droplet**.

### 3. Python + tests

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
make test
```

### 4. Open in Grok Build

1. Open **Grok Build** on the MacBook.  
2. Open folder **`~/mexc-bot`** (this repo).  
3. **New session** is fine — point the agent at:

   > Read `START_HERE.md`, then `AGENTS.md`, then `docs/SESSION_HANDOFF.md`.  
   > I am upgrading the alarm system; do not break spot targets. Production is Docker on DigitalOcean.

### 5. Deploy after code changes (still droplet)

```bash
# on droplet
cd ~/mexc-alert-bot
git pull origin main
docker compose up -d --build mexc-bot
docker logs --tail 80 mexc-alert-bot
```

Smoke: Telegram `/s` · `/l` · `/p f TSLA` · `/mw`.

---

## What is *not* transferred by git

| Item | Where it lives | Action |
|------|----------------|--------|
| Grok Build **chat history** | Per machine / product | Start new session; use these docs |
| Droplet **`.env`** / tokens | Server only | SSH droplet if needed; don’t put tokens in repo |
| Prod **SQLite** `data/alerts.db` | Droplet volume | Leave on server |
| Old Mac mini sessions | Mini only | Optional archive; not required for coding |

---

## Agent bootstrap checklist (copy into first message if needed)

```
You are working on mexc-alert-bot (folder may be mexc-bot).
1. Read START_HERE.md, AGENTS.md, docs/SESSION_HANDOFF.md.
2. Run make test before/after behavior changes.
3. Never break spot target stable_id crossing; never delete alerts from movers.
4. Feature flags default OFF in templates; prod enables on droplet.
5. Stock futures resolve: TSLA may be TSLAUSDT / TESLA_USDT / *STOCK*_USDT — see exchange.py.
6. Deploy path: push GitHub → droplet git pull → docker compose up -d --build mexc-bot.
```

---

## Related local projects (not this repo)

- `~/ad-theory-trading-agent` — AD chart learning (optional context for features).  
- Production runtime is **only** the droplet Docker stack, not the laptop.

<!-- agents: search START_HERE -->
