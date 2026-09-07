# Grok Build — Kill hung plan (POST /api/machine/kill)

Kenneth 2026-09-07. Live orders stay false. Do not invent prices. Do not hang the live droplet from this task.

## Fault

Kenneth marked ANSEMUSDT done. Master set watch_only=true and cleared buys/sells, but the Machine page still showed ANSEM as state=watch with Path why. The UI only hides a row when `state === "out"` or `killed`. Engine already had `Engine.kill(plan_id, why)` (sets `killed=True`, `state="out"`, logs, appends closes). There was no HTTP kill route. The KILL span was paint-only.

## Fix

- `POST /api/machine/kill` in `machine/api.py` — body `id` or `plan_id` (accept both), optional `why`. Resolve plan, call `engine.kill`, return `plan_row` with `live_orders_allowed: false`. 401 without bearer; 404 if missing.
- `plan_row` includes `killed` (bool) so the sheet/ranked paint contract can hide finished plays.
- `static/machine/app.js` — `.kill` click confirms, POSTs kill with the plan id, refreshes; ranked skips out/killed; open sheet closes when the selected plan is pulled.

## Prove

```
cd ad-desk-machine
.venv/bin/pytest -q tests/test_kill.py
```

Expect green. Live orders stay false.

## Acceptance

```
curl -s -X POST "$HOST/api/machine/kill" \
  -H "Authorization: Bearer $MACHINE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"id":"ANSEMUSDT_1h","why":"Kenneth marked done"}'
```

Expect `state: "out"`, `killed: true`, `live_orders_allowed: false`. Row gone from sheet; ranked paint skips it.

## Do not

- Place live MEXC orders.
- Invent ticks or prices.
- SSH the droplet from this kill task.
