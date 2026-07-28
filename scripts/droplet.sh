#!/usr/bin/env bash
# Remote droplet helper — run from laptop when SSH host is configured.
#
# Prerequisites:
#   ~/.ssh/config Host (default name: mexc-droplet)
#   Repo on server: ~/mexc-alert-bot  (override DROPLET_REPO)
#
# Usage:
#   ./scripts/droplet.sh status
#   ./scripts/droplet.sh staging-up | staging-down | staging-logs
#   ./scripts/droplet.sh prod-logs
#   ./scripts/droplet.sh deploy-staging
#   ./scripts/droplet.sh deploy-prod     # DESTRUCTIVE to running prod container rebuild — confirm
#   ./scripts/droplet.sh ssh             # interactive shell
#
# Env:
#   DROPLET_SSH_HOST=mexc-droplet
#   DROPLET_REPO=~/mexc-alert-bot

set -euo pipefail

HOST="${DROPLET_SSH_HOST:-mexc-droplet}"
REPO="${DROPLET_REPO:-~/mexc-alert-bot}"
CMD="${1:-}"

usage() {
  sed -n '2,20p' "$0" | sed 's/^# //' | sed 's/^#//'
  exit "${1:-0}"
}

[[ -z "$CMD" || "$CMD" == "-h" || "$CMD" == "--help" ]] && usage 0

remote() {
  # shellcheck disable=SC2029
  ssh -o BatchMode=yes -o ConnectTimeout=15 "$HOST" "$*"
}

remote_tty() {
  ssh -t "$HOST" "$*"
}

case "$CMD" in
  status)
    remote "echo '=== host ===' && hostname && echo '=== containers ===' && docker ps -a --filter name=mexc-alert --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}' && echo '=== disk data ===' && du -sh $REPO/data $REPO/data-staging 2>/dev/null || true"
    ;;
  ssh)
    remote_tty "cd $REPO && exec \$SHELL -l"
    ;;
  staging-logs)
    remote "docker logs --tail ${2:-80} mexc-alert-bot-staging 2>&1"
    ;;
  prod-logs)
    remote "docker logs --tail ${2:-80} mexc-alert-bot 2>&1"
    ;;
  staging-up)
    remote "set -e; cd $REPO; mkdir -p data-staging; if [[ ! -f .env.staging ]]; then echo 'MISSING .env.staging on droplet — copy from .env.staging.example and set staging token'; exit 1; fi; docker compose --profile staging up -d --build mexc-bot-staging; docker ps --filter name=mexc-alert --format 'table {{.Names}}\t{{.Status}}'"
    ;;
  staging-down)
    remote "cd $REPO && docker compose --profile staging stop mexc-bot-staging 2>/dev/null; docker stop mexc-alert-bot-staging 2>/dev/null || true; echo 'staging stopped (prod untouched)'; docker ps --filter name=mexc-alert --format 'table {{.Names}}\t{{.Status}}'"
    ;;
  deploy-staging)
    remote "set -e; cd $REPO; git pull --ff-only origin main; mkdir -p data-staging; docker compose --profile staging up -d --build mexc-bot-staging; docker logs --tail 40 mexc-alert-bot-staging"
    ;;
  deploy-prod)
    echo "About to rebuild PRODUCTION on $HOST ($REPO)."
    echo "Type yes to continue:"
    read -r ans
    [[ "$ans" == "yes" ]] || { echo "Aborted."; exit 1; }
    remote "set -e; cd $REPO; git pull --ff-only origin main; docker compose up -d --build mexc-bot; docker logs --tail 40 mexc-alert-bot"
    ;;
  verify)
    remote "set -e; cd $REPO; git pull --ff-only origin main 2>/dev/null || true; bash scripts/verify_build.sh"
    ;;
  *)
    echo "Unknown command: $CMD"
    usage 1
    ;;
esac
