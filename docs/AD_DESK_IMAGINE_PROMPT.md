# Grok Imagine — AD Desk visual design brief

Use this prompt (or the short version) with **Grok Imagine** to generate high-fidelity UI references. Goal: make AD Desk look like a **serious panic-trading command surface**, not a generic dashboard or “AI coach theater.”

## Primary prompt (paste into Imagine)

```text
Design a premium dark-mode trading desk UI mockup for "AD Desk" — a solo daytrader’s command center for MEXC panic dumps and average-down (AD) scale-ins. Ultra-clean fintech / Bloomberg-terminal hybrid, not crypto-casino neon.

Layout (desktop 16:9, then a second frame mobile 9:16):
1) Top thin status bar: connection · XAI voice ready · live clock · user id pill
2) Left or top nav icons only: Overview · Positions · Movers · Targets · Learning · Intel
3) Main Overview stack (vertical priority hierarchy, not equal cards):
   - NEEDS YOU strip (max 2 pending questions) — amber/rose urgency, one-tap answers
   - TARGETS row (compact chips)
   - MOVERS board (ranked dump list with PANIC/FAST tags, set badges)
   - POSITIONS money truth (open futures + spot, layers, PnL exchange-backed)
   - BOOK INTEL (only when symbol-matched delist/news — empty state is fine, no spam)
   - AGENT MEMORY strip (short lessons learned — student memory, NOT coach recommendations)
4) Bottom global VOICE CALL DOCK: single “Start call” primary control, waveform when live, always reachable

Visual language:
- Background: deep charcoal #0B0D10 to #12151A, subtle grid or noise, no purple gradients
- Accents: electric lime/cyan for live marks; danger red for dumps; amber for “needs you”
- Typography: Inter / SF Pro style, tight tabular nums for prices, high contrast
- Cards: soft 1px borders, 12–16px radius, dense but breathable (8pt grid)
- No stock illustrations of robots or “AI brain”. No Super-Agent theater. No cluttered charts wall.
- Data density like a pro desk: scannable tables, clear hierarchy, hover states implied

Mood: calm under stress — when markets dump, the UI stays readable and decisive.
Include micro-labels: “teach_ok”, “money truth”, “mover set: Panic 7%”.
```

## Variant prompts

**Movers multi-set screen**

```text
Dark trading UI screen “Movers” for AD Desk: multi-set watchlists. Top: set switcher pills (Default · Panic 7% · Grind 4%). Each set shows On/Off, threshold %, lookback minutes, and a dense coin table (F/S · symbol · mark · 24h). Add-set and rename controls. Panic/red accents on dumping rows. Same AD Desk visual system as a serious MEXC daytrading desk.
```

**Learning teach screen**

```text
AD Desk Learning V1 UI: “You teach · agent is student”. Trade-first selector (open/closed positions), behavior chips (plan_ok, ad_met, ad_missed, hesitant…), free-text lesson box, What I’ve learned list with delete. No coach briefs, no approve-draft theater. Dark charcoal fintech aesthetic, amber for pending answers.
```

## How we’ll use the outputs

1. Generate 2–3 desktop Overview frames + 1 mobile dock frame.
2. Pick the strongest hierarchy; extract colors, spacing, type scale.
3. Port tokens into `desk.css` (CSS variables) without rewriting product logic.
4. Optional: second pass for Movers multi-set + Learning only.

## Constraints to respect in any redesign

- Hierarchy: **Needs you → targets → movers → positions → intel → memory**
- Telegram is not the learning UI; desk is teach + positions
- Money truth only when exchange-backed
- Voice dock stays global
- No silent live-order chrome
