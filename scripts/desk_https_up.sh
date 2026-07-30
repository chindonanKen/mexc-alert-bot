#!/usr/bin/env bash
# Bring up AD Desk behind HTTPS (required for browser microphone).
# Usage (on droplet):
#   ./scripts/desk_https_up.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "Missing .env — need DESK_API_TOKEN (and XAI_API_KEY for voice)"
  exit 1
fi

if ! grep -q '^DESK_API_TOKEN=.\+' .env 2>/dev/null; then
  echo "WARN: DESK_API_TOKEN empty or missing in .env"
fi

echo "==> Building desk + Caddy HTTPS front"
docker compose --profile desk --profile desk-https up -d --build mexc-desk mexc-desk-https

echo ""
echo "==> Status"
docker ps --filter name=mexc-ad-desk --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
docker ps --filter name=mexc-desk-https --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'

IP="$(curl -s --max-time 3 ifconfig.me 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}' || echo YOUR_DROPLET_IP)"

echo ""
echo "Open the desk on HTTPS (not :8080):"
echo "  https://${IP}/?token=YOUR_DESK_API_TOKEN"
echo ""
echo "Browser will warn about a self-signed certificate — click Advanced → Proceed."
echo "After that, the mic is allowed (secure context)."
echo ""
echo "Do NOT use http://${IP}:8080 for voice — mic will stay blocked."
