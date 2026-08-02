# Droplet: AD Desk learning on **real** data

**No seed / dummy data.** Desk and Telegram bot share the **same production SQLite** so movers, targets, and learning events are your live book.

Owner Telegram / desk user id: **`8630949601`**

---

## Architecture (what talks to what)

```
MEXC public APIs ──► mexc-bot (Telegram alarms: targets + movers)
                         │
                         ▼
                   data/alerts.db   ◄── same file ──►  mexc-desk (AD Desk UI + voice + learning)
                         ▲
MEXC private read ───────┘  (fills → journal → auto took/skip)
```

- **Telegram bot** = push alarms (unchanged).  
- **AD Desk** = overview, Learning, coach, voice.  
- **One DB** = real alerts, watchlist, fires, labels, journal.

---

## 1. MEXC API key (you create this)

In MEXC account → API management:

| Setting | Value |
|---------|--------|
| Permissions | **Read only** (spot trade history / account read) |
| Trade / withdraw / futures trade | **OFF** |
| IP bind | Droplet public IP if MEXC allows (recommended) |

You will put key + secret **only** in droplet `.env` — never commit, never paste into git/chat if avoidable.

> Note: current fill sync is **spot myTrades**. Futures engagement still learns from **journal** positions on desk and any fills that map; spot fills auto-journal when private read is on.

---

## 2. Droplet `.env` — edit checklist

Path: typically `~/mexc-alert-bot/.env` (same file for bot + desk compose).

### Already should be on (bot)

Keep your working Telegram / mover settings. Confirm at least:

```bash
TELEGRAM_BOT_TOKEN=...          # existing
FEATURE_FUTURES_ALERTS=true     # if you use /af
FEATURE_MOVER_SCANNER=true      # if you use movers
FEATURE_LEARNING=true           # fires → learning_events (you said already on)
ALERTS_FILE=./data/alerts.json  # or path compose mounts; resolves to alerts.db
```

### Add / set for desk + learning + MEXC read

```bash
# --- AD Desk identity (must match Telegram user) ---
DESK_USER_ID=8630949601
MEXC_PRIVATE_TELEGRAM_USER_ID=8630949601

# --- Desk API auth (long random string; same token in browser ?token=) ---
DESK_API_TOKEN=generate-a-long-random-secret
DESK_PORT=8080
DESK_HOST=0.0.0.0
# DESK_ALLOW_LIVE_ORDERS=false   # keep false

# --- Learning auto engagement (real journal/fills, not dummy) ---
LEARNING_AUTO_FROM_POSITIONS=true
LEARNING_GRACE_SECONDS=3600
LEARNING_MAX_PENDING_QUESTIONS=2
LEARNING_ENGAGEMENT_POLL_SECONDS=60
LEARNING_OUTCOME_HORIZONS_SECONDS=900,3600,14400
LEARNING_OUTCOME_POLL_SECONDS=60

# --- MEXC private READ only (after you create the key) ---
FEATURE_MEXC_PRIVATE_READ=true
MEXC_API_KEY=your_read_only_key
MEXC_API_SECRET=your_read_only_secret
MEXC_FILL_SYNC_POLL_SECONDS=120
MEXC_FILL_NOTIFY=false

# --- Voice (optional but recommended for teach/coach) ---
XAI_API_KEY=...
XAI_API_BASE=https://api.x.ai/v1
DESK_VOICE_TTS=true
```

**Critical:** `DESK_USER_ID` and `MEXC_PRIVATE_TELEGRAM_USER_ID` must be **`8630949601`** so desk rows match Telegram learning events and fill sync attaches to your user.

**Critical:** Desk container must mount the **same** `./data` as the bot (compose already does this if both use the same volume).

---

## 3. Deploy steps (droplet)

```bash
cd ~/mexc-alert-bot   # or your clone path
git fetch origin && git pull origin main
# edit .env as above — real keys only, no seed scripts

# Rebuild bot (learning bridge starts with FEATURE_LEARNING)
docker compose up -d --build mexc-bot

# Desk (profile desk if used)
docker compose --profile desk up -d --build mexc-desk
# OR: ./scripts/desk_https_up.sh   # if you use Caddy HTTPS for mic

docker logs --tail 80 mexc-alert-bot
docker logs --tail 80 mexc-desk   # name may vary — check compose
```

**Do not run** `make desk-seed` / `seed_desk_local.py` on the droplet.

---

## 4. Verify real data

| Check | Expect |
|-------|--------|
| Telegram `/l` `/mw` | Same targets/movers as desk Targets / Movers |
| Desk Overview | Your real top targets, recent mover fires, open journal positions |
| Learning | Real `learning_events` after fires; auto took/skip after 1h if journal/fills |
| Fills | After private read: new spot trades appear in journal_fills over time |
| `/s` or logs | Engagement bridge / outcome poller running if learning on |

```bash
# Optional DB peek (read-only)
sqlite3 data/alerts.db "SELECT COUNT(*) FROM alerts;"
sqlite3 data/alerts.db "SELECT COUNT(*) FROM learning_events WHERE user_id=8630949601;"
sqlite3 data/alerts.db "SELECT COUNT(*) FROM journal_fills WHERE user_id=8630949601;"
```

---

## 5. Day-to-day use

1. **Alarms** still on Telegram.  
2. Open **AD Desk** (HTTPS if voice).  
3. **Needs you** cards show symbol · band · drop · fire price · time · system inference — answer when back.  
4. **Trade reviews:** closed positions with hold time, PnL %, buy/sell layers, linked fire; tag plan_ok / fomo / pride / …  
5. **By ticker:** click a chip for that chart’s fires/trades/win-rate; ask coach “SIREN process?”  
6. **Teach** + **Ask coach**; voice tools use the same memory.  
7. MEXC fills → journal → after **1h** grace, auto **took** / **skip**.  
8. Desk delete target/mover should match Telegram `/l` `/mw` (same DB + `DESK_USER_ID=8630949601`).

---

## 6. Security

- Read-only MEXC key only  
- Never commit `.env`  
- `DESK_ALLOW_LIVE_ORDERS=false`  
- Rotate desk token if leaked  
- Prefer IP-restricted MEXC key  

---

## Compose note

If desk was not running before:

```bash
docker compose --profile desk up -d --build
```

Ensure desk service has:

- `env_file: .env`  
- volume `./data:/app/data` (same as bot)  
- `ALERTS_FILE` pointing at that data path  
