#!/bin/bash
# ============================================
# RENEW CERTIFICATE
# ============================================

set -e

DOMAIN="${1:-${IOT_DOMAIN:-iot-box.local}}"
CERT_DIR="${2:-/home/step/certs}"
CA_URL="${STEP_CA_URL:-https://localhost:9000}"

CERT_FILE="$CERT_DIR/${DOMAIN}.crt"
KEY_FILE="$CERT_DIR/${DOMAIN}.key"

echo "=================================================="
echo "  Renewing Certificate"
echo "=================================================="
echo "Certificate: $CERT_FILE"
echo "=================================================="

if [ ! -f "$CERT_FILE" ]; then
    echo "✗ Certificate not found: $CERT_FILE"
    exit 1
fi

# Check expiration
EXPIRY=$(step certificate inspect "$CERT_FILE" --format=json | jq -r '.validity.end')
EXPIRY_EPOCH=$(date -d "$EXPIRY" +%s 2>/dev/null || date -j -f "%Y-%m-%dT%H:%M:%SZ" "$EXPIRY" +%s)
NOW_EPOCH=$(date +%s)
DAYS_LEFT=$(( ($EXPIRY_EPOCH - $NOW_EPOCH) / 86400 ))

echo "Certificate expires in $DAYS_LEFT days ($EXPIRY)"

if [ $DAYS_LEFT -gt 30 ]; then
    echo "Certificate is still valid for $DAYS_LEFT days"
    echo "Renewal not needed (threshold: 30 days)"
    exit 0
fi

echo "Renewing certificate..."

# Renew
step ca renew \
    "$CERT_FILE" \
    "$KEY_FILE" \
    --ca-url="$CA_URL" \
    --root="$CERT_DIR/root_ca.crt" \
    --force

echo "=================================================="
echo "✓ Certificate renewed successfully!"
echo "=================================================="

# Show new expiration
NEW_EXPIRY=$(step certificate inspect "$CERT_FILE" --format=json | jq -r '.validity.end')
echo "New expiration: $NEW_EXPIRY"

# Reload Traefik (if running)
if command -v docker &> /dev/null; then
    echo "Reloading Traefik..."
    docker kill -s HUP iot-traefik 2>/dev/null || echo "Traefik not running"
fi