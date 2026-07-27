# AGENTS.md — MEXC Alert Bot

Guide for humans and coding agents working on this repo. Read this before changing production behavior.

---

## What this project is

**Telegram bot** that:

1. **Target price alerts (V1 core)** — one-shot alerts when a MEXC price crosses a user target (spot by default).
2. **Futures target alerts (V3)** — same one-shot model on MEXC **USDT-M futures** contracts.
3. **Downside movers (V3)** — watchlist scanner that fires when a coin drops ≥ X% over a lookback window (default 15m). **Down only.** Spot and futures can mix on the same list.

Runtime: Python 3.11+, Docker on a DigitalOcean droplet, long-poll Telegram (`pyTelegramBotAPI`), public MEXC REST (no trading keys).

**Repo:** typically `mexc-alert-bot` on GitHub · **local folder** may be `mexc-bot`.

---

## Non‑negotiable safety rules

1. **Do not break existing spot target alerts.** Large live alert sets run in production.
2. **Feature flags default OFF** in code/config templates. Production enables them explicitly in `.env`.
3. **Additive DB only** — never rewrite or drop `alerts` rows in migrations.
4. **Movers must not delete target alerts.** Separate tables + `MoverScanner` (no shared fire/remove path with `PriceMonitor`).
5. **Prefer `/mw add` / `/mw remove`** over bare `/mw SYMBOL…` (replace-all wipes the whole watchlist).
6. **Never commit `.env`** or bot tokens. Use `.env.example` / `.env.staging.example` only.
7. **Staging uses a separate volume** (`./data-staging`) and preferably a second BotFather token.

---

## Architecture (mental model)

```
Telegram user
    │  commands + notifications
    ▼
bot.py  ─────────────────────────────►  AlertStore (SQLite: alerts)
    │                                         ▲
    │                                         │
    │         PriceMonitor (thread)  ─────────┘  one-shot fire → remove by stable_id
    │              │
    │              ├── MexcClient (spot /ticker/price)
    │              └── MexcFuturesClient (futures /contract/ticker)  [if flag on]
    │
    └── MoverStore + MoverScanner (thread)   [if flag on]
            │         downside % only, step-down re-arm, never touches alerts
            ├── spot and/or futures prices from watchlist markets
            └── PriceHistory ring buffer
```

| Module | Role |
|--------|------|
| `mexc_bot/main.py` | Wire settings, store, providers, monitor, movers, Telegram polling |
| `mexc_bot/config.py` | Env → `Settings` (flags, poll intervals, mover defaults) |
| `mexc_bot/bot.py` | All Telegram handlers (`/a`, `/af`, `/mw`, …) |
| `mexc_bot/monitor.py` | Target-price loop: cross or tolerance band → notify → delete |
| `mexc_bot/exchange.py` | Spot + futures clients; **futures symbol resolve** (STOCK perps) |
| `mexc_bot/storage.py` | SQLite `alerts` + visual ranks + `market` column |
| `mexc_bot/movers/` | Isolated downside scanner (`scanner`, `history`, `storage`) |
| `tests/` | Crossing/remove regression + V3 resolve/movers unit tests |
| `docs/V3_TESTING_AND_PROMOTION.md` | Staging → prod promotion runbook |
| `docker-compose.yml` | `mexc-bot` (prod) + `mexc-bot-staging` (profile `staging`) |

---

## Feature flags (`.env`)

| Variable | Default | Effect |
|----------|---------|--------|
| `FEATURE_FUTURES_ALERTS` | `false` | `/af`, `/p f`, futures book in `PriceMonitor` |
| `FEATURE_MOVER_SCANNER` | `false` | `/movers`, `/mw`, `MoverScanner` thread |
| `MOVER_LOOKBACK_SECONDS` | `900` | Default rolling window (user can override via `/movers set`) |
| `MOVER_THRESHOLD_PERCENT` | `5` | Default downside % from **high within window** |
| `MOVER_POLL_SECONDS` | `5` | Scanner cadence (code floor **2s**). Lower = snappier |
| `MOVER_COOLDOWN_SECONDS` | `45` | **Min-gap** between fires (anti-spam only — not a long mute) |
| `MOVER_RECOVERY_PERCENT` | `3` | Bounce above last-fire price clears cascade anchor |
| `MOVER_MARKETS` | `both` | Idle fallback only; **live scans follow watchlist row markets** |
| `PRICE_POLL_INTERVAL_SECONDS` | `1`–`2` | Target-alert monitor loop |
| `ALERT_TOLERANCE_PERCENT` | `0.0005` | Band fallback for target alerts |
| `ALERTS_FILE` | `data/alerts.json` | Path; storage uses sibling `.db` (JSON migrates once) |
| `MEXC_API_BASE` | spot v3 | Spot REST |
| `MEXC_FUTURES_API_BASE` | `https://contract.mexc.com/api/v1` | Futures REST |

Kill switch: set flag(s) to `false` and `docker compose up -d mexc-bot`.

---

## Data model

### Target alerts — table `alerts`

| Column | Notes |
|--------|--------|
| `id` | Stable PK (autoincrement). **Crossing history keys on this**, not visual rank. |
| `user_id` | Telegram user id |
| `symbol` | Spot: `BTCUSDT`. Futures: `BTC_USDT` or `TSLASTOCK_USDT` |
| `price` | Target |
| `enabled` | 0/1 |
| `market` | `'spot'` (default) or `'futures'` — **additive migration** |

**Visual id** shown in `/l` is 1-based rank (`ORDER BY id ASC`), recomputed every read. `/r` / `/t` use visual ids. Monitor removes by **stable_id**.

One-shot: fire → Telegram → `remove_alerts_by_stable_ids`.

Fire condition: price **crossed** target since last sample **or** within tolerance band.

### Movers — separate tables (same DB file)

- `mover_settings` — per-user enabled, threshold %, lookback seconds  
- `mover_watchlist` — `(user_id, symbol, market)` — **spot and futures mix allowed**  
- Cascade anchors + min-gap: **in-memory** (reset on container restart)

---

## Telegram commands (cheat sheet)

### Spot target alerts (always)

| Command | Action |
|---------|--------|
| `/a BTC 65000` | Add spot one-shot (`BTCUSDT`) |
| `/l` | List alerts (`[S]` / `[F]`) |
| `/r 3` or `/r BTCUSDT` | Remove |
| `/t 3` | Toggle |
| `/p BTC` | Spot price |
| `/s` | Status + flags + health |
| `/clearall confirm` | Wipe all target alerts |
| `/disableall` | Disable without delete |

### Futures targets (`FEATURE_FUTURES_ALERTS`)

| Command | Action |
|---------|--------|
| `/af BTC 65000` | Futures one-shot |
| `/af TSLA 250` | Resolves stock perps (e.g. `TSLASTOCK_USDT`) |
| `/p f BTC` / `/p f zhipu` | Futures price (short names OK) |

**Symbol resolve** (`exchange.resolve_futures_symbol`): maps `TSLA` / `ZHIPU` / `samsung` → live contract list including `*STOCK*_USDT`. Do not assume every name is `BASE_USDT`.

### Movers (`FEATURE_MOVER_SCANNER`)

| Command | Action |
|---------|--------|
| `/movers on` / `off` | Enable scanner for you |
| `/movers set 5 15` | 5% down in 15 minutes |
| `/movers list` | Settings + grouped watchlist |
| `/mw` | Show watchlist + tips |
| `/mw add f BTC ETH` | Add **futures** (does not wipe spot) |
| `/mw add s SIREN` | Add **spot** only (own book — e.g. when futures price differs) |
| `/mw add f:BTC s:SIREN` | Mixed add in one message |
| `/mw remove SIREN` | Remove (either market; or `s`/`f` filter) |
| `/mw clear` | Empty watchlist |
| Bare `/mw BTC ETH` | **Replaces entire list** — avoid when mixed |

Movers fire message prefix: `MOVER` with `[F]` or `[S]`. Not one-shot — **step-down re-arm** on further legs of a dump.

### Mover enrichments (scanner-owned)

When movers are on, the scanner can add (env-tunable; see `.env.example`):

| Feature | Default | Notes |
|---------|---------|--------|
| **Velocity** | on | `%/min` from peak + `PANIC` / `FAST` / `GRIND` on each fire |
| **Volume** | on | 24h vol line when futures ticker provides it |
| **Auto heat board** | on | Pushes ranked top-N when ≥`MOVER_HEAT_BREADTH_MIN` names dump — **no `/mw` needed** |
| **`/mw` heat** | on | Same ranking on demand |
| **Red candles** | **off** | 5m/15m/1h/4h consecutive closed reds via klines (`MOVER_ENRICH_KLINES`) |

Still **never** deletes target `alerts`. Backlog of other strategy bots: `docs/FUTURE_STRATEGY_BOTS.md`.

### Mover detection model (precision + cascade)

**First fire:** every poll, **rolling high → now** drawdown over lookback (default 15m). Fire as soon as ≤ −threshold. No candle close wait.

**Cascade (step-down):** on fire, set `anchor = price_now`. Next fire when  
`price_now ≤ anchor × (1 − threshold)`  
(e.g. another −7% from the last alert price). Then anchor moves to the new price.  
A −7% then another −20% cascade can produce multiple alerts — **not** silenced for 30 minutes.

**Min-gap:** `MOVER_COOLDOWN_SECONDS` (default **45s**) only blocks rapid double-sends. It is **not** a long mute. Production must not leave this at `1800` if cascade legs matter.

**Recovery:** if price rises `MOVER_RECOVERY_PERCENT` (default **3%**) above the anchor, clear anchor and return to peak-within-window mode for a new wave.

| Concept | Meaning |
|---------|--------|
| Window | Last N seconds of samples (N = user lookback) |
| Peak fire | `(price_now − peak) / peak` ≤ −threshold |
| Step fire | `(price_now − anchor) / anchor` ≤ −threshold |
| Latency after cross | ≈ poll interval (default 5s, floor 2s) |
| Cold start | Needs ~one full lookback of samples before first **peak** eval |

**Spot vs futures:** separate books. Spot row never uses futures lastPrice.

**Message shapes:**
- Peak: `… −X% within 15m · High … → now …`
- Step: `… −X% step from last alert · Last … → now …`

Tune: `MOVER_POLL_SECONDS=3`, `/movers set 7 15`, `MOVER_COOLDOWN_SECONDS=45`.

---

## Important implementation details

### Crossing keying (do not regress)

`PriceMonitor._last_prices` is keyed by `(user_id, stable_id)`, **not** visual rank. Visual ranks shift on any remove; stable keys prevent mass false fires. See `tests/test_crossing_and_remove_logic.py`.

### Telegram formatting

- Command replies: **plain text** (`parse_mode=None`). Default Markdown breaks on `BTC_USDT` underscores (400 parse entities).
- Fire notifications: **HTML** with escaped symbols.

### Futures client cache

`MexcFuturesClient` caches full ticker batch (~2 min) for resolve + prices. `get_all_prices()` force-refreshes.

### Movers scanner markets

Fetches **only markets present on enabled users’ watchlists**. Spot row → spot book; futures row → futures book. Never compare spot SIREN to futures SIREN.

History uses `PriceHistory.peak_drawdown` (not candle OHLC). Futures resolve cache (~2 min) does **not** apply to mover cycles — `get_all_prices()` force-refreshes.

---

## Local dev

```bash
cp .env.example .env   # set TELEGRAM_BOT_TOKEN
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
make test              # or: python tests/test_*.py
python -m mexc_bot.main
```

---

## Docker / droplet

```bash
# Production (volume ./data)
docker compose up -d --build mexc-bot
docker logs -f mexc-alert-bot

# Staging (profile + ./data-staging + .env.staging)
cp .env.staging.example .env.staging
docker compose --profile staging up -d --build mexc-bot-staging
```

Healthcheck expects `/app/data/alerts.db`. Permissions: non-root `appuser` (uid 1000) — `chown -R 1000:1000 data` if SQLite fails to open.

Deploy flow: push GitHub → droplet `git pull` → `docker compose up -d --build mexc-bot`.

---

## Tests agents must keep green

```bash
python tests/test_crossing_and_remove_logic.py
python tests/test_v3_futures_and_movers.py
# or: make test
```

Covers: stable_id crossing after rank shift, market isolation, futures resolve (`ZHIPU`→`ZHIPUSTOCK_USDT`), mover downside-only + step-down cascade, watchlist remove, alerts table isolation.

---

## How to extend safely

| Goal | Where | Notes |
|------|--------|------|
| New Telegram command | `bot.py` | Gate V3 behind flags; keep `/a` path unchanged |
| New price source | `exchange.py` + `PriceProvider` | Don’t hardcode REST in monitor |
| Alert fields | `storage.py` additive columns | Default old rows |
| Mover behavior | `mexc_bot/movers/*` only | No `AlertStore.remove_*` from scanner |
| Stock aliases | `FUTURES_BASE_ALIASES` in `exchange.py` | e.g. TESLA→TSLA |

---

## What not to do

- Don’t key monitor history by visual alert id  
- Don’t reintroduce global Markdown parse mode on the bot  
- Don’t put mover rules in the `alerts` table  
- Don’t point staging volume at production `./data`  
- Don’t assume futures symbol == spot base + `_USDT` (stock perps use `*STOCK*`)  
- Don’t lower production mover thresholds for “testing” without telling the user  

---

## Related docs

- `README.md` — product overview + deploy narrative  
- `docs/V3_TESTING_AND_PROMOTION.md` — staging/prod promotion checklist  
- `docs/FUTURE_STRATEGY_BOTS.md` — backlog of separate strategy bots (sympathy, base-break, divergence, …); revisit when planning next features  
- `.env.example` / `.env.staging.example` — env templates  

---

## Quick status check (production)

```text
/s          → flags, poll health
/l          → target alerts still present
/p BTC      → spot
/p f BTC    → futures
/mw         → movers watchlist grouped [F]/[S]
```

If target alerts vanish or mass-fire: check recent changes to `monitor.py` / `storage.py` stable_id logic and run crossing tests immediately.
