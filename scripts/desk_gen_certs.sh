#!/usr/bin/env bash
# Generate self-signed TLS cert with IP SAN for AD Desk HTTPS (mic).
# Usage: ./scripts/desk_gen_certs.sh [IP]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CERT_DIR="${ROOT}/deploy/caddy/certs"
mkdir -p "$CERT_DIR"

IP="${1:-}"
if [[ -z "$IP" ]]; then
  IP="$(curl -s --max-time 3 ifconfig.me 2>/dev/null || true)"
fi
if [[ -z "$IP" ]]; then
  IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
fi
if [[ -z "$IP" ]]; then
  IP="127.0.0.1"
fi

CRT="${CERT_DIR}/desk.crt"
KEY="${CERT_DIR}/desk.key"
CONF="${CERT_DIR}/openssl.cnf"

# Refresh if missing or older than 30 days
need=0
if [[ ! -f "$CRT" || ! -f "$KEY" ]]; then
  need=1
elif [[ -n "$(find "$CRT" -mtime +30 2>/dev/null)" ]]; then
  need=1
fi

if [[ "$need" -eq 0 ]]; then
  echo "Using existing certs in ${CERT_DIR} (IP hint: ${IP})"
  exit 0
fi

echo "Generating self-signed cert for IP ${IP} (+ localhost)"
cat >"$CONF" <<EOF
[req]
default_bits = 2048
prompt = no
default_md = sha256
distinguished_name = dn
x509_extensions = v3_req

[dn]
CN = ${IP}
O = AD Desk
OU = private-beta

[v3_req]
subjectAltName = @alt_names
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth

[alt_names]
IP.1 = ${IP}
IP.2 = 127.0.0.1
DNS.1 = localhost
EOF

openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
  -keyout "$KEY" \
  -out "$CRT" \
  -config "$CONF" \
  -extensions v3_req

chmod 644 "$CRT"
chmod 600 "$KEY"
echo "Wrote ${CRT} and ${KEY}"
