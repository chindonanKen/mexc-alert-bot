#!/usr/bin/env bash
# Push staging-related code paths to their limits (no Telegram login required).
#
# Usage (on droplet or laptop):
#   ./scripts/stress_staging.sh
#   STRESS_EVENTS=2000 ./scripts/stress_staging.sh
#
# Optional: bot → your chat message flood (does NOT steal getUpdates):
#   STAGING_BOT_TOKEN=... STAGING_CHAT_ID=... STRESS_NOTIFY=1 ./scripts/stress_staging.sh
#
# Live Telegram command spam must be done by YOU (or a user client) — bots
# cannot pretend to be Kenneth typing /start.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -x .venv/bin/python ]]; then
  PY=.venv/bin/python
else
  PY=python3
fi

export STRESS_EVENTS="${STRESS_EVENTS:-500}"
export STRESS_THREADS="${STRESS_THREADS:-8}"

echo "=== Unit gate first ==="
bash scripts/verify_build.sh

echo ""
echo "=== Staging stress suite (STRESS_EVENTS=$STRESS_EVENTS) ==="
$PY tests/test_staging_stress.py

if [[ "${STRESS_NOTIFY:-}" == "1" ]] || [[ "${1:-}" == "--notify" ]]; then
  echo ""
  echo "=== Optional Telegram notify flood ==="
  STRESS_NOTIFY=1 $PY tests/test_staging_stress.py --notify
fi

echo ""
echo "=== Manual Telegram checklist (you) ==="
echo "On @MEXC_Alerts_Stagingbot, try rapidly:"
echo "  /desk /s /brief /events /news"
echo "  took / skip / later / coach panic"
echo "  spam 10x /p BTC  and  /l"
echo "  if movers on: wait for fire → mash Took/Skip"
echo "  watch: docker logs -f mexc-alert-bot-staging"
echo ""
echo "=== stress_staging PASSED ==="
