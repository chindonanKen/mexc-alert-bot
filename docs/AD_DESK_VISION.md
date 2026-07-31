# AD Desk Vision — Voice-first AD command platform

**Status:** Living product vision (2026-07-31)  
**Owner:** Kenneth  
**Implements against:** [TRADING_STRATEGY.md](TRADING_STRATEGY.md) · [AGENTS.md](../AGENTS.md) · this file  
**UI inspiration:** futuristic HUD — dark void, cyan/green signal arcs, glass panels, sparse noise, voice as center of gravity  

---

## 1. Product thesis

The platform is **not** a generic trading terminal. It is:

1. **Sensors** (targets + movers) that wait while Kenneth is away  
2. **Memory** (fires, labels, outcomes, journal, fills) that learns what works  
3. **Intel** filtered to *his* book (not the whole market feed)  
4. **Coach / voice agent** as the **primary interface** — talk to change the desk, query strategy, critique risk, propose layers  
5. **Optional** paper agents and later gated live order *monitoring* — never silent auto-risk  

**Core loop:** plan AD levels → alarms wait → fire → voice + labels teach → coach checks discipline → (later) paper agents practice → recommendations only when evidence is strong.

---

## 2. Take on the seven pillars (honest)

### 2.1 Voice as main interaction + full desk control

| Good | Improve |
|------|---------|
| Call dock always present; tools edit alerts/watch/journal | Expand tools for *every* surface (PnL, layers, paper, coach critique) |
| Continuous multi-turn + TTS | Keep calm VAD; never spam empty STT |
| Strategy encoded in system prompt | Voice must **cite** labels/outcomes when coaching (“you skip grinds 80%”) |

**Rule:** Voice can read/write anything the UI can, **except** placing live exchange orders without an explicit double-gate (flag + spoken confirm + optional PIN later).

### 2.2 Hierarchy on Overview (signal > noise)

**Chronological priority (top → bottom of Overview):**

1. **Targets top 3** + **Movers top 3** (only the hottest / nearest — rest lives on Tape/Targets)  
2. **Intel** only for symbols in targets ∪ watchlist (and open positions)  
3. **Positions** — prefer MEXC-derived avg entry + mark + time-in-trade (journal until private API on)  
4. **Learning** — teach / label / comment (human ↔ system)  
5. **Secondary:** regime, majors, full lists  

**Improve:** stop showing every fire and every investigation by default; rank by relevance to *his* book.

### 2.3 How agents learn → recommend → (maybe) trade

**Stage A — Memory (now / near)**  
Event log + took/skip + bounce/DD outcomes + isolated verdicts + (optional) fills.

**Stage B — Expertise weights**  
Sources and setup tags get weights from confirmed bounces vs false alarms (already started for delist radar).

**Stage C — Coach only**  
“This looks GRIND + isolated — strategy says skip.” No auto size.

**Stage D — Recommendations**  
Ranked candidates with AD thesis, invalidation, layer sketch — always paper until Kenneth accepts.

**Stage E — Paper arena**  
Agents trade **fictive capital** on **real** public marks from watchlist/targets. Same playbook rules. Compare agent PnL vs Kenneth’s labels.

**Stage F — Live assist (gated)**  
Read open orders / positions from MEXC. Learn which of *his* order placements worked. Still **no** autonomous size without DESK_ALLOW_LIVE_ORDERS + confirm.

**Never jump E→F without soak metrics.**

### 2.4 Design overhaul

- **Feel:** deep space, glass HUD, cyan/mint signal, monospaced metrics, minimal chrome  
- **Voice dock** = permanent “cockpit bar”  
- **Overview** = ranked stack, not a dump of widgets  
- Avoid cluttered bento; prefer **one hero band + ranked rails**

### 2.5 AD layer planner (shared human + agent)

Proposal (build toward this):

| Field | Meaning |
|-------|---------|
| Symbol / market | Contract id |
| Regime note | High range / initial drop defined? |
| AD length (visual) | Kenneth or agent estimate |
| Layer ladder | 5–10 prices, exponential size fractions |
| Invalidation | Below last layer or structure break |
| Powder reserve | % capital held for extension |
| Mode | Scout / standard / defensive |

**API + voice:** `propose_layers` / `save_plan` / `list_plans`. Journal open_position can attach `plan_id`.  
**Agent advantage:** plans become training labels (did price tag L3? did he skip?).

### 2.6 PnL

| Source | Use |
|--------|-----|
| Journal open/close | Paper + desk PnL now |
| `journal_fills` (private read) | Realized + avg entry when FEATURE_MEXC_PRIVATE_READ |
| Paper agent books | Separate ledger |

Show: open mark-to-market, realized session/day, vs labels (took with +bounce = good).

### 2.7 Live orders (monitor first)

1. **Read-only** open orders + positions (private API)  
2. Voice: “what orders do I have on SIREN?”  
3. Outcomes: fill/cancel/timeout → learning  
4. **Placement** only under DESK_ALLOW_LIVE_ORDERS + voice confirm + never from paper agent auto path  

### 2.8 Security (non-negotiable)

| Rule | Detail |
|------|--------|
| Secrets | Never commit `.env`, tokens, keys, certs, `data/*` |
| Flags | Live orders default **OFF** |
| Voice | Auth token required; destructive tools confirm in speech |
| Private API | Read-only client until explicit write product decision |
| Staging | Separate token + `data-staging` |
| Agent | No silent live risk; paper agents cannot flip live flag |

---

## 3. Overview information architecture (ship target)

```
┌─────────────────────────────────────────────────────────┐
│  HERO: regime + voice status + pulse (sparse)           │
├──────────────────────┬──────────────────────────────────┤
│  TARGETS · top 3     │  MOVERS · top 3                  │
│  nearest / armed     │  deepest dump / PANIC first      │
├──────────────────────┴──────────────────────────────────┤
│  INTEL · filtered to book only                          │
├─────────────────────────────────────────────────────────┤
│  POSITIONS · mark · avg entry · time open · uPnL        │
├─────────────────────────────────────────────────────────┤
│  LEARNING · teach / label / comment                     │
├─────────────────────────────────────────────────────────┤
│  (expand) full tape · planner · paper arena · roadmap   │
└─────────────────────────────────────────────────────────┘
```

---

## 4. Voice agent contract

Voice is the **primary** control plane:

- Query: market, strategy playbook, memory, intel, PnL, plans  
- Mutate: alerts, watchlist, movers params, journal, labels, plans, paper sim  
- Coach: discipline check against TRADING_STRATEGY.md  
- **Refuse:** live order placement unless flag + explicit confirm  

---

## 5. Build phases (execution order)

| Phase | Deliverable | Status |
|-------|-------------|--------|
| **P0** | Vision in AGENTS + this doc; security restated | now |
| **P1** | Overview hierarchy + futuristic shell CSS | now |
| **P2** | Voice tools for hierarchy + coach critique | near |
| **P3** | Layer planner CRUD + voice | next |
| **P4** | Journal PnL + optional private fills on desk | next |
| **P5** | Paper agent arena (fictive book, real marks) | later |
| **P6** | Read-only live orders monitor | later |
| **P7** | Gated live placement | only after P4–P6 soak |

---

## 6. What needs improvement (summary)

1. **Noise** on Overview — fix with top-3 rails + filtered intel  
2. **Learning is passive** — make Learning a first-class teach panel + voice critique  
3. **Positions are journal-only** — bridge private fills when keys exist  
4. **No shared layer object** — add planner for human+agent  
5. **No paper sim** — agents can’t practice risk-free yet  
6. **Live orders** — monitor before place; never confuse paper with live  
7. **Design** — collapse clutter; HUD aesthetic; voice dock stays permanent  
8. **Latency** — REST STT+chat+TTS is inherently multi-second; realtime WS later  

---

## 7. Agent learning → recommendation checklist

Before any “auto recommend” ships:

- [ ] ≥ N labeled fires with outcomes (owner-defined soak)  
- [ ] Skip rate on GRIND / isolated tracked  
- [ ] Paper agent beat baseline random or “always take PANIC”  
- [ ] Coach false-positive rate reviewed by Kenneth  
- [ ] Live flag still OFF for placement  

---

*This document is the product north star for AD Desk UI + voice. Engineering safety remains in AGENTS.md.*
