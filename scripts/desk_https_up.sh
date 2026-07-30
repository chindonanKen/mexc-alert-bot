#!/usr/bin/env bash
# Bring up AD Desk behind HTTPS so the browser microphone works (no file upload).
# Usage on droplet (or any Docker host):
#   ./scripts/desk_https_up.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "Missing .env — need DESK_API_TOKEN and XAI_API_KEY"
  exit 1
fi

if ! grep -qE '^DESK_API_TOKEN=.+' .env 2>/dev/null; then
  echo "WARN: DESK_API_TOKEN empty or missing in .env"
fi
if ! grep -qE '^XAI_API_KEY=.+' .env 2>/dev/null && ! grep -qE '^GROK_API_KEY=.+' .env 2>/dev/null; then
  echo "WARN: XAI_API_KEY missing — voice STT/tools will fail until set"
fi

# Recreate so port maps match compose (old desk had public :8080 only)
echo "==> Stopping old desk containers (if any)"
docker compose --profile desk --profile desk-https stop mexc-desk mexc-desk-https 2>/dev/null || true
docker rm -f mexc-ad-desk mexc-desk-https 2>/dev/null || true

echo "==> Building desk + Caddy HTTPS front"
docker compose --profile desk --profile desk-https up -d --build --force-recreate mexc-desk mexc-desk-https

echo ""
echo "==> Waiting for health"
sleep 3
docker ps --filter name=mexc-ad-desk --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
docker ps --filter name=mexc-desk-https --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'

IP="$(curl -s --max-time 3 ifconfig.me 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}' || echo YOUR_DROPLET_IP)"

echo ""
echo "==> HTTPS smoke (self-signed ok)"
if curl -sk --max-time 10 "https://127.0.0.1/api/health" | head -c 200; then
  echo ""
  echo "Local HTTPS health: OK"
else
  echo "WARN: https://127.0.0.1/api/health failed — check: docker logs mexc-desk-https"
fi

# DigitalOcean / ufw tip
if command -v ufw >/dev/null 2>&1; then
  if ufw status 2>/dev/null | grep -qi active; then
    echo ""
    echo "If external browser fails, open firewall:"
    echo "  sudo ufw allow 80/tcp && sudo ufw allow 443/tcp && sudo ufw reload"
  fi
fi

TOKEN_HINT="YOUR_DESK_API_TOKEN"
if grep -qE '^DESK_API_TOKEN=.+' .env 2>/dev/null; then
  TOKEN_HINT="(from .env DESK_API_TOKEN)"
fi

echo ""
echo "=============================================="
echo "  Open the FULL desk (mic works here):"
echo "    https://${IP}/?token=${TOKEN_HINT}"
echo ""
echo "  1) Browser warns about certificate → Advanced → Proceed"
echo "  2) Allow microphone when prompted"
echo "  3) Voice tab → Tap to record → speak a command"
echo ""
echo "  Do NOT use http://${IP}:8080 for voice."
echo "  Mic requires https:// (secure context)."
echo "=============================================="
