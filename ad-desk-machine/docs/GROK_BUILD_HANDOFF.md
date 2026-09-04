# Grok Build handoff — AD Desk Machine (paper week)

Standalone package. **Live exchange orders stay OFF.** Do not wire this into the deleted desk `/api/machine` routes.

## What this is

`ad-desk-machine/` is the here-build Machine: hung SYN / AGI / US 4h plans, live-read Path / Size / Chart / Fail / Exit, **simulated fills only**, live MEXC **public** klines (`api.mexc.com`) for current price / volume / reds.

Desk `/api/machine/*` and `/machine` stay **404**. The old paper book was removed so it cannot collide with this package.

## Language

Use: **Machine · hung plan · buy layers · sell layers · Size layers · current price**.  
Never: paper plan / paper pack(s).

## Droplet bind (do not merge into the desk process)

On the droplet, run **next to** the desk, not inside it:

```bash
cd /root/mexc-alert-bot/ad-desk-machine   # or ~/mexc-alert-bot/ad-desk-machine
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export MACHINE_TOKEN='<same secret you store in droplet .env — not committed>'
# MACHINE_LOOP=1 to poll hung plays against live klines
uvicorn machine.api:app --host 0.0.0.0 --port 8787
```

Optional Caddy / firewall: open **8787** only to you. Bearer `MACHINE_TOKEN` on every `/plays`, `/log`, `/evaluate` call. `/health` is open and always reports `live_orders_allowed: false`.

Do **not**:

- Set `DESK_ALLOW_LIVE_ORDERS`
- Mount this app over desk `:8080`
- Restore `mexc_bot/machine/`
- Place live MEXC orders
- Wipe `alerts.db` / mover / learning tables

## Hung plays (paper week)

| File | T → B | Path | Sells |
|------|--------|------|--------|
| `data/plays/SYNUSDT_4h.json` | 0.14753 → 0.0413 | habit_ready false; dump-depth high_magnet; panic Q_i = % of B | already hung |
| `data/plays/AGIUSDT_4h.json` | 0.00748 → 0.004172 | habit_ready true; chosen reds into met = 1; vol_at_bottom 16623 | 0.00451 / 0.00476 / 0.00505 / 0.00575 / 0.005843 @ 10/15/30/30/15 |
| `data/plays/USUSDT_4h.json` | 0.02475 → 0.0115 | habit_ready false | 0.0139 / 0.0148 / 0.0167 / 0.0186 / 0.02092 @ 10/15/30/30/15 |

## Tests

```bash
cd ad-desk-machine
PYTHONPATH=. python3 -m pytest -q
```

## Desk

Leave Telegram + AD Desk as-is. This package does not write `alerts`, movers, journal, or learning.
