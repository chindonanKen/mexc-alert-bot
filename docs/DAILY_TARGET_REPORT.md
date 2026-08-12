# Daily target report (6 AM)

Summarizes overnight **price targets** for AD Desk / Telegram owner:

1. **Hits** — targets that fired (one-shot alerts are deleted; hits are logged)
2. **Near misses** — still-open targets that traded within **5%** of level (configurable), with **timestamp + price** of closest approach

## Data sources

| Need | Source |
|------|--------|
| Open targets | SQLite `alerts` |
| Overnight hits | `target_fire_log` (always written on fire) + `learning_events` source=`target` |
| Near-miss path | MEXC public **klines** (5m) for each open alert over the window |

Does **not** use mover watchlist (different product). Never deletes `alerts` rows.

## Schedule

- **Default window:** previous local 06:00 → this local 06:00 (`TIMEZONE`)
- **In-bot:** `FEATURE_DAILY_TARGET_REPORT=true` (default) starts a daemon thread at 6 AM
- **Host cron (backup):** `bash scripts/install_daily_report_cron.sh`

## Output

- File: `data/reports/daily_targets_YYYY-MM-DD.txt`
- Append log: `data/reports/daily_targets.log`
- Optional Telegram to `DESK_USER_ID` (`DAILY_TARGET_REPORT_TELEGRAM=true`)

## Run manually

```bash
# Host with .env
python3 scripts/daily_target_report.py --no-telegram

# Trailing 24h ending now (debug)
python3 scripts/daily_target_report.py --now --no-telegram

# Droplet container
docker exec mexc-alert-bot python -m mexc_bot.reports.daily_targets
```

## Env

| Variable | Default | Meaning |
|----------|---------|---------|
| `FEATURE_DAILY_TARGET_REPORT` | true | In-process 6 AM scheduler |
| `DAILY_TARGET_REPORT_HOUR` | 6 | Local hour |
| `DAILY_TARGET_NEAR_PCT` | 5 | Near-miss band % |
| `DAILY_TARGET_REPORT_TELEGRAM` | true | Push report text to owner |
| `TIMEZONE` | Europe/Stockholm | Window + schedule |
| `DESK_USER_ID` | required | Whose targets |
