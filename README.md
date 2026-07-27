# MEXC Alert Bot

Telegram bot that watches MEXC exchange prices and sends you instant alerts when a crypto hits your target price.

**For agents & contributors:** start with **[AGENTS.md](AGENTS.md)** (architecture, flags, safety, movers model).  
**Session / latest build handoff:** [docs/SESSION_HANDOFF.md](docs/SESSION_HANDOFF.md).  
**Future strategy bots backlog:** [docs/FUTURE_STRATEGY_BOTS.md](docs/FUTURE_STRATEGY_BOTS.md).

Originally a single-file script running 24/7 on a Digital Ocean VPS. This is the restructured, production-ready version with proper architecture, configuration, and deployment tooling.

## Current Features

- One-shot **spot** target alerts via Telegram (`/a BTC 65000`)
- Triggers on price **crossing** the target or landing in the tolerance band
- Enable/disable, list, remove; alerts removed after fire
- Background price polling + SQLite + Docker on DigitalOcean

### V3 (flags default OFF in templates; production may enable)

| Flag | Feature |
|------|---------|
| `FEATURE_FUTURES_ALERTS=true` | Futures one-shots `/af`, stock perps resolve (`TSLA` → live contract e.g. `TSLAUSDT`) |
| `FEATURE_MOVER_SCANNER=true` | Downside movers: peak high→now, **step-down cascade**, velocity/volume/heat board; optional kline reds |

Details, env knobs, and “do not break prod” rules: **[AGENTS.md](AGENTS.md)**.  
Staging checklist: [docs/V3_TESTING_AND_PROMOTION.md](docs/V3_TESTING_AND_PROMOTION.md).

Staging service (separate data volume):

```bash
cp .env.staging.example .env.staging   # second BotFather token
docker compose --profile staging up -d --build mexc-bot-staging
```

## Quick Start (Local)

1. Clone the repo and enter the directory.

2. Create environment file:
   ```bash
   cp .env.example .env
   ```

3. Edit `.env` and set your bot token (get one from [@BotFather](https://t.me/BotFather)):
   ```
   TELEGRAM_BOT_TOKEN=123456789:AAF...
   ```

4. Install deps and run:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt

   # Run
   python -m mexc_bot.main
   ```

   Or with the package:
   ```bash
   python -m mexc_bot.main
   ```

## Architecture

### High-Level Components

```
┌─────────────────────┐
│   Telegram Users    │
└──────────┬──────────┘
           │ (commands + notifications)
           ▼
┌─────────────────────┐       ┌─────────────────────┐
│   Telegram Bot      │◄──────│   Price Monitor     │
│   (bot.py)          │       │   (monitor.py)      │
└──────────┬──────────┘       └──────────┬──────────┘
           │                             │ polls every N seconds
           │ uses                        ▼
           │                    ┌─────────────────────┐
           │                    │   MEXC Exchange     │
           │                    │   (exchange.py)     │
           │                    └─────────────────────┘
           │
           ▼
┌─────────────────────┐
│   AlertStore        │
│   (storage.py)      │  ──► data/alerts.json  (atomic writes + lock)
└─────────────────────┘
           ▲
           │
┌─────────────────────┐
│   Settings / Config │
│   (config.py)       │  ◄── .env
└─────────────────────┘
```

### Module Responsibilities

| Module          | Responsibility |
|-----------------|----------------|
| `config.py`     | Loads all settings from environment with sane defaults. Fails fast on missing required values (especially the bot token). |
| `storage.py`    | Thread-safe CRUD for alerts. Atomic file writes (temp + rename) + RLock. Per-user ID generation. |
| `exchange.py`   | Price data adapter. Currently only MEXC public `/ticker/price`. Easy to extend for other exchanges or WebSocket feeds later. |
| `monitor.py`    | Background worker. Iterates active alerts, fetches prices, decides when to fire, calls notifier, then removes fired alerts. |
| `bot.py`        | All Telegram command handlers (`/addalert`, `/listalerts`, etc.). Clean separation from core logic. |
| `main.py`       | Wiring + lifecycle. Starts monitor thread, creates bot, registers signal handlers for graceful shutdown. |

### Data Model (v1)

Each alert is a simple dict:
```json
{
  "id": 7,
  "symbol": "FHEUSDT",
  "price": 0.03155,
  "enabled": true
}
```

Stored under the Telegram `user_id` (integer key in the top-level JSON object).

**Design notes:**
- One-shot alerts (removed on fire) — kept from original behavior.
- Crossing detection (`(prev - target) * (current - target) <= 0`) is the main way it detects when price has reached/passed your level in either direction. The tolerance band is a secondary exact-hit fallback.
- No per-alert "direction" yet (above or below). Can be added easily.

### Extensibility Points (Planned / Easy to Add)

- Multiple alert types: "above", "below", "percent change", "trailing stop"
- Different notification channels (email, Discord, another Telegram chat)
- Multi-exchange support (Binance, Bybit, etc.) via abstract `ExchangeClient`
- Switch persistence to SQLite (or Postgres) for better querying / multi-process
- WebSocket price streams instead of polling (lower latency, fewer requests)
- Web UI or admin dashboard (FastAPI + nice table)
- Metrics / Prometheus exporter + better logging
- Rate limit handling + exponential backoff
- Per-user settings (tolerance, timezone, quiet hours)

## Deployment to Digital Ocean (VPS)

**Repo**: https://github.com/chindonanKen/mexc-alert-bot

We use Docker + docker-compose for reliable 24/7 running and easy, safe updates.

### Prerequisites on your Droplet
- Ubuntu (recommended) or similar Linux.
- Docker + Docker Compose installed (official Docker install script is easiest).
- SSH access from your machine.
- (Strongly recommended) You have rotated the Telegram bot token in @BotFather since the original single-file version had the token hardcoded in plaintext.

### One-time Setup on the VPS

SSH into your Digital Ocean Droplet and run:

```bash
# 1. Clone the repo (you can name the folder whatever you like)
git clone https://github.com/chindonanKen/mexc-alert-bot.git
cd mexc-alert-bot

# 2. Create your local environment file (NEVER commit this)
cp .env.example .env
nano .env          # or vim / code --wait etc.

# Paste your real TELEGRAM_BOT_TOKEN (from @BotFather)
# You can also set PRICE_POLL_INTERVAL_SECONDS=1 for faster alerts
# Save and exit

# 3. Start the bot
docker compose up -d --build

# 4. Follow the logs to confirm it's healthy
docker compose logs -f mexc-bot
```

You should see lines like:
- "Price monitor started (batch mode)"
- "Starting Telegram bot polling..."

Test it immediately from Telegram with `/start` or `/a BTC 65000` (use a price you know will be near current for testing).

Your alerts are persisted in `./data/alerts.json` on the host via Docker volume — they survive container restarts and image rebuilds.

### Safe & Easy Update Workflow (Editing via Grok Build)

This is the flow we will use going forward so you can ask me to add features/fixes here, and they get to your VPS cleanly:

1. **Here in this chat (Grok Build)**: Tell me what to change ("add a /price command that shows 24h change", "make tolerance 0.1%", "fix the status message", etc.). I edit the code in the local working tree.

2. **Commit & push** (I can do this for you via tools, or you run locally):
   ```bash
   git add .
   git commit -m "Your descriptive message"
   git push
   ```

3. **On the VPS** (one or two commands):
   ```bash
   cd ~/mexc-alert-bot          # or wherever you cloned it
   git pull
   docker compose up -d --build
   docker compose logs --tail 100 -f
   ```

That's it. The container restarts with the new code, your `data/alerts.json` is untouched, and the bot keeps running.

**Pro tips for smoothness**:
- Keep this local `mexc-bot` folder on your Mac in sync with the GitHub repo (`git pull` occasionally if you edit on VPS or via other means).
- For even less typing on the VPS you can create a tiny helper:
  ```bash
  # ~/mexc-alert-bot/deploy.sh
  #!/bin/bash
  git pull && docker compose up -d --build && docker compose logs --tail 50
  ```
  Make it executable and run `./deploy.sh` after every push.

### Alternative: Run without Docker (quick & dirty)

Only use this for testing:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # edit it
python -m mexc_bot.main
```

Use a process manager (systemd, tmux + nohup, or pm2) for production if you go this route.

### Security Best Practices (Important)

- `.env` is gitignored and must **only** live on your VPS (and in your password manager / 1Password).
- The original `archive/original/mexc_bot.py` contained a hardcoded token — rotate it if you haven't already.
- The Dockerfile runs as a non-root `appuser`.
- Only the public MEXC API is called (no API keys needed for price data).
- Consider using a dedicated Telegram bot just for this (not one you use for other things).
- For future extra safety we can add:
  - GitHub Actions that only builds the image (no secrets in CI)
  - Deploy keys with read-only access for the VPS
  - Docker secrets or DO App Platform environment variables

### Monitoring & Troubleshooting

```bash
# Live logs
docker compose logs -f mexc-bot

# Restart
docker compose restart mexc-bot

# Full rebuild (after dependency or Dockerfile changes)
docker compose up -d --build --force-recreate

# Check container health
docker ps
```

If the bot stops responding on Telegram, check logs for polling errors or token issues.

### Making Future Development Easy with Grok

- All source of truth is now this GitHub repo.
- You can ask me anything like: "add support for multiple users better", "make the alert message include a MEXC link", "bump the tolerance in .env.example".
- I will implement, we review, commit/push, then one `git pull + compose up` on the VPS.
- Later we can add GitHub Actions for automatic image building or even auto-deploy on push (using a deploy key or DO API token scoped tightly).

The current setup (batch price fetching + 1s polling by default + short commands) should give you much more reliable and snappy alerts than the original single-file version.

## Project Structure

```
mexc-bot/
├── README.md
├── requirements.txt
├── .env.example
├── .gitignore
├── Dockerfile
├── docker-compose.yml
├── data/
│   └── alerts.json          # runtime state (gitignored)
├── mexc_bot/
│   ├── __init__.py
│   ├── config.py
│   ├── storage.py
│   ├── exchange.py
│   ├── monitor.py
│   ├── bot.py
│   └── main.py
└── (old single-file version kept as mexc_bot.py for reference during transition)
```

## Security Notes

- **Never commit your `.env`** or any file containing the Telegram bot token.
- The old `mexc_bot.py` had the token hardcoded — that file should be considered compromised for that token. Rotate the bot token in @BotFather if the old script was ever pushed anywhere public.
- Consider using a separate bot token for development vs production.

## Development Tips

- Run with `python -m mexc_bot.main`
- Change tolerance or poll interval via `.env` without code changes.
- The storage layer is safe to call from multiple threads (the monitor + any future web handlers).
- Add new commands in `bot.py` only. Keep business logic in the other modules.

## Next Steps / Roadmap Ideas

- [ ] Add "above/below" direction to alerts
- [ ] Support percentage-change alerts (e.g. +5% from now)
- [ ] Better ID display and editing
- [ ] Switch to async (aiogram + aiohttp) for scale
- [ ] Add a tiny health HTTP server (`/health`) so Docker healthchecks and DO monitoring work nicely
- [ ] GitHub Actions: lint + build Docker image
- [ ] Automated deploy from GitHub → Digital Ocean (via SSH action or DO App Platform)

## License

Personal project.

---

**Status**: Refactored + architected. GitHub repo live at https://github.com/chindonanKen/mexc-alert-bot. Clean Docker-based deployment ready for Digital Ocean + rapid iteration via Grok Build.

Big moves / futures volatility scanner research completed but intentionally deferred (per your request).
