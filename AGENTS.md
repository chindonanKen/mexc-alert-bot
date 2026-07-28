# AGENTS.md — MEXC Alert Bot

Guide for humans and coding agents. **Read this before changing production behavior.**

**New machine or new Grok chat?** Start with **[START_HERE.md](START_HERE.md)** (clone, tests, bootstrap prompt), then this file, then **[docs/SESSION_HANDOFF.md](docs/SESSION_HANDOFF.md)**.

**Repo (GitHub):** `https://github.com/chindonanKen/mexc-alert-bot` · branch `main`  
**Local folder:** `~/mexc-bot` (Mac mini or MacBook — same clone) · **droplet:** often `~/mexc-alert-bot`  
**Owner use case:** daytrading MEXC; primary edge is **sharp downside / panic dumps** (AD / average-drop style scale-ins). Movers are the high-value feature.  
**Dev vs prod:** Grok Build on the **laptop** is for code; **live bot stays on DigitalOcean**. Grok sessions are **not** portable between machines — **git + these docs** are.

---

## What this project is

**Telegram bot** (Python 3.11+, Docker on DigitalOcean):

| # | Feature | Notes |
|---|---------|--------|
| 1 | **Target price alerts (V1)** | One-shot when MEXC **spot** price crosses target |
| 2 | **Futures targets (V3)** | Same one-shot on **USDT-M futures** (`/af`) |
| 3 | **Downside movers (V3)** | Watchlist scanner: ≥ X% **down** within lookback (default 15m). Spot + futures mix. Cascades re-alert via step-down |

Stack: long-poll Telegram (`pyTelegramBotAPI`), **public** MEXC REST only (no trading keys).

---

## Non‑negotiable safety rules

1. **Do not break existing spot target alerts.** Live production alert sets are large.
2. **Feature flags default OFF** in code / `.env.example`. Production enables them in droplet `.env`.
3. **Additive DB only** — never rewrite or drop `alerts` rows in migrations.
4. **Movers must not delete target alerts.** Separate tables + `MoverScanner` (no shared fire/remove path with `PriceMonitor`).
5. **Prefer `/mw add` / `/mw remove`** over bare `/mw SYMBOL…` (replace-all wipes the whole watchlist).
6. **Never commit `.env`** or bot tokens. Templates only: `.env.example`, `.env.staging.example`.
7. **Staging** uses `./data-staging` (and preferably a second BotFather token) — never prod `./data`.

---

## Architecture (mental model)

```
Telegram user
    │  commands + notifications
    ▼
bot.py  ─────────────────────────────►  AlertStore (SQLite: alerts)
    │                                         ▲
    │         PriceMonitor (thread)  ─────────┘  one-shot fire → remove by stable_id
    │              │
    │              ├── MexcClient (spot /ticker/price)
    │              └── MexcFuturesClient (futures /contract/ticker)
    │
    └── MoverStore + MoverScanner (thread)
            │  peak drawdown + step-down re-arm; never touches alerts
            ├── spot and/or futures books from watchlist markets
            ├── PriceHistory ring buffer
            └── enrichments: velocity, volume, heat board, optional kline reds
```

| Module | Role |
|--------|------|
| `mexc_bot/main.py` | Wire settings, stores, providers, monitor, movers, polling. **Pass `futures_provider` into bot whenever the client exists** (movers-only still needs resolve for `/mw add f TSLA`). |
| `mexc_bot/config.py` | Env → `Settings` |
| `mexc_bot/bot.py` | All Telegram handlers; plain-text replies |
| `mexc_bot/monitor.py` | Target-price loop; stable_id crossing |
| `mexc_bot/exchange.py` | Spot + futures clients; **stock/crypto futures resolve** |
| `mexc_bot/storage.py` | SQLite `alerts` + visual ranks + `market` |
| `mexc_bot/movers/scanner.py` | Mover loop: peak/step fire, cascade anchors, enrichments, heat auto |
| `mexc_bot/movers/history.py` | Ring buffer; `peak_drawdown` |
| `mexc_bot/movers/storage.py` | `mover_settings` + `mover_watchlist` |
| `mexc_bot/movers/velocity.py` | `%/min` + PANIC/FAST/GRIND bands |
| `mexc_bot/movers/heat.py` | Ranked dump board (auto + `/mw`) |
| `mexc_bot/movers/klines.py` | Optional consecutive closed red counts (5m/15m/1h/4h) |
| `tests/` | Crossing, V3 resolve/movers, enrichment |
| `docs/V3_TESTING_AND_PROMOTION.md` | Staging → prod checklist |
| `docs/FUTURE_STRATEGY_BOTS.md` | Separate strategy-bot backlog (not movers) |
| `docs/SESSION_HANDOFF.md` | Latest session status + deploy notes |
| `docker-compose.yml` | `mexc-bot` + `mexc-bot-staging` (profile `staging`) |

---

## Feature flags & mover env (`.env`)

### Core flags

| Variable | Default | Effect |
|----------|---------|--------|
| `FEATURE_FUTURES_ALERTS` | `false` | `/af`, futures rows in `PriceMonitor` |
| `FEATURE_MOVER_SCANNER` | `false` | `/movers`, `/mw`, `MoverScanner` |

### Mover timing / cascade

| Variable | Default | Effect |
|----------|---------|--------|
| `MOVER_LOOKBACK_SECONDS` | `900` | Default rolling window (`/movers set` overrides per user) |
| `MOVER_THRESHOLD_PERCENT` | `5` | Default downside % from **high within window** |
| `MOVER_POLL_SECONDS` | `5` | Scanner cadence (code floor **2s**) |
| `MOVER_COOLDOWN_SECONDS` | `45` | **Min-gap** between fires only — **not** a long mute. Prod must not leave `1800` if cascade matters |
| `MOVER_RECOVERY_PERCENT` | `3` | Bounce above last-fire **anchor** clears cascade → peak mode again |
| `MOVER_MARKETS` | `both` | Idle fallback; live scans follow watchlist row markets |

### Mover enrichments (scanner-owned; never touch `alerts`)

| Variable | Default | Effect |
|----------|---------|--------|
| `MOVER_ENRICH_VELOCITY` | `true` | `%/min` + PANIC / FAST / GRIND on fires |
| `MOVER_VELOCITY_PANIC` | `2.0` | %/min ≥ this → PANIC |
| `MOVER_VELOCITY_FAST` | `0.8` | %/min ≥ this → FAST (else GRIND) |
| `MOVER_ENRICH_VOLUME` | `true` | 24h vol line when futures ticker provides it |
| `MOVER_ENRICH_KLINES` | **`false`** | Red-candle streaks via klines; **opt-in** (extra API; closed candles only) |
| `MOVER_HEAT_AUTO` | `true` | Auto **PANIC BOARD** when many watchlist names dump |
| `MOVER_HEAT_BREADTH_MIN` | `3` | Min dumping names to auto-push board |
| `MOVER_HEAT_TOP_N` | `5` | Top rows on board |
| `MOVER_HEAT_MIN_GAP_SECONDS` | `45` | Anti-spam for auto board |
| `MOVER_HEAT_REFRESH_SECONDS` | `90` | Refresh interval for similar board |
| `MOVER_HEAT_ON_MW` | `true` | Show heat ranking on `/mw` |

Other: `PRICE_POLL_INTERVAL_SECONDS`, `ALERT_TOLERANCE_PERCENT`, `ALERTS_FILE`, `MEXC_API_BASE`, `MEXC_FUTURES_API_BASE`.

**Kill switch:** set `FEATURE_*=false` and `docker compose up -d mexc-bot`.

---

## Data model

### Target alerts — table `alerts`

| Column | Notes |
|--------|--------|
| `id` | Stable PK. **Crossing history keys on this**, not visual rank |
| `user_id` | Telegram user id |
| `symbol` | Spot: `BTCUSDT`. Futures: `BTC_USDT`, `TSLAUSDT`, `TSLASTOCK_USDT`, … |
| `price` | Target |
| `enabled` | 0/1 |
| `market` | `'spot'` (default) or `'futures'` — additive |

Visual id in `/l` = 1-based rank by `id ASC`. Monitor removes by **stable_id**. One-shot: fire → notify → delete.

### Movers — same DB file, separate tables

- `mover_settings` — per-user enabled, threshold %, lookback  
- `mover_watchlist` — `(user_id, symbol, market)` — spot + futures mix  
- Cascade **anchors** + min-gap + heat anti-spam: **in-memory** (reset on restart)

---

## Telegram commands

### Spot targets (always)

| Command | Action |
|---------|--------|
| `/a BTC 65000` | Spot one-shot |
| `/l` `/r` `/t` | List / remove / toggle |
| `/p BTC` | Spot price |
| `/s` | Status + flags + mover health |
| `/clearall confirm` / `/disableall` | Wipe or disable all targets |

### Futures targets (`FEATURE_FUTURES_ALERTS`)

| Command | Action |
|---------|--------|
| `/af BTC 65000` | Crypto perp |
| `/af TSLA 250` | Stock perp (resolve to live id) |
| `/p f TSLA` | Futures price |

### Movers (`FEATURE_MOVER_SCANNER`)

| Command | Action |
|---------|--------|
| `/movers on\|off\|set 7 15\|list` | Enable / params / status |
| `/mw` | Watchlist + optional heat |
| `/mw add f BTC ETH` | Futures (no wipe of spot) |
| `/mw add s SIREN` | Spot book only |
| `/mw add f:BTC s:SIREN` | Mixed |
| `/mw remove …` / `/mw clear` | Edit list |
| Bare `/mw BTC ETH` | **Replaces entire list** — avoid when mixed |

---

## Mover detection model (do not regress)

1. **First fire (peak):** rolling **high → now** drawdown over lookback ≥ threshold. No candle-close wait.  
2. **Cascade (step):** after fire, `anchor = price_now`. Next fire when another full threshold **below anchor**.  
3. **Min-gap:** `MOVER_COOLDOWN_SECONDS` (default 45s) only blocks rapid double-sends.  
4. **Recovery:** price ≥ anchor × (1 + recovery%) → clear anchor (skip fire that cycle) → peak mode next.  
5. **Enrichments** attach to messages; klines **off by default**. Heat board can push without `/mw`.

Message shapes:
- Peak: `−X% within 15m · High → now`  
- Step: `−X% step from last alert · Last → now`  
- Plus velocity / volume / optional reds  

**Spot vs futures:** never mix books for the same name.

---

## Futures / stock symbol resolve (TSLA, etc.)

**Problem:** MEXC stock perps are **not** always `BASE_USDT`. Live forms include:

| Form | Example |
|------|---------|
| Compact UI | `TSLAUSDT` |
| Legacy STOCK | `TSLASTOCK_USDT`, `ZHIPUSTOCK_USDT` |
| Crypto | `BTC_USDT` |

**Resolve path:** `MexcFuturesClient.resolve_symbol` → `resolve_futures_symbol` against **live** contract ticker keys (`exchange.py`). Aliases: `TESLA`→`TSLA`, etc. (`FUTURES_BASE_ALIASES`).

**User commands that work for Tesla-like names:**
- `/af TSLA 250`, `/p f TSLA`, `/mw add f TSLA` (or `TESLA`)
- **Not** spot `/a TSLA` / `/mw add s TSLA` unless a real spot pair exists

**Wiring:** `main.py` must pass `futures_provider` into `create_bot` whenever the futures client is constructed (movers and/or futures alerts). Do **not** gate bot resolve on `FEATURE_FUTURES_ALERTS` only.

---

## Shipped vs not shipped (orientation)

### Shipped (movers / V3 path)

- Futures targets + mixed movers watchlist  
- Peak drawdown (high→now), faster poll  
- Step-down cascade re-arm + recovery + short min-gap  
- Velocity, volume, auto heat board, heat on `/mw`  
- Optional red-candle counts (code present, **default off**)  
- Compact + STOCK + crypto futures resolve (TSLA class)  

### Not shipped (do not assume present)

- Per-coin named **buckets** (different %/lookback per group)  
- Bounce/reclaim alerts, layer planner, defensive-mode messaging  
- Telegram inline buttons / deep links / web UI  
- Full kline **OHLC fire path** (only optional red tags)  
- Separate strategy bots (see `docs/FUTURE_STRATEGY_BOTS.md`)  

---

## Important implementation details

### Crossing keying

`PriceMonitor._last_prices` keyed by `(user_id, stable_id)`, **not** visual rank. See `tests/test_crossing_and_remove_logic.py`.

### Telegram formatting

- Command replies: **plain text** (`parse_mode=None`). Markdown breaks on `_` in futures symbols.  
- Fire notifications: **HTML** with escaped symbols.

### Futures cache vs movers

`MexcFuturesClient` ~2 min cache for resolve/`get_price`. Movers use `get_all_prices()` which **force-refreshes**. Cache does **not** make movers 2 minutes late.

### Klines (`MOVER_ENRICH_KLINES`)

- Optional **tags only**; do not block fire if kline API fails.  
- Closed candles → slight lag on the tag, not the dump trigger.  
- Leave **false** until user wants red overview on messages; then set `true` and restart.

---

## Local dev

```bash
cp .env.example .env   # TELEGRAM_BOT_TOKEN
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
make test
python -m mexc_bot.main
```

---

## Docker / droplet deploy

```bash
cd ~/mexc-alert-bot          # droplet path
git pull origin main
# edit .env if needed (flags, MOVER_*, never commit)
docker compose up -d --build mexc-bot
docker logs --tail 80 mexc-alert-bot
```

Staging:

```bash
cp .env.staging.example .env.staging
docker compose --profile staging up -d --build mexc-bot-staging
```

Healthcheck: `/app/data/alerts.db`. SQLite perms: `chown -R 1000:1000 data` if needed.

**Prod checklist after pull:** `/s`, `/l` (targets intact), `/p f TSLA`, `/mw`, watch one real dump for MOVER + optional heat board.

---

## Tests agents must keep green

```bash
make test
# or:
python3 tests/test_crossing_and_remove_logic.py
python3 tests/test_v3_futures_and_movers.py
python3 tests/test_mover_enrichment.py
```

Covers: stable_id crossing, market isolation, stock resolve (compact `TSLAUSDT` + STOCK), peak/step cascade, recovery, min-gap, heat/velocity, alerts table isolation.

---

## How to extend safely

| Goal | Where | Notes |
|------|--------|------|
| New Telegram command | `bot.py` | Gate V3; keep `/a` unchanged |
| Mover fire logic | `movers/scanner.py` + `history.py` | No `AlertStore` deletes |
| Enrichment | `movers/velocity|heat|klines.py` | Fail soft; flags default sensible |
| Symbol resolve | `exchange.py` | Prefer live `known` set |
| Stock aliases | `FUTURES_BASE_ALIASES` | e.g. TESLA→TSLA |
| Separate strategy bot | new module + flag | See `docs/FUTURE_STRATEGY_BOTS.md` |

---

## What not to do

- Don’t key monitor history by visual alert id  
- Don’t reintroduce global Markdown parse mode  
- Don’t put mover rules in the `alerts` table  
- Don’t point staging at production `./data`  
- Don’t assume futures id is only `BASE_USDT` or only `*STOCK*`  
- Don’t set long `MOVER_COOLDOWN_SECONDS` (e.g. 1800) if cascade re-alerts matter  
- Don’t enable `MOVER_ENRICH_KLINES` in prod without a heads-up (API load + message noise)  
- Don’t lower prod mover thresholds for “testing” without telling the user  

---

## Related docs

| Doc | Purpose |
|-----|---------|
| `README.md` | Product overview |
| `AGENTS.md` | **This file** — primary agent guide |
| `docs/SESSION_HANDOFF.md` | Latest build + what was done / next |
| `docs/V3_TESTING_AND_PROMOTION.md` | Staging → prod |
| `docs/FUTURE_STRATEGY_BOTS.md` | Future separate bots backlog |
| `.env.example` | Full env template |

---

## Quick status check (production)

```text
/s          → flags, mover cycle / anchors / min_gap
/l          → target alerts still present
/p BTC      → spot
/p f TSLA   → futures resolve (expect TSLAUSDT or *STOCK* form)
/mw         → watchlist [F]/[S] + heat if on
/movers list → threshold, lookback, step-down copy
```

If targets vanish or mass-fire: check `monitor.py` / `storage.py` stable_id logic; run crossing tests immediately.
