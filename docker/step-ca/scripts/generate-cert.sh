#!/bin/bash
# ============================================
# GENERATE CERTIFICATE FROM STEP-CA
# ============================================

set -e

DOMAIN="${1:-${IOT_DOMAIN:-iot-box.local}}"
OUTPUT_DIR="${2:-/home/step/certs}"
PROVISIONER="${STEP_CA_PROVISIONER:-admin}"
PASSWORD="${STEP_CA_PASSWORD:-changeme}"
CA_URL="${STEP_CA_URL:-https://localhost:9000}"

echo "=================================================="
echo "  Generating Certificate"
echo "=================================================="
echo "Domain: $DOMAIN"
echo "Output: $OUTPUT_DIR"
echo "CA URL: $CA_URL"
echo "=================================================="

# Wait for CA to be ready
until step ca health --ca-url="$CA_URL" 2>/dev/null; do
    echo "Waiting for CA to be ready..."
    sleep 2
done

echo "CA is ready. Generating certificate..."

# Generate certificate
step ca certificate \
    "$DOMAIN" \
    "$OUTPUT_DIR/${DOMAIN}.crt" \
    "$OUTPUT_DIR/${DOMAIN}.key" \
    --provisioner="$PROVISIONER" \
    --password-file=<(echo "$PASSWORD") \
    --ca-url="$CA_URL" \
    --root="$OUTPUT_DIR/root_ca.crt" \
    --san="$DOMAIN" \
    --san="*.$DOMAIN" \
    --san="localhost" \
    --san="127.0.0.1" \
    --not-after=8760h \
    --force

# Create symlinks for Traefik
ln -sf "${DOMAIN}.crt" "$OUTPUT_DIR/cert.pem"
ln -sf "${DOMAIN}.key" "$OUTPUT_DIR/key.pem"

# Set permissions
chmod 644 "$OUTPUT_DIR/${DOMAIN}.crt"
chmod 600 "$OUTPUT_DIR/${DOMAIN}.key"

echo "=================================================="
echo "✓ Certificate generated successfully!"
echo "=================================================="
echo "Certificate: $OUTPUT_DIR/${DOMAIN}.crt"
echo "Private Key: $OUTPUT_DIR/${DOMAIN}.key"
echo "Symlinks created: cert.pem, key.pem"
echo "=================================================="

# Show certificate info
step certificate inspect "$OUTPUT_DIR/${DOMAIN}.crt" --short