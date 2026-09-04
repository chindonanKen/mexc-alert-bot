# AD Desk Machine

Hung-plan decision loop for the AD desk. Simulated fills only.
**`live_orders_allowed = false`** is hard-coded — there is no path that places live exchange orders.

Language: Machine / hung plan / written plan; Machine log; buy layers / sell layers / Size layers; **current price** (never “last”).

## Ready for use

One command from the project root (venv already created):

```bash
cd /workspace/ad-desk-machine && source .venv/bin/activate && uvicorn machine.api:app --host 0.0.0.0 --port 8787
```

On the droplet, open **`https://<droplet>/machine`** (same HTTPS as the AD Desk). Type the **desk token** in the password field — it is sent once, kept as an HttpOnly cookie, never stored in the page. Mac mini and MacBook use that same URL.

Local loopback: `http://127.0.0.1:8787/machine`.  
Scripts may still send `Authorization: Bearer $MACHINE_TOKEN` (token is never printed in responses).

On boot the API:

1. Loads **all** `data/plays/*.json` (SYNUSDT / AGIUSDT / USUSDT hang; `examples/` is not auto-loaded).
2. Starts the **always-on decision loop**: live MEXC print feed → `engine.on_print` → Machine log / fills (page already polls; you do not need POST `/simulate` for the loop to react).

| What | Live vs simulated |
|------|-------------------|
| MEXC klines (price, volume, lows, TF red counts) | **Live** read from `api.mexc.com` every ~10s (`MACHINE_FEED_INTERVAL`) |
| Path / Exit / Size decisions | Engine on those prints |
| Fills / Machine log / closes | **Simulated** paper fills only |
| Exchange orders | **Off** — `live_orders_allowed` stays false |

Env knobs:

- `MACHINE_TOKEN` — bearer token for scripts (default `dev-token`)
- `DESK_API_TOKEN` — accepted by the `/machine` password form (same token as the AD Desk)
- `MACHINE_FEED_INTERVAL` — poll seconds (default `10`)
- `MACHINE_LOOP=0` — disable the background feed loop (tests do this)

Money sample with buys + sells (synthetic dump/bounce through the SYN hung plan):

```bash
python scripts/money_sample_m2_closed.py
# → data/money_sample_m2_closed.json
```

## Setup

```bash
cd /workspace/ad-desk-machine
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Routes

| Method | Path | Notes |
|--------|------|--------|
| GET | `/machine` | Static Machine page (password form; desk token) |
| POST | `/api/machine/login` | HttpOnly session cookie; never echoes the token |
| POST | `/api/machine/logout` | Clear session cookie |
| GET | `/api/machine/plans` | Ranked thin list + sheet fields |
| GET | `/api/machine/plans/{id}` | One plan |
| GET | `/api/machine/layers/{id}` | IN / OUT layers |
| GET | `/api/machine/trades` | Simulated fills |
| GET | `/api/machine/feed` | Recent prints (live + simulate) |
| GET | `/api/machine/log` | Decision Machine log |
| GET | `/api/machine/closes` | Closes |
| GET | `/api/machine/status` | `live_orders_allowed: false` + loop status |
| POST | `/api/machine/hang` | Hang a written plan |
| POST | `/api/machine/simulate` | Push one synthetic print (staff scoring) |

## Load a play file

Hung plans in `data/plays/`:

- `SYNUSDT_4h.json` / `AGIUSDT_4h.json` / `USUSDT_4h.json` — live hang-ready (Size buys; SYN has sell layers)
- `examples/demo_habit.json` — habit_ready true + sell layers
- `examples/demo_sit.json` — habit_ready false (sit first/second chosen TF red)
- `examples/demo_empty_out.json` — habit ready, **empty OUT** scaffold only — not live PRL (do not invent sells; official PRL AD is 0.47→0.2008)

`data/plays/*.json` load when the API starts. Examples stay under `examples/` until you hang them. Staff simulator:

```bash
python scripts/simulate.py --play data/plays/examples/demo_habit.json --dump
```

Play JSON should include Size layers (or omit `layers` to auto-build AD-side + panic) and red-habit fields:

`chosen_tf`, `faster_tfs`, `chosen_tf_reds_into_met`, `faster_tf_reds_at_low`, `vol_at_bottom_usd`, `habit_ready`.

## Tests

```bash
cd /workspace/ad-desk-machine
source .venv/bin/activate
pytest -q
```

Prove coverage: Path (habit sit / first-red habit buy / no fixed count), Size (at-or-through fills, unreached empty, Size-share USD, one buy set, empty OUT), Chart (met stays met), Exit live-read (into big base, panic-like volume, defensive under-AD, candles-to-bounce, leftover remaining-cost full-exit, auto GOOD/WEAK/FAIL/TOO_EARLY bounce kind, no invent, static fill), live MEXC feed conversion + decision loop + money-sample sells.

## Simulator (no MEXC)

```bash
python scripts/simulate.py --dump
python scripts/simulate.py --play data/plays/examples/demo_habit.json --dump --json
```

Or POST `/api/machine/simulate` with a JSON body:

```json
{
  "name": "DEMO",
  "price": 0.805,
  "volume_usd": 50000,
  "chosen_tf_reds": 1,
  "faster_tf_reds": {"5m": 2},
  "low": 0.805
}
```

## Layout

```
ad-desk-machine/
  machine/          engine, fills, path, size, chart, exit, log, feeds, loop, api
  static/machine/   /machine page
  data/plays/       hung plans (SYN/AGI/US) + examples/
  data/money_sample_m2_closed.json
  tests/            pytest prove suite
  scripts/simulate.py
  scripts/money_sample_m2_closed.py
```

## Rules (summary)

- **Path:** `habit_ready` false → sit on first/second red of chosen TF (board-wide panic still buys). When AD met + at AD + habit ready → BUY on chosen-TF habit **or** faster-TF reds+volume — even on first chosen red. No fixed 15m≥3 for every name.
- **Size / fills:** fill only when print is at or through layer price; filled USD = Size share; unreached stay empty; one buy set per hung plan; empty OUT when no sells written.
- **Chart:** met when low enters last 5% of AD length above B through B; met stays met.
- **Exit live-read:** while live with remaining sell layers, re-read play-file bounce/base/volume facts + live tape. Bounce kind is scored GOOD / WEAK / FAIL / TOO_EARLY from the tape + Reed facts (`score_bounce_kind`); `Print.weak_bounce` is an optional test override only. Usual bounce height is a map (fat sells on 3rd/4th toward it), not a freeze. Into a big base → sell the invested bag (do not wait for bounce-length past the base). Panic-like volume on the way up (~3× low-bar from facts) → sell a matching amount without waiting for usual height. WEAK → pull remaining sell layers down. TOO_EARLY → do not sell the first weak tick. FAIL → consider exit when Reed’s `candles_to_bounce` have passed at the AD with no bounce like this chart prints (not a clock). GOOD → usual path; strong first bounce may sell a matching amount; leftover may full-exit above remaining cost. Drop past AD without board-wide panic → defensive lower sell layers vs original, scaled to the new bottom (not parked at the bottom). Board-wide panic → do not sell the first weak bounce; if under/into a big base, still sell up into that base. Leftover remaining-cost: leftover avg = (bought USD − sold USD) / remaining qty; sell above remaining cost → leftover avg goes down (simulated fills only). No repeating bounce / empty sell layers → do not invent sells. Static fill when print ≥ hung sell price still works.
- **Machine log:** only decision changes (enter/exit/miss/kill/first met/grind/panic/exit-live) — no wait spam.
- **Live feed:** MEXC klines only; no invented ticks. Decision loop keeps reacting while uvicorn runs.
