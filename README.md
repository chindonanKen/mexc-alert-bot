# MEXC Alert Bot

Telegram bot that watches MEXC exchange prices and sends you instant alerts when a crypto hits your target price.

Originally a single-file script running 24/7 on a Digital Ocean VPS. This is the restructured, production-ready version with proper architecture, configuration, and deployment tooling.

## Current Features

- Set one-shot price alerts via Telegram (`/addalert SYMBOL PRICE`)
- Supports any symbol MEXC lists (e.g. `BTCUSDT`, `SOLUSDT`, `ENAUSDT`)
- Percentage-based tolerance (default 0.05%) — works for both low and high priced assets
- Enable/disable individual alerts
- List and remove alerts
- Background price polling (configurable interval)
- Alerts are removed automatically after they trigger
- JSON file persistence (easy to backup/inspect)
- Dockerized for reliable deployment and zero-downtime updates

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
- Tolerance is relative (`|current-target| / target <= tolerance`).
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

## Deployment to Digital Ocean (Current VPS)

### Recommended: Docker + docker-compose (easiest updates)

On your VPS:

```bash
# 1. One-time setup
git clone <your-new-github-repo-url> mexc-bot
cd mexc-bot

cp .env.example .env
# Edit .env and paste the REAL TELEGRAM_BOT_TOKEN (never commit it)

# 2. Build and run
docker compose up -d --build

# 3. Check logs
docker compose logs -f mexc-bot

# 4. To update in the future (after you push to GitHub)
git pull
docker compose up -d --build
```

Data lives in `./data/alerts.json` on the host (volume-mounted). It survives container rebuilds.

### Alternative (no Docker)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Use a process manager (systemd, pm2, or screen/tmux for quick)
nohup python -m mexc_bot.main > bot.log 2>&1 &
```

### Updating the running bot on VPS (after code changes)

Workflow we will use:

1. Make changes locally (or in a feature branch).
2. Commit + `git push` to GitHub.
3. On the VPS:
   ```bash
   cd ~/mexc-bot
   git pull
   docker compose up -d --build
   ```
4. (Optional but nice) Add a small `deploy.sh` or GitHub Action that can trigger a deploy via webhook + DO API / SSH later.

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

**Status**: Refactored + architected (April 2026). Ready for feature work and clean GitHub + VPS deployment loop.
