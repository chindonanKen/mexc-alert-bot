#!/usr/bin/env bash
# Stop STAGING only — never touches production mexc-bot / ./data
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

stopped=0

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx 'mexc-alert-bot-staging'; then
    echo "Stopping Docker staging container mexc-alert-bot-staging…"
    docker compose --profile staging stop mexc-bot-staging 2>/dev/null || docker stop mexc-alert-bot-staging 2>/dev/null || true
    stopped=1
  fi
fi

if [[ -f .staging.pid ]]; then
  pid=$(tr -d '[:space:]' < .staging.pid || true)
  if [[ -n "${pid:-}" ]] && [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
    echo "Stopping local staging PID $pid…"
    kill "$pid" 2>/dev/null || true
    for _ in 1 2 3 4 5; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.4
    done
    kill -9 "$pid" 2>/dev/null || true
    stopped=1
  fi
  rm -f .staging.pid
fi

# Clean up any orphaned local staging processes for this repo path only
if command -v pgrep >/dev/null 2>&1; then
  while read -r opid; do
    [[ -z "${opid:-}" ]] && continue
    # skip self
    [[ "$opid" == "$$" ]] && continue
    if ps -p "$opid" -o command= 2>/dev/null | grep -q 'mexc_bot.main'; then
      echo "Stopping orphan mexc_bot.main pid $opid…"
      kill "$opid" 2>/dev/null || true
      stopped=1
    fi
  done < <(pgrep -f '/Users/kennethjohansson/mexc-bot.*mexc_bot.main' 2>/dev/null || true)
fi

if [[ $stopped -eq 0 ]]; then
  echo "No staging process/container found (already stopped)."
else
  echo "Staging stopped. Production (./data + prod token) was not modified."
fi
