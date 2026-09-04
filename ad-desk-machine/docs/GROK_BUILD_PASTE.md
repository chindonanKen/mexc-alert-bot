# Grok Build — port AD Desk Machine (paper week)

Kenneth said yes 2026-09-04 19:01 PHT. Port the proven here-build to the droplet. Live exchange orders stay OFF.

## Why droplet now
Kenneth’s Mac in the Philippines cannot reach api.mexc.com (DNS/filter returns a private blocker IP). The Machine must run on the droplet so live MEXC klines work. Confirm from the droplet: `curl -sS https://api.mexc.com/api/v3/ticker/price?symbol=AGIUSDT` returns a real price JSON.

## Source
Here-build tree: ad-desk-machine (Machine engine, fills, path, size, chart, exit, feeds, loop, api, static /machine page, data/plays hung plans, tests).
Full brief: docs/GROK_BUILD_HANDOFF.md in that tree.

## Do this
1. Put this Machine tree on the git trunk Kenneth uses for the live desk bind (/root/mexc-alert-bot). One trunk. No docker-build invent. No SSH overlay of s1 except emergencies.
2. Install: Python 3.11+, venv, pip install -r requirements.txt.
3. Run: uvicorn machine.api:app --host 0.0.0.0 --port 8787 (or desk reverse-proxy to /machine).
4. Env: MACHINE_TOKEN (secret), MACHINE_FEED_INTERVAL default 10. Do not turn live orders on. live_orders_allowed must stay false.
5. Run pytest -q on the droplet tree. Expect all green (here-build: 76 passed).
6. Confirm GET /api/machine/status shows live_orders_allowed false and the decision loop running.
7. Hung plays load from data/plays/*.json (SYNUSDT_4h, AGIUSDT_4h, USUSDT_4h). Do not auto-load examples/.
8. Leave it running for paper week. Staff score closes on expectancy, payoff, and tail versus Kenneth — not win rate.

## Hung plans (do not invent new layers)
- SYNUSDT 4h: watch_only false; habit_ready false; sells hung; buys dump-depth + panic percent-of-B.
- AGIUSDT 4h: watch_only false; habit_ready true; chosen_tf_reds_into_met=1; vol_at_bottom_usd=16623; sells 0.00451 / 0.00476 / 0.00505 / 0.00575 / 0.005843 at 10/15/30/30/15.
- USUSDT 4h: watch_only false; habit_ready false; sells 0.0139 / 0.0148 / 0.0167 / 0.0186 / 0.02092 at 10/15/30/30/15.

## Do not
- Place live MEXC orders.
- Wipe SQLite or invent learning tables.
- Mix process-book edits into play files.
- Invent sell layers.
- Merge cutover that turns live orders on.

## Language
Machine / hung plan / written plan; Machine log; buy layers / sell layers / Size layers; current price. No "paper plan," "paper decisions," or "packs."

## Done when
Paper Machine is running on the droplet with live MEXC klines in, simulated fills, Machine log, /machine page, hung SYN/AGI/US plans loaded, tests green, live_orders_allowed false.
