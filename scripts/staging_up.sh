#!/usr/bin/env bash
# Start isolated STAGING bot (learning ON). Does NOT stop production.
#
# Isolation:
#   - .env.staging  (own Telegram token)
#   - ./data-staging (own SQLite)  — never ./data
#
# Usage (from repo root):
#   ./scripts/staging_up.sh
#   ./scripts/staging_up.sh --docker    # force Docker if available
#   ./scripts/staging_up.sh --local     # force local Python process
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

MODE="auto"
for arg in "$@"; do
  case "$arg" in
    --docker) MODE="docker" ;;
    --local)  MODE="local" ;;
    -h|--help)
      echo "Usage: $0 [--docker|--local]"
      exit 0
      ;;
  esac
done

if [[ ! -f .env.staging ]]; then
  cp .env.staging.example .env.staging
  echo "Created .env.staging from example."
  echo ""
  echo ">>> EDIT .env.staging and set TELEGRAM_BOT_TOKEN to a SECOND BotFather bot."
  echo ">>> Do NOT paste your production token."
  echo ">>> Then re-run: ./scripts/staging_up.sh"
  exit 1
fi

if grep -qE 'TELEGRAM_BOT_TOKEN=your_staging_bot_token_here|^TELEGRAM_BOT_TOKEN=\s*$' .env.staging; then
  echo "ERROR: .env.staging still has a placeholder token."
  echo "Open .env.staging and set TELEGRAM_BOT_TOKEN from a staging BotFather bot."
  exit 1
fi

mkdir -p data-staging
touch data-staging/.gitkeep

have_docker=0
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  have_docker=1
fi

if [[ "$MODE" == "auto" ]]; then
  if [[ $have_docker -eq 1 ]]; then
    MODE="docker"
  else
    MODE="local"
  fi
fi

if [[ "$MODE" == "docker" ]]; then
  if [[ $have_docker -eq 0 ]]; then
    echo "ERROR: Docker not available. Use: $0 --local"
    exit 1
  fi
  echo "Starting STAGING via Docker (profile staging)…"
  echo "  container: mexc-alert-bot-staging"
  echo "  data:      ./data-staging  (prod uses ./data — untouched)"
  docker compose --profile staging up -d --build mexc-bot-staging
  echo ""
  docker ps --filter name=mexc-alert-bot --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}' || true
  echo ""
  echo "Logs: docker logs -f mexc-alert-bot-staging"
  echo "Stop:  ./scripts/staging_down.sh"
  echo "Prod container (if any) was NOT stopped."
  exit 0
fi

# --- Local Python process ---
if [[ -f .staging.pid ]]; then
  oldpid=$(cat .staging.pid || true)
  if [[ -n "${oldpid:-}" ]] && kill -0 "$oldpid" 2>/dev/null; then
    echo "Staging already running (pid $oldpid). Logs: tail -f .staging.log"
    echo "Stop first: ./scripts/staging_down.sh"
    exit 0
  fi
  rm -f .staging.pid
fi

if [[ -x .venv/bin/python ]]; then
  PY="$ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PY=python3
else
  echo "ERROR: no python3"; exit 1
fi

# Load .env.staging into this shell (KEY=VAL lines only; no export needed yet)
set -a
# shellcheck disable=SC1091
source /dev/null
while IFS= read -r line || [[ -n "$line" ]]; do
  # skip comments / blank
  [[ "$line" =~ ^[[:space:]]*# ]] && continue
  [[ -z "${line// }" ]] && continue
  if [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
    export "$line"
  fi
done < .env.staging
set +a

# Force isolation paths (override any mistaken ALERTS_FILE)
export ALERTS_FILE="$ROOT/data-staging/alerts.json"
# Prevent accidental load of prod .env winning paths — process env already set;
# python-dotenv does not override existing env vars by default.
export FEATURE_LEARNING="${FEATURE_LEARNING:-true}"
export FEATURE_FUTURES_ALERTS="${FEATURE_FUTURES_ALERTS:-true}"
export FEATURE_MOVER_SCANNER="${FEATURE_MOVER_SCANNER:-true}"
export FEATURE_NEWS_MONITOR="${FEATURE_NEWS_MONITOR:-false}"
export FEATURE_VOICE="${FEATURE_VOICE:-false}"

if [[ -z "${TELEGRAM_BOT_TOKEN:-}" ]]; then
  echo "ERROR: TELEGRAM_BOT_TOKEN empty after loading .env.staging"
  exit 1
fi

echo "Starting STAGING via local Python…"
echo "  python:   $PY ($($PY --version 2>&1))"
echo "  data:     $ALERTS_FILE"
echo "  learning: $FEATURE_LEARNING  futures: $FEATURE_FUTURES_ALERTS  movers: $FEATURE_MOVER_SCANNER"
echo "  log:      $ROOT/.staging.log"

# Prefer certifi CA bundle on macOS / broken system cert stores
if "$PY" -c "import certifi" 2>/dev/null; then
  export SSL_CERT_FILE="$("$PY" -m certifi)"
  export REQUESTS_CA_BUNDLE="$SSL_CERT_FILE"
fi

: >"$ROOT/.staging.log"
nohup "$PY" -m mexc_bot.main >>"$ROOT/.staging.log" 2>&1 &
echo $! >"$ROOT/.staging.pid"
sleep 2
if kill -0 "$(cat "$ROOT/.staging.pid")" 2>/dev/null; then
  echo "Staging PID $(cat "$ROOT/.staging.pid") running."
  echo "Logs:   tail -f .staging.log"
  echo "Stop:   ./scripts/staging_down.sh"
  echo "Telegram: open the STAGING bot (not prod) → /s  /l  /events  /brief"
  # Soft network check (MEXC often blocked on some ISPs)
  if ! "$PY" -c "import requests,certifi; r=requests.get('https://api.mexc.com/api/v3/ping',timeout=8,verify=certifi.where()); assert r.status_code==200 and 'pong' in r.text.lower() or r.json()=={}" 2>/dev/null; then
    echo ""
    echo "NOTE: This machine may not reach MEXC cleanly (prices/movers need MEXC)."
    echo "      Telegram commands still work. For full price tests use the droplet or VPN."
  fi
  exit 0
fi

echo "ERROR: process exited immediately. Last log lines:"
tail -40 "$ROOT/.staging.log" || true
rm -f "$ROOT/.staging.pid"
exit 1
