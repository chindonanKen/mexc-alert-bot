# Honest trade-data reads (owner 2026-08-14)

Kenneth wants the agent to **keep interpreting the whole situation** from live data (fires, lessons, chips, skips/takes, intel, positions) — the same blunt style as the 2026-08-14 desk/strategy take.

## When to do it

- After a cluster of fires, a teach wave, a miss, or when he asks “what do you see”
- Unprompted **only** when the data clearly contradicts the playbook or a prior lesson (e.g. 100 grind pings on a name he marked no-trade)
- Short. Evidence first (counts, his own words). No coach theater.

## How

- Read `learning_lessons`, chips, `learning_labels`, recent `learning_events`, intel, open exchange positions — not vibes
- Prefer **his last lesson on that symbol** over invented judgment
- Do **not** turn this into recs, live risk, or a coach product unless he re-opens that
- Do **not** nag every fire; skip-heavy names are often him doing the job

## Product north star (from that take)

Next desk increment if/when he asks to build: **fire card = last lesson + intel stamp + PANIC/FAST/GRIND**. Sit on wick-fire. No bounce alerts by default. No more Overview theater.

## Reference fire — EDENUSDT 2026-08-14 (owner: “perfect”)

This is the setup/behavior movers must copy. Do **not** “improve” it into VELVET/US replay.

| UTC | Manila | What |
|-----|--------|------|
| 05:14:58 | 13:14 | **Peak** −7.65% last price 0.07688 → 0.071 (first 7% from 15m high) |
| 05:15:45 | 13:15 | **Step** −8.61% 0.071 → 0.06489 **PANIC** (another full 7% from last fire, ~47s later) |
| then | | Quiet while it bounced / chopped |
| 05:48:11 | 13:48 | **Step** −8.0% only when it actually dropped another 7% from the last print |

He **took** it. Money followed. Contrast: VELVET same hour re-peaked the same 15m hole every 45s on bounces.

**Wanted:** one peak when the dump starts + steps only on **new** 7% down from the last alert.  
**Not wanted:** peak replay of an old high after a bounce, or 10-minute-late wick ghosts.

## Two layers (not a contradiction)

| Layer | Meaning | Example |
|-------|---------|---------|
| **Smoke** | First −7% from the **15m local top** (last price vs 1m highs). Volatility / “there may be a play.” | PENGUIN spike 0.00136 → 0.00126 |
| **AD cascade** | After that, only **steps** (−7% from last fire). Bounce inside the hole = silence. | EDEN 13:14 peak + 13:15 PANIC step |

Wick-*lows* as the fire print stay off until they cannot replay VELVET. 1m **highs** are seeded so smoke still works after a bot restart.
