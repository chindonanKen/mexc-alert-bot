# Production reliability — sensors first

Telegram targets + movers are a **vital work tool**. A “healthy” container with a dead poller is an outage.

## What you asked: staging first?

**Yes, for anything that starts the bot or touches SQLite init.** Desk-only copy/CSS can still go live. Sensor path cannot.

| Change | Where it lands first |
|--------|----------------------|
| `mexc_bot/main.py`, `monitor.py`, `movers/storage.py`, `_init_db`, Docker/healthcheck, deploy scripts | **Staging**, then prod after smoke PASS |
| Desk UI / Learning copy | Prod ok after `desk-qa` |
| Docs only | No deploy |

Staging uses a **second BotFather token** + `./data-staging`. Prod `./data` is never opened.

```bash
./scripts/droplet.sh deploy-staging   # pull + build staging + smoke
./scripts/droplet.sh smoke-staging
# you: /s on the staging bot
./scripts/droplet.sh deploy-prod      # confirm yes — then prod smoke
```

If `.env.staging` is missing on the droplet, create it from `.env.staging.example` and set the staging token. Do not paste the prod token.

## Post-deploy smoke (mandatory)

Must pass before anyone says “shipped”:

1. Container **running**, not `Restarting`, still up after ~30s
2. Logs contain `Starting Telegram bot polling`
3. `data/bot_heartbeat.json` is fresh and `polling=true`
4. `alerts` count did not shrink vs pre-deploy snapshot
5. `mover_watchlist` count ≥ last snapshot

```bash
make smoke            # prod
make smoke-staging
```

`scripts/deploy.sh` and `droplet.sh deploy-prod` run this automatically.

## Healthcheck

Docker no longer checks “does `alerts.db` exist?”. It checks **heartbeat + polling**. That file lives in `./data` (appuser-writable), not root-owned `.safety`.

## Incident classes (do not repeat)

| Failure | Rule |
|---------|------|
| Schema rebuild on every `MoverStore()` | Never rebuild on request path |
| Fire-only restore | Snapshot the **coin list**; restore from snapshot |
| Root-owned `.safety` + lock file | Init must not crash if a lock can’t be written |
| Health = db file exists | Health = polling heartbeat |

## Agents

Do **not** `docker compose up` prod after a bot-start change until staging smoke passed. If staging cannot run (no token), say so and wait — do not skip to prod.
