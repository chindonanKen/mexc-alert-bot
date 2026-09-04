# AD Desk Machine (paper week)

Standalone hung-plan Machine. **Live MEXC orders stay OFF.** Simulated fills only. Live **public** klines from `api.mexc.com` for current price, volume, and reds.

This folder runs **next to** AD Desk. Desk `/api/machine` stays 404. Do not restore the deleted desk paper book.

## Language

Machine · hung plan · buy layers · sell layers · Size layers · current price.

Never: paper plan / paper pack(s).

## Run (paper only)

```bash
cd ad-desk-machine
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export MACHINE_TOKEN='choose-a-long-secret'
uvicorn machine.api:app --host 0.0.0.0 --port 8787
```

- `GET /health` — open; always `live_orders_allowed: false`
- `GET /plays`, `POST /plays/{id}/evaluate`, `GET /log` — `Authorization: Bearer $MACHINE_TOKEN`
- Optional: `MACHINE_LOOP=1` to poll hung plays against live klines

`live_orders_allowed` is **hard-coded false**. No env flag can place a live order.

## Hung plays (paper week)

| Play | T | B | Notes |
|------|---|---|--------|
| `SYNUSDT_4h` | 0.14753 | 0.0413 | habit_ready false; sell layers hung; panic Q_i = % of B; dump-depth high_magnet |
| `AGIUSDT_4h` | 0.00748 | 0.004172 | habit_ready true; chosen TF reds into met = 1; vol_at_bottom 16623 |
| `USUSDT_4h` | 0.02475 | 0.0115 | habit_ready false |

Files: `data/plays/*.json`. Optional exit facts: `data/.grokbot/`.

## Tests

```bash
cd ad-desk-machine
PYTHONPATH=. python3 -m pytest -q
```

## Droplet

See [docs/GROK_BUILD_HANDOFF.md](docs/GROK_BUILD_HANDOFF.md). Bind port **8787** beside the desk. Do not wipe databases. Do not enable live orders.
