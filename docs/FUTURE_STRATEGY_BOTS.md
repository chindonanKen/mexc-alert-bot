# Future strategy bots (backlog)

Ideas for **separate** scanners/bots that can run **alongside** AD / movers — not inside them.

**Rules when implementing any of these:**

- Feature flags default **OFF** in code / `.env.example`
- Own tables/modules (or clearly isolated state)
- **Never** delete or rewrite target-price `alerts` rows
- Prefer the same patterns as V3 movers: thread + store + Telegram plain/HTML hygiene
- Keep spot target crossing / stable_id logic untouched

Status: all items **backlog** unless marked otherwise. Revisit with agents when prioritizing the next feature wave.

---

## High interest (strong fit with AD daytrading)

| Bot | Idea | Why it pairs with movers |
|-----|------|---------------------------|
| **Sympathy / sector bot** | Leader dumps or pumps → alert lagging peers in the same theme | “Free coins” / hot-sector plays without staring at 40 charts |
| **Base-break bot** | Multi-day range high break, retest hold, or failed break | Secondary style on mid/large caps; slower than 15m dumps |
| **RSI divergence bot** | Price LL + RSI HL (and reverse) on 5–15m | Downtrend filter before aggressive layering |
| **Bounce / failed-bounce bot** | After a dump: first real bounce, or no bounce within N minutes | “First bounce strongest” + failed-AD exit discipline |
| **Position guardian bot** | Registered entry + invalidation: SL, time-stop, TP layers | Manage 1–7 day holds without hunting new entries |

---

## Full backlog

### Market structure & timing

1. **Base-break bot** — Multi-day range break + retest (or fail).  
2. **Session-structure bot** — Asia H/L, NY open volatility, session VWAP reclaim.  
3. **Failed-breakout fade bot** — Break level then reclaim opposite side (trapped traders).  
4. **Trend-pullback bot** — HTF uptrend + shallow pullback hold (dip-buy, not pure panic AD).

### Panic / mean reversion companions

5. **Sympathy / sector bot** — Theme correlation alerts.  
6. **Volume-climax bot** — Extreme volume + range expansion independent of fixed %.  
7. **Mean-reversion band bot** — Rolling σ / band extremes when no manual AD yet.  
8. **RSI divergence bot** — Momentum vs price for downtrend longs / caution.  
9. **Bounce / failed-bounce bot** — Post-dump recovery or stall.  
10. **Correlation / beta bot** — Coin vs BTC lead/lag (true market panic vs idiosyncratic dump).

### Futures microstructure

11. **OI / funding shock bot** — OI spike + dump, or funding flip (leverage flush context).  
12. **Liquidity / thin-book bot** — Spreads widen / book thins — warn on illiquid “fake” dumps.  
13. **Squeeze / short-relief bot** — Fast +% into resistance (weak pumps / short cover).

### Process & risk

14. **News-impact bot** — Keywords/headlines → temporary tighter mover thresholds.  
15. **Position guardian bot** — Open trade management (SL, time, bounce targets).  
16. **Journal / coach bot** — EOD: fires vs taken/skipped, simple stats for AD agent loop.

---

## Suggested revisit order (when ready)

1. Sympathy / sector  
2. Bounce / failed-bounce (if not already part of mover enrichments)  
3. Base-break  
4. RSI divergence  
5. Position guardian  

Everything else as data/APIs and bandwidth allow.

---

## Explicitly not in this file

Current **mover enrichments** (volume, velocity, heat rank, red-candle tags) live in the main mover plan / `mexc_bot/movers/*` — they extend the existing scanner, they are not “separate strategy bots.”

<!-- agents: search FUTURE_STRATEGY_BOTS or "strategy bots backlog" -->
