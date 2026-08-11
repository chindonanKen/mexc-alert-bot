# AGENTS.md — MEXC Alert Bot

Guide for humans and coding agents. **Read this before changing production behavior.**

**New machine or new Grok chat?** Start with **[START_HERE.md](START_HERE.md)** (clone, tests, bootstrap prompt), then this file, then **[docs/SESSION_HANDOFF.md](docs/SESSION_HANDOFF.md)**.  
**How the owner trades (AD, panic, layers, psychology):** **[docs/TRADING_STRATEGY.md](docs/TRADING_STRATEGY.md)** — required before building coach / learning / recommendation agents.

**Repo (GitHub):** `https://github.com/chindonanKen/mexc-alert-bot` · branch `main`  
**Local folder:** `~/mexc-bot` (Mac mini or MacBook — same clone) · **droplet:** often `~/mexc-alert-bot`  
**Owner use case:** daytrading MEXC; primary edge is **sharp downside / panic dumps** (AD / average-drop style scale-ins). Movers are the high-value feature.  
**Dev vs prod:** Grok Build on the **laptop** is for code; **live bot stays on DigitalOcean**. Grok sessions are **not** portable between machines — **git + these docs** are.

### Two products (do not blur)

| Product | Role | Agent rule |
|---------|------|------------|
| **Telegram alarm bot** | Sensors + push **today** (targets, movers). Already good. | **Leave as-is.** Do not grow learning/coach into Telegram. |
| **AD Desk** | Positions + Overview + **Learning V1 (teach)** + voice tools. | Build teach/student memory here. **No coach product** until owner re-opens it. Must work without Telegram open. |

**Long-term:** multi-device target/mover alarms come **from the desk**, not Telegram. Until desk push ships, the bot keeps alarms; desk owns teach/memory. Shared SQLite for sensor events is plumbing, not “desk depends on Telegram UX.”

### Mandatory QA after every AD Desk iteration (owner 2026-08-09)

Fine-tuning the desk burns tokens on silent regressions. **Do not end a Desk change without `desk-qa`.**

| Step | Action |
|------|--------|
| 1 | Touch desk code (`mexc_bot/webapi/**`, `mexc_bot/learning/**`, desk static) |
| 2 | Run workflow **`desk-qa`** (`workflow` tool `name: desk-qa` or `/workflow desk-qa`) with `focus` = one-line change summary |
| 3 | Fix **blockers**; re-run if FAIL |
| 4 | `python3 scripts/desk_qa_gate.py pass --note "desk-qa PASS: …"` |
| 5 | Only then claim done / deploy narrative |

- Workflow: [`.grok/workflows/desk-qa.rhai`](.grok/workflows/desk-qa.rhai) — 3 agents: **new functions · UI/UX · regressions**  
- Hook gate: [`.grok/hooks/desk-qa-mandatory.json`](.grok/hooks/desk-qa-mandatory.json) — **Stop blocked** while desk edits are dirty/unpassed  
- Rules: [`.grok/rules/00-desk-qa-mandatory.md`](.grok/rules/00-desk-qa-mandatory.md)  
- **Trust once:** `/hooks-trust` so project hooks run on this machine  
- Status: `python3 scripts/desk_qa_gate.py status` · `make desk-qa`  

**Do not** mark pass without running the panel. Docs-only / pure Telegram bot work (no desk paths) is exempt.

Full vision: **[docs/AD_DESK_VISION.md](docs/AD_DESK_VISION.md)** · strategy: **[docs/TRADING_STRATEGY.md](docs/TRADING_STRATEGY.md)** · handoff: **[docs/SESSION_HANDOFF.md](docs/SESSION_HANDOFF.md)**.  
**Autonomy roadmap (canonical):** **[docs/AD_AGENT_PLAN.md](docs/AD_AGENT_PLAN.md)** — P0 shipped → **P1 case factory next** → … → P7 gated live. Full focus = build this agent; desk UI only small edits.

### Platform baseline (owner-accepted 2026-08-03)

| Surface | Status |
|---------|--------|
| **Positions** | **Solid** — exchange money truth (futures open + history, spot balances, discrete closed cycles, buy/sell layers) |
| **Targets / movers** | **Solid** — Telegram sensors + desk CRUD over shared SQLite |
| **Overview** | **Partial** — Needs you · targets · movers · positions · agent memory strip |
| **Learning** | **Learning V1 shipped** — you teach, agent is student (**not** a coach product) |
| **Voice** | **Turn-based beta** — STT → tools → TTS; fluent streaming Voice 2.0 **not** shipped |

### AD Desk product north star (voice-first, teach-first)

| Principle | Detail |
|-----------|--------|
| **Desk = teach + positions platform** | Pending, teach bound to trades, lessons, agent_ask, voice — all AD Desk. No Telegram required. |
| **You teach · agent is student** | Durable lessons from Kenneth; agent does **not** coach or invent process judgment. Super-Agent / coach theater removed from Learning UI. |
| **Money truth sealed** | `$` / PnL teaching only when `teach_ok` / exchange `money_truth` and within `LEARNING_TEACH_SINCE` desk-era window. No dual-truth coach training on junk journal. |
| **Voice first** | Call dock = primary control for desk data + teach tools |
| **Overview hierarchy** | **Needs you** (pending only) → targets · movers · positions · book intel · agent memory strip |
| **Learn before recommend** | Teach soak first; recs / paper later; **no silent live risk** |
| **Alarm bot stable** | Prefer event log / shared DB hooks over rewriting Telegram |
| **Future desk push** | Design notify so alarms can leave Telegram later |
| **Security** | Never commit secrets; **never wipe DB on deploy/rebuild** ([DB_SAFETY](docs/DB_SAFETY.md)); live defaults OFF; voice auth required |

**Learning loop:** **signal** (fires) + **trade** (exchange positions/layers) + **teach** (lessons bound to trade context) — desk primary; see **[Learning environment](#learning-environment-feedback--coach)**.

**Local desk:** `make desk-dev` · seed: `make desk-seed` · HTTPS droplet: `./scripts/desk_https_up.sh`

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
3. **Additive DB only — never wipe data on deploy/rebuild/desk update.** Migrations may only **add** tables/columns (or verified rebuilds that preserve **every** row). No `DROP` of live tables outside `safe_rebuild_table`. No `rm data/`, no `docker compose down -v` on prod. Full rule: **[docs/DB_SAFETY.md](docs/DB_SAFETY.md)** · helpers: `mexc_bot/db_safety.py` · gates: `make db-safety` / `scripts/pre_deploy_db_guard.sh` (wired into `deploy.sh` + `droplet.sh deploy-prod`).
4. **Movers must not delete target alerts.** Separate tables + `MoverScanner` (no shared fire/remove path with `PriceMonitor`).
5. **Movers list protection:** `/mw add` and bare `/mw COIN` are **additive only**. Full wipe needs `/mw clear confirm`. Full replace needs explicit `/mw replace …` and aborts if any symbol fails resolve. `set_watchlist([])` refuses to empty a non-empty list unless `force_empty=True`. Regression: `tests/test_mw_data_safety.py`.
6. **Never commit `.env`** or bot tokens. Templates only: `.env.example`, `.env.staging.example`.
7. **Staging** uses `./data-staging` (and preferably a second BotFather token) — never prod `./data`.

### Database durability (owner 2026-08-11)

| Do | Do not |
|----|--------|
| `CREATE TABLE IF NOT EXISTS` / `ensure_column` | Wipe or recreate `alerts.db` on start |
| `safe_rebuild_table` (abort if row count would shrink) | Hand-rolled `DROP TABLE` + empty recreate |
| Bind-mount `./data:/app/data` (already in compose) | `compose down -v`, delete host `./data` |
| Run `pre_deploy_db_guard` before prod rebuild | Deploy when static scan fails or watchlist empty while movers ON |
| User-facing single-row deletes (alert/lesson/mw) only in APIs | `DELETE FROM` inside `_migrate*` / `_init_db` bulk wipes |

Incident that forced this rule: empty `mover_watchlist` after an unsafe PK migration → silent miss of real dumps (e.g. BLUAI). Restore: `POST /api/watchlist/restore-from-fires`.

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
| `mexc_bot/db_safety.py` | **Additive schema helpers** + protected tables; use for all migrations |
| `mexc_bot/movers/scanner.py` | Mover loop: peak/step fire, cascade anchors, enrichments, heat auto |
| `mexc_bot/movers/history.py` | Ring buffer; `peak_drawdown` |
| `mexc_bot/movers/storage.py` | `mover_settings` + `mover_watchlist` (PK migrate via `safe_rebuild_table`) |
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
| `MOVER_DEDUPE_PRICE_EPS` | `0.002` | Suppress re-fire if price within this fraction of last fire (anti same-price spam) |
| `MOVER_DEDUPE_WINDOW_SECONDS` | `120` | Apply price-eps suppress only within this window after last fire |
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
- Bounce/reclaim alerts, full AD layer planner persistence  
- Telegram inline buttons / deep links  
- Full kline **OHLC fire path** (only optional red tags)  
- Paper agent arena (fictive capital on real marks)  
- Autonomous trade placement / unsupervised live orders  
- Separate strategy bots (see `docs/FUTURE_STRATEGY_BOTS.md`)  

### Shipped / in progress (AD Desk path)

- FastAPI desk + SPA; global voice call dock (turn-based STT → tools → TTS)
- Desk CRUD: alerts, watchlist, movers, journal labels
- Overview hierarchy (partial): Needs you · targets · movers · positions · agent memory
- **Positions money truth:** MEXC `open_positions` + `history_positions` + spot account balances; discrete closed cycles (dust flat eps); buy/sell deal layers on opens; ignore delisted dust (e.g. GOONC)
- **Learning V1 (teach, not coach)** — module `mexc_bot/webapi/learning_v1.py`
  - Trade-first teach: select open or closed trade → bound context → save lesson
  - Closed behavior chips + **`ad_met` / `ad_missed`**; process chip guide
  - Pending answers (max 2), What I've learned, Recent, Ask agent
  - **Delete lesson** (UI + `DELETE /api/learning/lessons/{id}` + voice tool)
  - `teach_ok` / `LEARNING_TEACH_SINCE` gates $ claims; `teach_only` exchange slice
- Engagement bridge plumbing (took|skip|partial|late) + OutcomePoller (flagged)
- Voice tools: teach, what_have_you_learned, pending, fires/trades, agent_ask, delete_lesson

### Not shipped (learning / desk — later)

- **Coach product** (re-opened only by owner; Super-Agent UI removed)
- Fluent streaming / Speech-to-Speech voice (Voice 2.0)
- Paper agents / ranked recs
- AD layer planner persistence
- Full multi-device desk push (notify stub only)
- Silent live orders

---

## Learning environment (teach the agent — V1)

**Product intent (2026-08-03):** Kenneth teaches; the agent is a **student** that stores durable lessons and can **ask back**. It is **not** a coach that invents judgment or trains on dual-truth money. Manual "did you take it?" is the exception — positions/fills drive engagement when confident.

### Three tiers (do not collapse)

| Tier | Content | Writer |
|------|---------|--------|
| **A · Facts** | Fire, prices, band, heat, fills, open/close, exchange PnL | System (MEXC private + sensors) |
| **B · Inference** | took / skip / partial / late (+ confidence) | System (positions/fills/journal bridge) |
| **C · Judgment** | greed, hesitant, fomo, pride, plan_ok, rule_break, process_skip, **ad_met**, **ad_missed** | **Human only** via teach chips / free text — agent does not auto-judge |

Took ≠ good trade. Skip ≠ discipline. Lucky green on a rule-break is still a process fail — **owner** labels that.

### Money truth (non-negotiable for teaching)

| Rule | Detail |
|------|--------|
| Exchange authority | Futures opens from `open_positions`; closed cycles from `history_positions` + fill segmentation; spot opens from account balances (tradeable only) |
| `money_truth` / `teach_ok` | Bundle and agent tools only claim $ when exchange-backed and teach-ready |
| `LEARNING_TEACH_SINCE` | Desk-era window — do not train on pre-desk junk journal mega-blobs |
| Journal dual-truth | Sealed for $ training; journal remains UX/history when useful, not coach money source |

### Loop (V1)

```text
Sensor fire → learning_events
  → engagement bridge → took|skip|partial|late (high conf = silent)
  → low conf → at most 1–2 pending questions (desk/voice only)
  → Owner picks trade (open or closed) → teach (chips + notes) → learning_lessons
  → Open teach → later close teach = new lesson chapters (same symbol OK)
  → Bad lesson → delete_lesson (permanent unteach)
  → agent_ask / what_have_you_learned reads lessons + teach_ok trades only
```

### Auto engagement (bridge rules)

Link unlabeled events on `(symbol, market)` after a **grace window** (**1 hour**, owner default):

| Evidence | Inference |
|----------|-----------|
| New/add long or buy fill near fire | **took** |
| Nothing in journal/fills/positions | **skip** |
| Much later / far from fire | **late** / FOMO candidate |
| Tiny vs normal size | **partial** / hesitant candidate |
| Conflict | **unknown** → ask once |

Label with source metadata (`auto_position` / `human` override). Prefer private reads when `FEATURE_MEXC_PRIVATE_READ`; else journal. **Never** touch `alerts`.

### Behavior codes (closed set → chips / stats)

`plan_ok` · `pride` · `greed` · `hesitant` · `fomo` · `rule_break` · `false_panic` · `process_skip` · **`ad_met`** · **`ad_missed`**  
Free text stays in lesson body / notes, not aggregate keys.

### Ask when unsure (not always)

Silent when confident. Cap **2** open questions. Coalesce same-symbol.  
**Away-safe:** questions **queue only on AD Desk** (badge + Needs you). No Telegram required for the teaching loop. Voice-first when a call is active.

### Desk UI (Learning V1 — current)

Overview top → bottom:

1. **Needs you** — pending questions only (max 2)  
2. **Targets** · **Movers**  
3. **Positions** (exchange-backed)  
4. **Book intel** (only if matched)  
5. **Agent memory strip** — short "what I've learned" (not coach theater)

**Learning view:**

| Block | Role |
|-------|------|
| Pending answers | Queue; one-tap / voice |
| Teach | **Trade-first** — select open/closed row → chips + free text → new lesson |
| What I've learned | Lessons list + **delete** |
| Recent | Compact fires / engagement |
| Ask agent | Student Q&A over memory (not coach briefs) |

No Super-Agent / approve-drafts coach UI. No full unlabeled event feed as hero.

### Voice = learning channel (desk)

Turn-based tools: list events/trades, label, pending answer, **teach**, **what_have_you_learned**, **agent_ask**, **delete_lesson**, strategy cite.  
**Not shipped:** fluent interruptible streaming voice; do not promise it as current work.

### Durable lessons

`learning_lessons` (text, tags/chips, weight, evidence / trade binding). Delete is supported. Teach open then teach again on close = **new lessons**, not overwrite.

### Build order — status (legacy L-labels)

| Phase | Ship | Status |
|-------|------|--------|
| **L1** | Positions money truth + teach_ok window | **Shipped** (= P0) |
| **L2** | Pending queue + Overview Needs you | **Shipped** |
| **L3** | Learning V1 trade-first teach + delete + AD chips + voice tools | **Shipped** (= P0) |
| **L4–L5** | Old coach/recs labels | **Superseded** by P6–P7 gates below |

### AD Agent autonomy plan (canonical — follow this)

**Full doc:** **[docs/AD_AGENT_PLAN.md](docs/AD_AGENT_PLAN.md)**  
**Focus:** build the autonomous AD agent. AD Desk = small edits only unless a phase needs it.

```text
Observe → Freeze case → Decide + log → Grade → AD policy → Paper → Advise → Gated live
```

| Phase | What | Status |
|-------|------|--------|
| **P0** | Truth & teach (money, chips, trade-bound lessons) | **Shipped** |
| **P1** | **Case factory** — `agent_setup_cases`, freeze on fire/teach, Learning snapshot UI | **Core shipped** (index/retrieval → P2) |
| **P2** | Decide + log (`agent_decisions`, nearest-case, soft remind) | Not started |
| **P3** | Grade vs path / `ad_met` / teach_ok PnL | Not started |
| **P4** | AD policy proposals (layers/zones) | Not started |
| **P5** | Paper / replay + pass bar | Not started |
| **P6** | Advise / recs | **Only if owner re-opens + P5 bar** |
| **P7** | Gated live AD | **Default off; never silent** |

**Do not:** skip to coach/live; train $ on non-`teach_ok`; ML day one; expand Telegram learning; break spot `stable_id` crossing.

**Flags:** `FEATURE_LEARNING`; `LEARNING_AUTO_FROM_POSITIONS`; `FEATURE_MEXC_PRIVATE_READ`; `LEARNING_TEACH_SINCE`. Owner `DESK_USER_ID=8630949601`.

Related: [docs/AD_AGENT_PLAN.md](docs/AD_AGENT_PLAN.md) · [docs/AD_DESK_VISION.md](docs/AD_DESK_VISION.md) · [docs/TRADING_STRATEGY.md](docs/TRADING_STRATEGY.md) · [docs/SESSION_HANDOFF.md](docs/SESSION_HANDOFF.md)

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
- **Don’t wipe SQLite on deploy/rebuild/desk update** — no `DROP` live tables, no `down -v`, no empty migration swaps (see [docs/DB_SAFETY.md](docs/DB_SAFETY.md))

---

## Related docs

| Doc | Purpose |
|-----|---------|
| `README.md` | Product overview |
| `START_HERE.md` | New machine / new Grok session |
| `AGENTS.md` | **This file** — engineering safety + architecture |
| `docs/TRADING_STRATEGY.md` | **Owner trading playbook** for coach/learning agents |
| `docs/V4_TRADING_ASSISTANT.md` | Learning / coach / fatal news / voice → fluent agent design |
| `docs/SESSION_HANDOFF.md` | Latest build + what was done / next |
| `docs/DB_SAFETY.md` | **Hard rule:** never erase DB on deploy/rebuild; migration + guards |
| `docs/V3_TESTING_AND_PROMOTION.md` | Staging → prod |
| `docs/FUTURE_STRATEGY_BOTS.md` | Future separate bots backlog |
| `.env.example` | Full env template |

### V4 learning (default OFF)

| Variable | Default | Effect |
|----------|---------|--------|
| `FEATURE_LEARNING` | `false` | Event log on mover fires, `/j` `/events` `/trade` `/brief` `/coach`, outcome poller |
| `LEARNING_OUTCOME_HORIZONS_SECONDS` | `900,3600,14400` | Bounce/DD measurement windows after fires |
| `LEARNING_AUTO_FROM_POSITIONS` | planned | Infer took/skip from journal/fills/positions (see Learning environment) |
| `FEATURE_MEXC_PRIVATE_READ` | `false` | Read-only fills/positions → journal_fills + engagement bridge |
| `FEATURE_NEWS_MONITOR` | `false` | Fatal news (when wired) |
| `FEATURE_VOICE` | `false` | Voice channel into same agent tools |

Tables: `learning_events`, `learning_labels`, `learning_outcomes`, `journal_trades`, `journal_fills` (+ planned lessons / pending questions) — same DB file, **never** delete `alerts`.

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
