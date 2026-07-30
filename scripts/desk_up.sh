#!/usr/bin/env bash
# HTTP-only AD Desk (matches droplet Grok guide).
# UI + text agent work. Browser mic does NOT work on http://IP:8080.
# For mic: ./scripts/desk_https_up.sh and open https://IP/
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "Missing .env — need DESK_API_TOKEN"
  exit 1
fi

echo "==> Building mexc-desk (HTTP :8080 only)"
docker compose --profile desk up -d --build mexc-desk

IP="$(curl -s --max-time 3 ifconfig.me 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}' || echo YOUR_DROPLET_IP)"

echo ""
docker ps --filter name=mexc-ad-desk --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
echo ""
echo "Open: http://${IP}:8080/?token=YOUR_DESK_API_TOKEN"
echo "Token: grep '^DESK_API_TOKEN=' .env"
echo ""
echo "Mic will stay blocked on HTTP IP. For voice:"
echo "  ./scripts/desk_https_up.sh"
echo "  https://${IP}/?token=..."
