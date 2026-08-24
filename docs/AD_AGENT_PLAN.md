# AD Agent Plan — path to full autonomy

**Canonical.** Follow this from now on. Full focus = build this agent. AD Desk gets only **small edits** unless they unblock a phase.

**End goal:** Fully autonomous AD agent on MEXC panic/AD scale-ins — observe → decide like Kenneth’s process → propose layers → paper-proven → **gated** live. Autonomy is earned by **score + paper**, not chat polish.

**Foundation — chart history is the sole source of truth.**  
Entry, size, skip, TF, AD length, and typical reds are **dictated by what this symbol already printed on the working timeframe in this range.** Written rules (including 3–5 reds, panic-over-grind) are *how to read* that history, not a script that beats the candles. Owner chips/note record **how he traded**; they do **not** overwrite measured history. If the agent and the user disagree with the chart, **the chart wins.**

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

### P1 — Case factory ← **IN PROGRESS → production-ready this wave**
**Need:** Situations the agent can match, not diary text.  
On **fire** (async freeze) + **teach** (re-snap + chips/note): table `agent_setup_cases`, module `learning/cases.py`, desk snapshot UI + howto.  
Words **annotate**; **features index**. Interactive charts still optional.  

**Must index (owner 2026-08):** **history-first** per-TF AD observations (open ladder **1m–1w**) · this TF’s own typical drop / red streak when measurable · `regime_guess` · vol vs *this* book · live `red_streak` · **factor_alignment vs this chart’s past** · incident `ts`/`px` · four buckets (human judgment of the trade) · `sym`/`base`.  
**Do not** blend TFs. **Do not** treat canned 3–5 reds or “AD + panic vol” as law unless **this** history says so. Chips never overwrite OHLC-derived fields.  

**Exit:** freeze JSON is matchable **chart memory**; retrieve finds similar *histories*; desk shows measured stack + click corrections for *what you see on the chart*, not opinions.  
**Already shipped:** freeze on fire/teach · snapshot UI · normalize-index · bucket chips · first 19-lesson map.

### P2 — Decide + log
**Need:** Agent **thinks** on a dump.  
Nearest-case retrieve → walk Rule 1 → **1.5 (pick TF)** → **2.5 (factor stack + size)** → 2–8 → structured call → immutable `agent_decisions`. Soft remind on desk/voice; **no orders**.  

**Decision must state:** `tf_structure`, `tf_timing`, `ad_len`, why TF won, **factor board** (AD / vol / pace / reds / regime / breadth / bases), `alignment` count, **size_hint** (lean / standard / press), bucket, confidence.  
Compare the live dump to **this TF’s stored history**. Alignment = how many *this-chart* tells match. Size to that stack. Never “the user said wait for 5 reds.” Structure vs timing disagree → wait or tiny size; **never average** two TF prices.  
**Exit:** Every relevant fire has a logged opinion.

### P3 — Grade
**Need:** Real learning (right/wrong).  
Join decision → path + `ad_met`/`ad_missed` + `teach_ok` PnL when known. Score **process vs money separately**.  
Process miss = ignored **this chart’s** history (wrong TF, full size on a dump that never paid here). A small probe when history was mixed can still be process-ok. Chips don’t rewrite what printed. Promote/demote by setup bucket.  
**Exit:** “Would this agent have helped?” is a number.

### P4 — AD policy
**Need:** Full play, not only take/skip.  
Layers/zones attach to the **chosen TF’s AD**, not a blend. **Size and layer count scale with alignment** (few factors → 1–2 tiny layers; fat stack → normal 5–10 exp). Near HTF base → wider/smaller early layers. Still no live risk.  
**Exit:** Consistent AD proposals on new fires.

### P5 — Paper / replay
**Need:** Prove skill without capital.  
Replay dumps + paper book; same decide→grade including **TF selection** and **red/vol gate**. Agent vs owner took/skip.  
Pass bar includes skip-precision on grinds / isolated / empty stacks, and **sizing that matches alignment** (not a ban on first-red).  
**Exit:** Owner-set pass bar cleared.

### P6 — Advise (owner re-open only)
**Need:** Live help, human still in control.  
Ranked recs citing **TF + factor board + alignment + size hint + case IDs**. **Gate:** P5 bar met + owner re-opens coach-like UI.

### P7 — Gated live AD
**Need:** Autonomy with teeth.  
Flags, size caps, kill switch, allowlists; only paper-proven **TF + bucket + alignment/size** policy; live size still capped by how many factors lined up. Full audit. **Default off. Never silent.**  
**Exit:** Autonomous AD under hard limits.

### AD geometry & timing (owner 2026-08 — applies P1–P7)

See `docs/TRADING_STRATEGY.md` Rules 1, 1.5, 2.5. Module: `mexc_bot/learning/red_streak.py`.

- New lows: never drag old high-range ADs; wait for high-volume selling spikes.  
- AD is **timeframe-dependent** (open ladder **1m → 1w**). Pick the TF with the best bounce history **in the current range**, then mix volume / panic-grind / bases. Never hardcode “the” TF.  
- **Chart history on the working TF is the only source of truth.** Examples (3–5 reds, panic-over-grind) teach *how to look*. This coin’s printed swings decide entry and size. Owner speech does not override candles.

#### How the agent counts reds (canonical — one factor)

On **each TF independently** (never mix 5m reds with 1h reds):

1. Take closed OHLCV bars oldest → newest. **Drop the forming candle.**  
2. A bar is **red** iff `close < open` (strict). Doji (`close == open`) **breaks** the streak. Green breaks it.  
3. Walk **newest closed bar backward**. Count consecutive reds until a non-red.  
4. That integer is `red_streak`. Labels: `1st` `2nd` `3rd` `4th` `5th` `6plus`.  
5. Compare live `red_streak` to **this TF’s historical typical streak** (if enough past ADs). A global 3–5 is only a fallback when this chart has no sample.  
6. P1 **stores** measured history + live dump + alignment. P2 **sizes** to how well the dump matches *this* history. Owner clicks to confirm **what they see on the chart**, not to invent a rule.

P1 freeze: `ad_by_tf[]` per TF + `factor_alignment` `{factors, yes_count, size_hint}`.

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
4. Prefer structured cases over free-text RAG. **Chart history on the working TF beats user speech.**  
5. Desk UI polish only when it unblocks a phase or is a small owner ask.  
6. Never break spot target `stable_id` crossing; never grow learning into Telegram.  
7. After shipping a phase: update this file status, `SESSION_HANDOFF`, `GET /api/roadmap`, `Agents.md`.

**Status snapshot:** P0 done · P1 case core shipped · **P2 week-1 student decide** (tape walk + paper fill on tag; no live orders) · full decide+log still later.

### Free coins (spot discipline)

When **$ sold ≥ $ bought** on an open spot cycle, residual bag = **free inventory** (scale out in layers).  
Auto badge `FREE` + manual **Mark free coins / Not free**. Teach chips: `free_coins`, `free_tp_ok`, `free_tp_greed`.
