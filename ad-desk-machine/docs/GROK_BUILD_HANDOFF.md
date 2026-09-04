# Grok Build handoff — AD Desk Machine (here-build → droplet)

**Status:** READY — Kenneth said yes 2026-09-04. Port to droplet for paper week only.  
**Live exchange orders:** OFF until Kenneth unlocks. `live_orders_allowed = false` must stay hard-coded on the droplet.

## What this is

Proven here-build at `/workspace/ad-desk-machine` (local team iterate). Grok Build ports this tree to the droplet for a **paper week** only: live MEXC klines in, simulated fills, Machine log, `/machine` page. No live orders.

## Pass bar (do not change)

Score paper week on **expectancy, payoff, and tail versus Kenneth**, not win rate. Goal 1 may name highest win rate as ambition; the score bar does not change.

## Stages before this handoff is LIVE

1. Path habit dig → Lock PASS → hang habit fields; lift `watch_only` (SYN/AGI/US).
2. AGI/US empty OUT: on first buy fill, `needs_you` `empty_out_after_buy` → Reed bounce/base → Gauge draft → Lock PASS → Master hang sells. Do not invent sells from n=1.
3. Droplet paper week ready: this tree + README runbook + tests green.

## Port checklist (Grok Build)

1. Copy this repo to the droplet bind path Kenneth names (git only; no docker-build invent). Follow standing Grok Build align: Master analysis/small PRs; Grok Build tests/merge/droplet. One trunk.
2. Python 3.11+, `pip install -r requirements.txt`, venv.
3. Run: `uvicorn machine.api:app --host 0.0.0.0 --port 8787` (or desk reverse-proxy).
4. Env: `MACHINE_TOKEN` (secret), `MACHINE_FEED_INTERVAL` (default 10), never set live orders on.
5. Confirm `pytest -q` passes on the droplet image/tree before cutover.
6. Confirm GET `/api/machine/status` shows `live_orders_allowed: false` and loop running.
7. Hung plays load from `data/plays/*.json` (not `examples/`).
8. Paper week: leave running; staff score closes via Machine log + closes API vs Kenneth’s desk.

## Do not

- Place live MEXC orders.
- Wipe SQLite / invent learning tables.
- Invent sell layers when `sell_layers` is empty and Reed has no repeat (n&lt;2) unless Kenneth recuts.
- Mix process-book edits into play files.
- SSH overlay / docker-build outside Kenneth’s Grok Build rules.

## Prove suite

```bash
cd ad-desk-machine && source .venv/bin/activate && pytest -q
```

Expected: all green (here-build last count: 76 passed).

## Money sample (local)

```bash
python scripts/money_sample_m2_closed.py
# → data/money_sample_m2_closed.json
```

## Language

Machine / hung plan / written plan; Machine log; buy layers / sell layers / Size layers; current price. No “paper plan,” “paper decisions,” or “packs.”

## Open holes (not blockers for paper week)

- Flatten-on-news not wired (before live unlock only).
- Learn-from-desk / Goal 3 / Goal 4 after paper proves Goal 2.
- SYN/US Path habit_ready false after dig (board-wide panic still buys; Path sit on first/second red).

## READY line (Master fills)

- Date PHT: 2026-09-04 19:01 PHT
- pytest count: 76 passed
- watch_only lifted on: SYNUSDT_4h, AGIUSDT_4h, USUSDT_4h
- habit_ready on: AGIUSDT_4h true (chosen_tf_reds_into_met=1, vol_at_bottom_usd=16623); SYN/US false after dig
- sell_layers: SYN hung; AGI 0.00451/0.00476/0.00505/0.00575/0.005843; US 0.0139/0.0148/0.0167/0.0186/0.02092
- Lock PASS refs: Path dig 2026-09-04; Exit sells AGI+US 2026-09-04
- Kenneth go for Grok Build port: YES 2026-09-04
- live_orders_allowed: false (hard-coded)
