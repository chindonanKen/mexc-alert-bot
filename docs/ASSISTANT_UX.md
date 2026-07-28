# Trading assistant UX — less commands, more conversation

**Status:** Active direction (2026-07-28)  
**Problem:** Command surface grew with V3/V4 (`/j`, `/trade`, `/mw`, …). Live trading cannot depend on remembering slash grammar.  
**Goal:** The product is a **living trading assistant**. Telegram stays the **push + chat channel**; UX must feel like talking to a desk partner, not operating a CLI.

Related: [V4_TRADING_ASSISTANT.md](V4_TRADING_ASSISTANT.md) · [TRADING_STRATEGY.md](TRADING_STRATEGY.md) · [DROPLET_OPS.md](DROPLET_OPS.md)

---

## Design principles

1. **Capture in one tap** when possible (Took / Skip on a fire).  
2. **Plain language** for common intents (*“skip”*, *“took that”*, *“brief”*).  
3. **Slash commands are power tools**, not the primary UI — progressive disclosure.  
4. **MEXC fills** (read-only, soon) = journal of record; taps/labels = intent.  
5. **Same brain, better shells later** — Telegram first; web/PWA desk is V2, not a rewrite.  
6. **Never break** spot targets / stable_id / prod isolation.

---

## UX layers (what you use when)

| Layer | When | Examples |
|-------|------|----------|
| **A. Fire actions** | Dump/target just fired | Buttons: Took · Skip · Later · (bounce later) |
| **B. Desk home** | Between trades | `/desk` — short menu + status (`brief` for session brief) |
| **C. Chat** | Hands free-ish | “skip”, “took”, “brief”, “what’s open” |
| **D. Power commands** | Rare / precise | `/a`, `/mw add f …`, `/movers set` |
| **E. Web desk (V2)** | Overview on any device | Heat, journal, chat, labels — same backend |

**Kenneth’s default path should be A → C, not D.**

---

## Shipped in this direction (Telegram)

- Inline **Took / Skip / Later** on mover fires when `FEATURE_LEARNING=true`  
- Callback labels written to `learning_labels` (no `/j` required)  
- Optional bounce buttons after **Took**  
- `/desk` — single home for assistant actions  
- Plain-text intents when learning is on (see bot handler)  
- `/help` shortened: sensors vs assistant; power commands secondary  

## Shipped later in V1

- Buttons on **target** fires  
- **MEXC read-only** journal fill sync (flagged)  
- Fatal **news** monitor (flagged)  
- **Voice** → STT → intents (flagged)  

## Next (V2+)

1. Reply keyboard sticky “Desk” row (optional)  
2. Fluent multi-turn coach (LLM + tools) as default  
3. **V2 web/PWA** — overview + chat; Telegram remains panic push  

---

## What we refuse

- Making every feature a new slash command by default  
- Forcing multi-step `/j bounce strong SYMBOL` mid-panic  
- Auto-trade / writing orders without an explicit product decision  
- Replacing Telegram push with a web-only alert path  

---

## Agent rules when adding features

- Prefer **button / desk / plain language** over a new command.  
- If a command is needed, register it under `/desk` “Advanced” help, not the top of `/start`.  
- Learning capture must work **from the fire message** without typing the symbol.  

<!-- agents: search ASSISTANT_UX -->
