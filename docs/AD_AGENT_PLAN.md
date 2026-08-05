# AD Agent Plan — path to full autonomy

**Canonical.** Follow this from now on. Full focus = build this agent. AD Desk gets only **small edits** unless they unblock a phase.

**End goal:** Fully autonomous AD agent on MEXC panic/AD scale-ins — observe → decide like Kenneth’s process → propose layers → paper-proven → **gated** live. Autonomy is earned by **score + paper**, not chat polish.

**Ladder (every phase must advance this):**

```text
Observe → Freeze case → Decide + log → Grade → AD policy → Paper → Advise → Gated live
```

| Product | Role |
|---------|------|
| **Telegram** | Sensors/alarms only — leave as-is |
| **AD Desk** | Positions + teach + agent surfaces; occasional UX fixes only |

---

## Phases

### P0 — Truth & teach ✅ **SHIPPED**
**Need:** Clean fuel.  
Fires, positions, `teach_ok` money, teach on trades, chips (`ad_met`/`ad_missed`…), delete junk, mover sets, Binance delist intel.  
**Exit:** Reliable data + human labels possible.  
**Owner while later phases build:** Light teach on real trades (quality > volume). Do not block engineering on a “teach soak.”

### P1 — Case factory ← **IN PROGRESS (shipped core)**
**Need:** Situations the agent can match, not diary text.  
On **fire** (async freeze) + **teach** (re-snap + chips/note): table `agent_setup_cases`, module `learning/cases.py`, desk snapshot UI + howto.  
Auto klines via `compute_fire_features`. Interactive charts still optional later.  
Words **annotate**; **features index**.  
**Exit (remaining):** richer nearest-case index for P2; backfill polish.

### P2 — Decide + log
**Need:** Agent **thinks** on a dump.  
Nearest-case retrieve → structured call (lean take / skip / wait-deeper + why + confidence) → immutable `agent_decisions`. Soft remind on desk/voice; **no orders**.  
**Exit:** Every relevant fire has a logged opinion.

### P3 — Grade
**Need:** Real learning (right/wrong).  
Join decision → path + `ad_met`/`ad_missed` + `teach_ok` PnL when known. Score process vs money separately; promote/demote by setup bucket.  
**Exit:** “Would this agent have helped?” is a number.

### P4 — AD policy
**Need:** Full play, not only take/skip.  
Propose layers/zones/sizes from cases + taught rules; light counterfactuals. Still no live risk.  
**Exit:** Consistent AD proposals on new fires.

### P5 — Paper / replay
**Need:** Prove skill without capital.  
Replay dumps + paper book; same decide→grade; agent vs owner took/skip.  
**Exit:** Owner-set pass bar cleared.

### P6 — Advise (owner re-open only)
**Need:** Live help, human still in control.  
Ranked recs citing cases + scores. **Gate:** P5 bar met + owner re-opens coach-like UI.

### P7 — Gated live AD
**Need:** Autonomy with teeth.  
Flags, size caps, kill switch, allowlists; only paper-proven policy; full audit. **Default off. Never silent.**  
**Exit:** Autonomous AD under hard limits.

---

## Phase → end goal

| Phase | Unlocks |
|-------|---------|
| P0 | Trustworthy data/labels |
| P1 | Memory of **setups** |
| P2 | **Thinking** (decision) |
| P3 | **Learning** (feedback) |
| P4 | **AD plan** generation |
| P5 | **Proof** |
| P6 | Live **advice** |
| P7 | Live **action** |

**One line:** Truth → cases → decide & log → grade → AD policy → paper → advise → gated live.

---

## Rules for agents

1. **Do not skip phases** (no P6/P7 before P3–P5).  
2. **No coach theater** / ranked recs until P6 gates.  
3. **No live orders** until P7 + explicit owner enable.  
4. Prefer structured cases over free-text RAG.  
5. Desk UI polish only when it unblocks a phase or is a small owner ask.  
6. Never break spot target `stable_id` crossing; never grow learning into Telegram.  
7. After shipping a phase: update this file status, `SESSION_HANDOFF`, `GET /api/roadmap`, `Agents.md`.

**Status snapshot:** P0 done · P1 case core shipped · **desk money UX: $ bought/sold + free coins + PnL page** · P2 decide+log next for agent brain.

### Free coins (spot discipline)

When **$ sold ≥ $ bought** on an open spot cycle, residual bag = **free inventory** (scale out in layers).  
Auto badge `FREE` + manual **Mark free coins / Not free**. Teach chips: `free_coins`, `free_tp_ok`, `free_tp_greed`.
