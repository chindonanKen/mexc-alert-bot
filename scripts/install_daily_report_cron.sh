#!/usr/bin/env bash
# Install host cron for daily 6 AM target report inside the bot container.
# Run on the droplet: bash scripts/install_daily_report_cron.sh
#
# Uses TIMEZONE from .env when possible; cron itself is host-local time.
# Prefer Europe/Stockholm 6:00 → set host TZ or adjust CRON_HOUR.

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Host cron is wall-clock on the droplet. Prefer in-bot scheduler (Asia/Manila).
# If host TZ is UTC, 6 Manila = 22:00 previous day UTC → set CRON_HOUR accordingly.
HOUR="${CRON_HOUR:-6}"
MARKER="# mexc-alert-bot daily-target-report"
CRON_LINE="0 ${HOUR} * * * cd ${ROOT} && docker exec -e DAILY_TARGET_REPORT_TZ=Asia/Manila -e TIMEZONE=Asia/Manila mexc-alert-bot python -m mexc_bot.reports.daily_targets >> ${ROOT}/data/reports/cron.log 2>&1 ${MARKER}"

mkdir -p "${ROOT}/data/reports"

# Remove old marker lines, append new
tmp="$(mktemp)"
crontab -l 2>/dev/null | grep -v "mexc-alert-bot daily-target-report" >"$tmp" || true
echo "$CRON_LINE" >>"$tmp"
crontab "$tmp"
rm -f "$tmp"

echo "Installed cron:"
crontab -l | grep "daily-target-report" || true
echo ""
echo "Manual test:"
echo "  docker exec mexc-alert-bot python -m mexc_bot.reports.daily_targets"
echo "  # or: python3 scripts/daily_target_report.py --no-telegram"
