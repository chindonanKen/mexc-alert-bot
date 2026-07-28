# Kenneth’s trading strategy — agent reference

**Purpose:** Give Grok Build (and future alert-bot “coach” agents) a deep, operational map of how Kenneth trades.  
**Use this when:** designing alerts, movers logic, news/panic filters, recommendations, learning loops, or any agent that guides live decisions.

**Not financial advice.** This is a personal playbook encoded for software that *assists* Kenneth; it does not auto-trade unless a separate product decision says so.

**Sources (synthesized):**  
- `ad-theory-trading-agent/knowledge/ad_theory.md` + `ad_theory_rubric.md`  
- Trade Bible notes (style, flaws, weaknesses, improvements)  
- Live use of **mexc-alert-bot** movers (panic dumps, cascade steps, velocity/heat)  
- Product intent: set levels/alarms → step away → engage when tools fire  

**Related code docs:** [AGENTS.md](../AGENTS.md) · [SESSION_HANDOFF.md](SESSION_HANDOFF.md) · [FUTURE_STRATEGY_BOTS.md](FUTURE_STRATEGY_BOTS.md) · [START_HERE.md](../START_HERE.md)

---

## 1. Identity of the trader

Kenneth is a **discretionary crypto (and stock-perp) day/swing trader** on **MEXC**, oriented around:

| Attribute | Description |
|-----------|-------------|
| **Primary style** | **Average Drop (AD)** — visual mean-reversion into dips |
| **Secondary style** | **Base breaks** on mid/large caps (context-dependent aggression) |
| **Horizon** | Typically **1 day → ~1 week** (not multi-month holds as default) |
| **Venue** | MEXC spot + USDT-M futures (including stock perps like TSLA) |
| **Edge he has proven with tools** | Being **present at sharp downside** (movers) beats waiting on static price alarms alone |
| **Relationship to tools** | Alarms and scanners should **reduce screen time** and enforce plan; not create FOMO noise |

**Core sentence:**  
*Find charts that respect their own average drops; scale in on **panic** with volume; layer out on meaningful bounces; treat grinds and isolated news dumps as high risk.*

---

## 2. Core philosophy

### 2.1 Visual, chart-specific, history-first

- AD is **visual**, not a fixed global formula (not “always −7% on everything”).
- **Each chart** has its own “personality”: typical drop length, bounce quality, how many candles before a bounce, tendency to fail.
- **Same general price range** matters: measurements from an old high regime often fail in a new low regime (and vice versa).
- Prefer AD lengths that historically gave **many reliable bounces with manageable downside**, not the absolute lowest wick.

### 2.2 Panic over grind

| Preferred | Avoid / de-risk |
|-----------|-----------------|
| Sharp, fast dumps | Slow multi-hour/day grinds |
| Market-wide selling | Isolated single-coin collapse |
| Volume expansion into the low | Big move, no volume |
| Clear AD history on the chart | Price discovery with no AD history yet |

**Why:** Sharp moves often reflect **panic liquidity** → higher chance of mean-reversion bounce. Grinds = less panic, more trend/structural risk.

### 2.3 Scale in (and out), never all-in on hope

- **Exponential layering** into weakness (small first, larger deeper).
- Keep powder for extensions beyond the first AD.
- Exits also layered; don’t dump the whole book on the first green candle unless **defensive mode** is active.

### 2.4 Plan → alarm → leave the screen

From personal flaws notes: over-watching news/charts causes emotional errors.  
**Ideal workflow:** mark levels / watchlist → **alarms do the waiting** → engage only when something meaningful fires.

Agents should **reinforce this workflow**, not invite constant chart-staring.

---

## 3. Strategy A — Average Drop (primary)

### 3.1 What an AD is

An **Average Drop** is the typical **visual distance** from a local top (or swing high) to a local bottom (swing low) that this chart tends to print in the **current price regime**.

- Measure top → bottom on the timeframe Kenneth is actually trading (often short TF for entry timing; higher TF for regime).
- **Initial Drop:** after a **new high / price-discovery** phase, the **first significant pullback** defines the reference AD for that *new higher* range. Do not drag old low-range ADs into brand-new highs without a new Initial Drop.

### 3.2 Rule stack (must apply in order for recommendations)

These come from the AD rubric. Any coach agent should **walk Rules 1–8** before advising.

#### Rule 1 — Range: history vs price discovery

1. Is price in a **familiar range** with prior AD behavior on *this* chart?  
   - **Yes** → use the best historical AD length for that range.  
2. **New high territory** (meaningful new highs)?  
   - Wait for / identify **Initial Drop**; that sets the new AD.  
3. **New low territory** never really traded?  
   - Wait for ADs to **form** in the new zone; don’t force old high-range ADs.  
4. Always **re-measure** when the regime changes.

#### Rule 2 — Setup quality

**High conviction**

- Sharp, fast, panic-like drop  
- Broad market selling (BTC/ETH or many alts dumping together)  
- Volume spike near/into the low  
- Price into or slightly beyond a **respected** historical AD  

**Low conviction / higher risk**

- Slow grind  
- Isolated dump (coin-specific news / narrative)  
- Approaching major base from above (breakdown risk)  
- Slow-forming AD (takes much longer than usual → less panic)  
- **No volume** on the decline (“no volume, big move” = caution)

#### Rule 3 — Entry layering

- Default **5–10 layers**, **exponential** size (early small, later larger).  
- Start conservative; widen spacing and shrink early size near major bases or risky setups.  
- On **deep extensions** past AD: later layers can be more aggressive (best panic prices).  
- Recommendations should be **concrete** when conviction is high:
  - layer count  
  - price zones  
  - relative size weights (not necessarily $ amounts)  
  - why spacing/size differs from default  

#### Rule 4 — Extensions (entry vs exit are different)

| Role | Behavior on deep extension past AD |
|------|-------------------------------------|
| **Entries** | Often **best** opportunity — scale **into** panic |
| **Exits** | Switch to **defensive mode** — don’t expect full normal bounce; take weak bounce for small loss / BE / small win |

On **broad market panic**, prefer **not** to panic-exit early; let the event complete, then take the real bounce.

#### Rule 5 — Exit layering

- Default idea: multi-layer exits (e.g. 5-slice structure; larger slices often mid–late bounce).  
- Use **chart-specific** bounce history (how long / how far bounces usually run).  
- **Failed AD:** price hits AD zone but **no meaningful bounce** in expected time → **exit to preserve capital**. Do not marry the thesis.

#### Rule 6 — No-trade / risk filters

- Charts with repeated **catastrophic failed ADs** → avoid or tiny size.  
- **Broad panic** vs **idiosyncratic collapse / P&D** — latter is wipeout territory.  
- When rules say no-trade, the agent must be **decisive**:  
  > “No trade recommended. [reason tied to rules].”  
  Do **not** undermine with a full “if you trade anyway…” plan.

#### Rule 7 — Sideways / low vol

- AD lengths shrink when vol dies.  
- Be more selective; wider layer spacing if trading at all.  
- Volume alone is never enough — combine with structure.

#### Rule 8 — Learned examples

- Prefer **chart-specific** lessons (from AD agent learning, journal, past fires).  
- Explicitly cite: “Similar to [symbol] failed AD — defensive exits.”  
- If no history: say so; fall back to core theory only.

### 3.3 Timeframes & indicators (supporting, not primary)

| Tool | Role |
|------|------|
| **Visual AD + structure** | Primary |
| **Volume (esp. ~5m)** | Confirm panic / selling climax |
| **RSI** | In **strong downtrends**, bullish divergence can support bounce thesis — secondary |
| **HTF trend** | Context: bounce probability after AD; aggression on base breaks |
| **Movers velocity** | Operational proxy for “sharp vs grind” (PANIC / FAST / GRIND) |

---

## 4. Strategy B — Base breaks (secondary)

**What:** Larger/medium-cap coins breaking (or retesting) **major bases**.

| Market context | Aggression |
|----------------|------------|
| Uptrend / constructive | More aggressive on base breaks |
| Downtrend / bearish | More conservative |
| Coin near **relative lows** vs its high | History overrides — may treat differently |

**Tension with AD (from flaws notes):**  
Over-using base theory can cause **waiting for bases to be hit** before exiting free coins that should already be scaled out. Prefer:

- AD panic entries for “downside juice”  
- Use bases as **context / risk** (breakdown risk), not the only exit framework  
- Accumulate free coins that can **run toward** bases rather than only trading the break itself in a bear

---

## 5. Risk management principles

1. **Manage risk; don’t expect to lose** — size and structure first.  
2. **Bad news toolkit:** distinguish temporary liquidity news (often tradable dumps) vs **fundamental destruction** (avoid or micro size).  
3. **HTF trend** filters bounce quality after AD.  
4. **Plan the trade and trade the plan** — no impulse top-reversal stabs without AD structure.  
5. **Pride / greed are named enemies** (see §7) — agents should call them out when behavior matches.  
6. **Participation:** prefer liquid books; thin dumps are less trustworthy.

---

## 6. Hold time & trade management

- Default hold: **hours to a few days**, up to about a **week**.  
- Active trades: improve **exit plans** (layered).  
- Free coins: historically weak TP discipline — agent should **prompt** partials on strength.  
- Emotional early exits on good ADs (especially hyped coins): agent should **coach patience** when structure still valid and panic was clean.

---

## 7. Psychology & self-knowledge (critical for coach agents)

### 7.1 Weaknesses (importance order)

1. **Pride** — cannot accept a wrong thesis; holds until “falls to hell.”  
2. **Greed** — poor TP on free / runaway coins.  
3. **Emotion / self-trust** — abandons AD plan mid-trade; exits good ADs too early on hyped names.  
4. **No plan** — occasional improvisation.  
5. **False panic** — jumps top reversals that are really **bad news / hard trend**, not mean-reverting panic.

### 7.2 Flaws pattern (process)

- Selling winners too early on downside-scalps because “it might go lower.”  
- Entering breakout-style trades in the wrong regime (bear) instead of waiting for **panic**.  
- Overusing base theory; underusing free-coin management.  
- **Over-consuming news/videos** → stress → worse decisions.  
- Cure: **set alarms, leave the computer, attend only when levels fire.**

### 7.3 Documented improvements

- Can cut a trade that looks wrong without ego spiral.  
- Uses **5m volume + AD** to time entries (worked well).  
- Better exit planning on active trades (still incomplete on free coins).

### 7.4 How agents should talk to Kenneth

| Do | Don’t |
|----|--------|
| Tie advice to **AD rules** and chart history | Vague “be careful” |
| Label **PANIC vs GRIND vs ISOLATED** | Treat every −7% the same |
| Suggest **layers + zones** when high conviction | All-in / YOLO language |
| Say **no-trade** cleanly | Soften with contradictory full plans |
| Remind **plan / pride / greed** when patterns match | Moralize or lecture endlessly |
| Support **alarm → leave screen** | Encourage constant chart sitting |

---

## 8. How the alert bot fits the strategy

The **mexc-alert-bot** is not the strategy itself; it is the **sensor + trigger layer** for the strategy.

### 8.1 Mapping bot features → strategy concepts

| Bot capability | Strategy role |
|----------------|---------------|
| **Target alerts** (`/a`, `/af`) | Planned levels, layer prices, invalidation, bounce targets |
| **Movers peak high→now** | Detect **dump within window** without waiting candle close |
| **Step-down cascade** | Next layers of panic (another −X% from last fire) without long mute |
| **Velocity PANIC/FAST/GRIND** | Rule 2 quality proxy |
| **Volume line** | Confirm participation (Rule 2) |
| **Heat / panic board** | Market-wide vs isolated (Rule 2 / 6) |
| **Optional kline reds** | Visual continuation context (secondary) |
| **Stock resolve (TSLA…)** | Same strategy on stock perps when listed |

### 8.2 Ideal future learning loop (product direction)

```
Alerts / movers / heat / (news) fire
        │
        ▼
Structured event log (symbol, market, velocity, breadth, time, prices)
        │
        ▼
Kenneth marks outcome (bounce quality, layered?, pride exit?, free-coin TP?)
        │
        ▼
Per-symbol / per-regime stats + coach recommendations
        │
        ▼
Better watchlists, thresholds, “no-trade” warnings, layer suggestions
```

Agents that “learn how to guide better” should optimize for:

1. **Timing quality** — did the fire coincide with tradeable panic?  
2. **Setup quality** — panic vs grind vs isolated  
3. **Behavioral quality** — did Kenneth follow layering / exits / no-trade?  
4. **Outcome quality** — bounce size, drawdown after entry, time to recovery  

---

## 9. Recommendation templates for agents

### 9.1 High-conviction panic AD (example shape)

```text
SETUP: High conviction (Rules 2, 3)
- Regime: familiar range / or Initial Drop defined
- Quality: PANIC velocity + volume + heat breadth (market-wide)
- AD zone: ~[price]–[price] (from chart history / last high)
ACTION:
- Layers 1–N at … (sizes exponential)
- Invalidation / failed AD: if no bounce by … or breaks …
- Exit plan: scale out … ; defensive if extended …
WATCH: next cascade step if −threshold from last fill
```

### 9.2 Grind / isolated (example shape)

```text
SETUP: Low conviction (Rules 2, 6)
- GRIND or isolated dump; volume weak / heat breadth low
RECOMMENDATION: No trade (or micro scout only). Reason: …
```

### 9.3 Pride / early-exit coach

```text
BEHAVIOR FLAG: Early exit risk on valid AD structure
- Structure still intact (no failed AD)
- Reminder: stick to AD on hyped coins; layer out on bounce significance
```

---

## 10. What agents must never assume

- That **Telegram movers %** equals Kenneth’s **visual AD** length (related but not identical).  
- That every heat-board name is a buy.  
- That stock perps behave like mid-cap alts.  
- That “down a lot” always means “good AD.”  
- That recommendations should auto-place orders (out of scope unless explicitly built).  

---

## 11. Implementation guidance for the alert-bot codebase

When building coach/learning features:

| Principle | Implication |
|-----------|-------------|
| Safety | Never break spot targets / stable_id; feature-flag new agents |
| Separation | Learning/coach modules must not delete `alerts` via mover path |
| Signal taxonomy | Store velocity band, heat breadth, market, step vs peak fire |
| User control | Kenneth sets watchlists/thresholds; agent advises, doesn’t hijack |
| Docs | Update SESSION_HANDOFF when learning features ship |

Primary code map: `mexc_bot/movers/*`, `bot.py`, future `coach` / `journal` modules as designed.

---

## 12. One-page checklist (agent pre-flight)

Before any trade recommendation:

1. [ ] Range: familiar AD history vs price discovery?  
2. [ ] Quality: panic vs grind? market-wide vs isolated? volume?  
3. [ ] Structure: AD zone / Initial Drop identified?  
4. [ ] Layers: count, zones, exponential weights (only if tradeable)?  
5. [ ] Extension: entry opportunity vs defensive exit mode?  
6. [ ] Failed AD criteria defined?  
7. [ ] Behavioral risk: pride / greed / early exit / news FOMO?  
8. [ ] If low conviction → **No trade** only — no contradictory plan.  

---

## 13. Document maintenance

| When | Action |
|------|--------|
| Strategy changes | Edit this file; bump date in SESSION_HANDOFF |
| New bot sensors (news, OI, …) | Add mapping in §8 |
| New personal lessons | Add under §7 or chart-specific appendix |

**Owner:** Kenneth Johansson  
**Canonical location:** `docs/TRADING_STRATEGY.md` in `mexc-alert-bot`

<!-- agents: search TRADING_STRATEGY or "Average Drop" -->
