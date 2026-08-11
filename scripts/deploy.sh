#!/usr/bin/env bash
#
# Simple one-command deploy helper for the MEXC Alert Bot on your VPS.
#
# Usage (after git push from your machine / Grok):
#   ./scripts/deploy.sh
#
# What it does:
#   - git pull (gets the latest from GitHub)
#   - docker compose up -d --build (rebuilds if needed and restarts)
#   - shows the last 80 lines of logs so you can verify quickly
#
# Make executable once: chmod +x scripts/deploy.sh

set -euo pipefail

# Never wipe SQLite: refuse deploy if code or data looks unsafe.
# (No docker compose down -v, no rm data/, no empty-watchlist with movers on.)
echo "==> Pre-deploy DB durability guard..."
bash scripts/pre_deploy_db_guard.sh --strict

echo "==> Pulling latest code from GitHub..."
git pull --ff-only

# Re-check after pull (new code may introduce banned patterns)
echo "==> Post-pull DB safety re-check..."
bash scripts/pre_deploy_db_guard.sh --strict

echo "==> Rebuilding and restarting container (bind-mount ./data preserved)..."
# NOTE: never use `docker compose down -v` here — that would destroy volumes.
docker compose up -d --build

echo "==> Recent logs (last 80 lines):"
docker compose logs --tail 80

echo ""
echo "Done. Use 'docker compose logs -f mexc-bot' to follow live logs."
echo "Data durability: ./data is bind-mounted; rebuilds do not erase alerts.db."