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

**Foundation (source of truth):**  
Every AD rule, entry, size, and skip is **dictated by the actual history of this chart on the working timeframe** — swings, drops, bounces, volume, and failed ADs that already printed.  

- **The chart decides.** Not a global formula, not a canned 3–5 reds, not what the user *says* they prefer in the moment.  
- Human teach (chips / note) records **how Kenneth traded the dump** (took, skip, pride). It does **not** overwrite measured history (AD length, typical red count, vol personality).  
- If this symbol’s 4h history bottoms after two reds with climax vol, **that** is the rule on 4h — not a textbook “wait for the 4th.”  
- If this range has no history yet (new high / new low), **wait for history to form**. Do not import another TF’s or another range’s story.

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

- Measure top → bottom **on one timeframe at a time**. A 5m AD, a 1h AD, and a 1D AD are **different objects** — do not mix their lengths.
- **Initial Drop:** after a **new high / price-discovery** phase, the **first significant pullback** defines the reference AD for that *new higher* range. Do not drag old low-range ADs into brand-new highs without a new Initial Drop.

### 3.2 Rule stack (must apply in order for recommendations)

These rules are **how to read history**, not a script that overrides it. Walk 1–8, but **every answer is measured on this TF in this range.** If history and a written example disagree, **history wins.**

#### Rule 1 — Range: history vs price discovery

1. Is price in a **familiar range** with prior AD behavior on *this* chart?  
   - **Yes** → use the best historical AD length for that range.  
2. **New high territory** (meaningful new highs)?  
   - Wait for / identify **Initial Drop**; that sets the new AD.  
3. **New low territory** never really traded?  
   - **Don’t drag old high-range ADs** into the new lows — those lengths belong to the old regime.  
   - Wait for ADs to **form** in the new zone.  
   - Wait for **high-volume selling spikes** (panic participation). Quiet/new-low drips without climax volume are not a usable AD yet.  
4. Always **re-measure** when the regime changes.  
5. **AD is timeframe-dependent** — pick **one** working TF using the procedure below. Never blend two TFs into a single “average AD.”

##### How the agent picks the timeframe (Rule 1.5)

**General idea (this is the rule):**  
Every timeframe has its **own** AD personality. A bounce-quality drop on 15m is not the same length, duration, or reliability as the drop on 1h, 4h, 8h, or 1D — same coin, same range. The agent’s job is **not** to use a fixed TF list. It is to **look across whatever TFs actually show structure on this chart**, then pick the AD that historically **worked in this range**, and only then mix in volume, panic/grind, and bases.

Examples of TFs you may see: 5m, 15m, 1h, 4h, 8h, 12h, 1D, or whatever the book actually trades. **The set is open.** Do not refuse a valid 8h AD because it was not in a template list. Do not force 5m/1h/1D if those TFs are noisy or empty in this range.

**Step A — Measure separately on TFs that have real swings here.**  
For each TF that shows clear tops/bottoms **in the current price range**, list ADs that bounced or failed. Ignore other ranges (Rule 1.1–1.3). Skip TFs with no readable history.

**Step B — Rank by historical success in this range.**  
Prefer the TF whose AD **most often** led to a usable bounce here (hit rate / bounce quality) — not the deepest wick, not the prettiest chart, not a default “we always use 15m.”  
If a TF has **too few** completed ADs in this range → do not pick it; say so and look at another TF.

**Step C — Confirm or veto with context (do not skip this).**  
The winner from B is a **candidate**, not automatic:

| Factor | How it changes the pick |
|--------|-------------------------|
| **Volume** | High-volume selling into the low **supports** the candidate. No volume / dead tape → veto or wait (see also Rule 2 / 7). |
| **Panic vs grind** | PANIC / FAST on that TF supports taking *that* TF’s AD. GRIND → skip, wait-deeper, or a **higher** TF only if *that* slower TF’s AD has history — do not force a fast-TF AD onto a slow grind. |
| **Base levels** | A major base just below is **risk**, not a reason to switch TF. Do not ride a fast-TF AD through a HTF base; shrink size, widen layers, or **no-trade** if breakdown risk dominates (Strategy B as filter). |

**Step D — Use two TFs at most, different jobs.**  
- **Structure TF** (whichever slower TF won on success-in-range): which AD **zone** matters.  
- **Timing TF** (a faster TF that shows volume/panic): where the dump **lands inside** that zone.  
If structure and timing disagree (fast TF’s AD already complete, slower TF’s AD not even started) → **wait** or **skip**. Do not average the two prices.

**Agent must state:** `tf_structure`, `tf_timing` (actual intervals used, not a canned list), `ad_len` on the chosen TF, **why that TF won** (success in range), and any veto (volume / grind / base).

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

#### Rule 2.5 — Factors from *this* chart’s history; size to the stack

**Source of truth:** the printed history on the **working TF** in the **current range**. Written examples (e.g. “3–5 reds”) are only *how other charts often looked* — they are **not** this coin’s law unless this coin’s history agrees.

**Collect factors from history + this dump** (yes / weak / no). “Yes” means *this chart used to bounce / fail when that condition printed*:

| Factor | Measure on this TF / range |
|--------|----------------------------|
| **AD in range** | This chart’s own bounce-quality drop length (Rule 1 / 1.5) |
| **Volume** | How *this* book printed climax vs dry on dumps that bounced vs failed |
| **Pace** | Whether *this* chart’s winners were panic or also worked on grinds |
| **Red streak** | How many closed reds *this* TF usually needed before a usable low (count: `close < open`, newest→older). If history bottoms on the 2nd red, that is the tell — not a global 3–5. |
| **Regime** | Same range only; new highs/lows have no imported AD |
| **Breadth** | Whether *this* name paid on isolated dumps or only with market heat |
| **Bases** | HTF bases *on this chart* as risk / size, not a reason to invent an AD |

**How to act**

- **Enter when enough of *this chart’s* factors line up** with the live dump.  
- **Size to the stack:** few matches → lean / one layer; many matches to past winners → standard or press.  
- Do **not** skip solely because the dump is the 1st red, and do **not** require a canned “AD + panic vol” combo, unless **this** history says those were required.  
- Human speech / chips never invent an AD length or a red count the candles did not print.

**How reds are counted (same TF only):** closed candles, `close < open`, walk newest→older until a non-red. Doji or green **breaks** the streak. Forming candle does not count. Store `red_streak` plus **this TF’s historical typical streak** when known.

**Agent must state:** working `tf`, what history on that TF actually showed, live factor board vs that history, `alignment`, **size hint**. Never “the user said wait for 5 reds.”

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

AD **choice of TF** is Rule 1.5 (not optional flavor). Indicators only **confirm** the candidate.

| Tool | Role |
|------|------|
| **Visual AD + structure (per TF)** | Primary — one structure TF, optional timing TF |
| **Volume (esp. ~5m)** | Confirm panic / selling climax on the timing TF |
| **RSI** | In **strong downtrends**, bullish divergence can support bounce thesis — secondary |
| **HTF trend / bases** | Context: bounce odds after AD; breakdown risk; aggression on base breaks |
| **Movers velocity** | Operational proxy for “sharp vs grind” (PANIC / FAST / GRIND) on the dump |

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

1. [ ] Range: familiar AD history vs price discovery? New lows → no old high-range ADs; wait for high-volume selling spikes.  
2. [ ] Timeframe: which **actual** TF’s AD has the best bounce history **in this range** (not a fixed 5m/1h/1D menu)? Structure TF + timing TF stated? Volume / panic-grind / bases confirm or veto?  
3. [ ] Quality: panic vs grind? market-wide vs isolated? volume?  
4. [ ] Structure: AD zone / Initial Drop identified **on the chosen TF**?  
5. [ ] History on the working TF: which of *this chart’s* factors lined up? Size matches the stack. User opinion does not override printed candles.  
6. [ ] Layers: count, zones, exponential weights (only if tradeable)?  
7. [ ] Extension: entry opportunity vs defensive exit mode?  
8. [ ] Failed AD criteria defined?  
9. [ ] Behavioral risk: pride / greed / early exit / news FOMO?  
10. [ ] If low conviction → **No trade** only — no contradictory plan.  

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
