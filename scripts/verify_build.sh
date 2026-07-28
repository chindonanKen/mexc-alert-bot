#!/usr/bin/env bash
# Local verify gate for mexc-alert-bot builds (safe, no prod secrets).
# Usage: from repo root → ./scripts/verify_build.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=== 1. Git state ==="
git status -sb
git log -3 --oneline
echo

echo "=== 2. Unit tests ==="
if [[ -x .venv/bin/python ]]; then
  PY=.venv/bin/python
elif command -v python3 >/dev/null 2>&1; then
  PY=python3
else
  echo "No python3 found"; exit 1
fi
# Prefer 3.11+ if available for | union types in monitor
if command -v python3.11 >/dev/null 2>&1; then PY=python3.11; fi
if command -v python3.12 >/dev/null 2>&1; then PY=python3.12; fi

echo "Using: $($PY --version)"
$PY tests/test_crossing_and_remove_logic.py
$PY tests/test_v3_futures_and_movers.py
$PY tests/test_mover_enrichment.py
$PY tests/test_learning_events.py
echo "All unit tests OK"
echo

echo "=== 3. Safety greps ==="
# Learning/news/coach must never delete alerts
if grep -rn "DELETE FROM alerts" mexc_bot/learning mexc_bot/coach mexc_bot/news 2>/dev/null; then
  echo "FAIL: learning/coach/news deletes alerts"; exit 1
fi
# No hardcoded real-looking tokens in py/env (docs may show placeholders like 123456789:AAF...)
if grep -rnE "TELEGRAM_BOT_TOKEN=[0-9]{8,}:[A-Za-z0-9_-]{20,}" mexc_bot scripts --include='*.py' --include='*.sh' 2>/dev/null; then
  echo "FAIL: possible committed token in code"; exit 1
fi
if [[ -f .env ]] && grep -qE "TELEGRAM_BOT_TOKEN=[0-9]{8,}:" .env 2>/dev/null; then
  echo "NOTE: .env has a token (expected locally; must never be git-committed)"
fi
echo "Safety greps OK"
echo

echo "=== 4. Flags default OFF in .env.example ==="
for flag in FEATURE_LEARNING FEATURE_NEWS_MONITOR FEATURE_VOICE FEATURE_FUTURES_ALERTS FEATURE_MOVER_SCANNER; do
  if ! grep -q "^${flag}=false" .env.example; then
    echo "WARN: $flag not explicitly false in .env.example"
  fi
done
echo "Flag check done"
echo

echo "=== 5. Import smoke (learning modules) ==="
$PY -c "
from mexc_bot.learning import EventStore, OutcomePoller
from mexc_bot.coach import format_brief, format_coach_reply
from mexc_bot.learning.integrity import validate_event_row, coach_must_not_claim_unlogged
print('imports OK')
"
echo

echo "=== verify_build PASSED ==="
echo "Next (manual / staging Telegram):"
echo "  /s  /l  /p f TSLA  /mw  /events  /brief  /coach panic"
echo "  After a mover: /events then /j took then /events again"
echo "See docs/VERIFY_BUILD.md for full agent prompt + checklist."
