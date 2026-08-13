# Production reliability (sensors first)

Telegram targets + movers are a vital work tool.

## Staging first

Anything that runs on **bot start** or **SQLite init** goes to **staging**, then prod only after smoke PASS:

- `mexc_bot/main.py`, `monitor.py`, `movers/storage.py`, `_init_db` / migrations
- Docker / compose / healthcheck / deploy scripts

Desk-only UI and docs-only are exempt.

```bash
./scripts/droplet.sh deploy-staging
./scripts/droplet.sh smoke-staging
# then, after owner can /s the staging bot:
./scripts/droplet.sh deploy-prod
```

If `.env.staging` is missing, stop and say so. Do not skip to prod.

## Smoke is mandatory after prod bot/desk rebuild

`bash scripts/post_deploy_smoke.sh` (or `make smoke`). Fail = not shipped.

Must see: container still Up (not Restarting), `Starting Telegram bot polling`, fresh `data/bot_heartbeat.json` with `polling=true`, alerts/watchlist counts did not shrink.

## Do not

- Claim healthy because `alerts.db` exists
- Crash the bot if `.safety` / a lock file is unwritable
- Rebuild `mover_watchlist` inside `MoverStore()`
- `docker compose down -v` on prod

Full page: [docs/PROD_RELIABILITY.md](../../docs/PROD_RELIABILITY.md).
