#!/usr/bin/env bash
# Bring up AD Desk with dual entry:
#   https://IP/        → mic works (self-signed openssl cert + Caddy)
#   http://IP:8080/    → UI + text agent (mic blocked by browser)
#
# Usage on droplet:
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

if ! command -v openssl >/dev/null 2>&1; then
  echo "ERROR: openssl required to generate desk TLS certs"
  exit 1
fi

IP="$(curl -s --max-time 3 ifconfig.me 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}' || echo 127.0.0.1)"

echo "==> TLS certs (IP SAN for ${IP})"
bash "${ROOT}/scripts/desk_gen_certs.sh" "$IP"

if [[ ! -f deploy/caddy/certs/desk.crt || ! -f deploy/caddy/certs/desk.key ]]; then
  echo "ERROR: cert generation failed"
  exit 1
fi

echo "==> Stopping old desk / https containers"
docker compose --profile desk --profile desk-https stop mexc-desk mexc-desk-https 2>/dev/null || true
docker rm -f mexc-ad-desk mexc-desk-https 2>/dev/null || true

echo "==> Building desk + Caddy HTTPS front"
docker compose --profile desk --profile desk-https up -d --build --force-recreate mexc-desk mexc-desk-https

echo ""
echo "==> Waiting for health"
sleep 4
docker ps --filter name=mexc-ad-desk --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
docker ps --filter name=mexc-desk-https --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'

echo ""
echo "==> Smoke checks"
HTTP_OK=0
HTTPS_OK=0
if curl -s --max-time 8 "http://127.0.0.1:8080/api/health" | head -c 200; then
  echo ""
  echo "HTTP  :8080 health: OK"
  HTTP_OK=1
else
  echo "WARN: http://127.0.0.1:8080/api/health failed"
fi
if curl -sk --max-time 8 "https://127.0.0.1/api/health" | head -c 200; then
  echo ""
  echo "HTTPS :443 health: OK"
  HTTPS_OK=1
else
  echo "WARN: https://127.0.0.1/api/health failed — docker logs mexc-desk-https"
  docker logs --tail 30 mexc-desk-https 2>&1 || true
fi

if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -qi active; then
  echo ""
  echo "UFW active — ensure:"
  echo "  sudo ufw allow 8080/tcp   # HTTP desk"
  echo "  sudo ufw allow 443/tcp    # HTTPS mic"
fi

TOKEN_HINT="YOUR_DESK_API_TOKEN"
echo ""
echo "=============================================="
echo "  How to open AD Desk"
echo ""
echo "  FULL (mic + voice):"
echo "    https://${IP}/?token=${TOKEN_HINT}"
echo "    Browser: Advanced → Proceed to site (self-signed)"
echo "    Then allow microphone."
echo ""
echo "  UI + text agent only (mic blocked by browser):"
echo "    http://${IP}:8080/?token=${TOKEN_HINT}"
echo "    Same as droplet Grok HTTP guide — fine for CRUD/text."
echo ""
echo "  Token (on droplet, do not paste into shared chats):"
echo "    grep '^DESK_API_TOKEN=' ~/mexc-alert-bot/.env"
echo ""
if [[ "$HTTPS_OK" -ne 1 ]]; then
  echo "  HTTPS smoke FAILED on this host — use :8080 for text;"
  echo "  check: docker logs mexc-desk-https"
  echo "  and DigitalOcean cloud firewall allows TCP 443."
fi
if [[ "$HTTP_OK" -ne 1 ]]; then
  echo "  HTTP :8080 smoke FAILED — check: docker logs mexc-ad-desk"
fi
echo "=============================================="
