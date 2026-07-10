# V3 — Futures alerts + downside movers

## What was built

Two features, both **off by default** (`FEATURE_*=false`):

| Feature | How you use it | How it works |
|---------|----------------|--------------|
| **Futures target alerts** | `/af BTC 65000` | Same one-shot cross/band logic as spot, but prices from MEXC contract API (`BTC_USDT`) |
| **Downside movers** | `/movers on`, `/mw BTC ETH` | Separate scanner: −X% over lookback (default 15m), **down only**, cooldown, never deletes target alerts |

Production V1 (`/a`, spot monitor, existing SQLite rows) is unchanged when flags stay `false`.

## Safety design

- Feature flags default **false**
- Additive DB column: `alerts.market` default `'spot'` (existing rows keep working)
- Mover tables: `mover_settings`, `mover_watchlist` — **never** touch `alerts`
- Staging compose profile uses **`./data-staging`** and **`.env.staging`** (separate volume)
- Futures rows are ignored by the monitor if `futures_provider` is not attached (flag off)

---

## Local unit tests (no Telegram, no MEXC)

From the repo root:

```bash
python tests/test_crossing_and_remove_logic.py
python tests/test_v3_futures_and_movers.py
```

All should print `PASS` / `All … tests passed`.

---

## Staging on DigitalOcean (recommended before prod)

Use a **second Telegram bot** from [@BotFather](https://t.me/BotFather) so your live alert chat is never mixed with test noise.

### 1. On the droplet

```bash
cd ~/mexc-alert-bot   # or your actual path
git pull              # after this branch is pushed

# Staging env
cp .env.staging.example .env.staging
nano .env.staging     # set TELEGRAM_BOT_TOKEN to the *staging* bot token
# Confirm:
#   FEATURE_FUTURES_ALERTS=true
#   FEATURE_MOVER_SCANNER=true

mkdir -p data-staging
chown -R 1000:1000 data-staging   # if container runs as appuser

# Start ONLY staging (prod container left alone)
docker compose --profile staging up -d --build mexc-bot-staging

docker logs -f mexc-alert-bot-staging
```

You should see:

- `Feature flags: futures_alerts=True mover_scanner=True`
- `Futures price client ready`
- `Mover scanner thread started`
- `Starting Telegram bot polling...`

### 2. Telegram checklist (staging bot)

**Regression (spot still works):**

1. `/a BTC 999999` → created `[S]`
2. `/l` → shows `[S]`
3. `/r` that id → removed
4. `/p BTC` → spot price

**Futures:**

1. `/p f BTC` → futures last price
2. `/af BTC <price slightly above or below current>` so it can cross soon  
   or pick a level you know will print
3. `/l` → `[F]` tag
4. Wait for `🚨 … [F]` then confirm it disappears from `/l`

**Movers:**

1. `/mw BTC ETH SOL`
2. `/movers set 5 15`
3. `/movers on`
4. `/movers list` → ON + watchlist
5. Wait for history to fill (~lookback). For a faster test, set `/movers set 1 2` (1% in 2 minutes) temporarily
6. On a real dump you get `📉 MOVER [F] …` — same symbol should not re-spam until cooldown

**Isolation:**

- Staging must use `data-staging`, not `./data`
- Prod `mexc-alert-bot` should still be running; `/s` on **prod** bot should show `futures=False movers=False` if flags off there

### 3. Stop staging when done

```bash
docker compose --profile staging stop mexc-bot-staging
# optional: docker compose --profile staging down
```

---

## Promote to production (only after staging is good)

### Soft deploy (code only, flags still off)

```bash
cd ~/mexc-alert-bot
git pull
# Ensure production .env still has:
#   FEATURE_FUTURES_ALERTS=false
#   FEATURE_MOVER_SCANNER=false
docker compose up -d --build mexc-bot
docker logs --tail 50 mexc-alert-bot
```

Confirm:

- Existing spot alerts still listed on **prod** Telegram bot
- Logs: `Feature flags: futures_alerts=False mover_scanner=False`
- No errors about DB / `market` column (migration is automatic and additive)

### Enable futures only

In production `.env`:

```bash
FEATURE_FUTURES_ALERTS=true
FEATURE_MOVER_SCANNER=false
```

```bash
docker compose up -d mexc-bot
```

Test `/af` + `/p f` on prod carefully. Spot `/a` path unchanged.

### Enable movers later

```bash
FEATURE_MOVER_SCANNER=true
MOVER_MARKETS=futures
# keep reasonable defaults
```

```bash
docker compose up -d mexc-bot
```

Then `/mw …`, `/movers set 5 15`, `/movers on`.

### Instant kill switch

Set either flag back to `false` and recreate the container — target alerts and DB rows remain; futures monitoring / scanner simply stop.

---

## Grok Build on the droplet

If you use Grok Build SSH’d into the VPS:

1. **Never** point staging `ALERTS_FILE` / volume at production `./data`
2. Prefer editing `.env.staging` and running the staging service
3. Before any prod `docker compose up --build`, confirm prod flags and that `./data` is the volume you intend
4. After schema migration, a one-time `alerts.market` column appears — all old rows are `spot`

---

## Commands cheat sheet

| Command | Notes |
|---------|--------|
| `/a BTC 65000` | Spot (always) |
| `/af BTC 65000` | Futures (needs `FEATURE_FUTURES_ALERTS`) |
| `/l` | List with `[S]` / `[F]` |
| `/p BTC` / `/p f BTC` | Spot / futures price |
| `/movers on\|off\|set\|list` | Mover controls |
| `/mw BTC ETH` | Replace futures watchlist |
| `/mw clear` | Empty watchlist |
| `/s` | Health + flag status |

---

## Known limits (acceptable for V3 test)

- Futures API must be reachable from the droplet (geo/IP blocks possible from some networks)
- Mover needs ~lookback of samples before first evaluation (cold start)
- Watchlist-only (no “scan entire exchange” yet — by design for noise control)
- Cooldowns are in-memory (reset on container restart)
