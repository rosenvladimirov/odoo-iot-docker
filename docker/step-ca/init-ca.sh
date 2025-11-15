#!/bin/bash
# ============================================
# STEP-CA INITIALIZATION SCRIPT
# ============================================

set -e

STEP_HOME="/home/step"
CONFIG_DIR="$STEP_HOME/config"
CERTS_DIR="$STEP_HOME/certs"
SECRETS_DIR="$STEP_HOME/secrets"
DB_DIR="$STEP_HOME/db"

CA_NAME="${STEP_CA_NAME:-IoT Box CA}"
DNS_NAME="${IOT_DOMAIN:-iot-box.local}"
PROVISIONER_NAME="${STEP_CA_PROVISIONER:-admin}"
PASSWORD="${STEP_CA_PASSWORD:-changeme}"

echo "=================================================="
echo "  Step-CA Initialization"
echo "=================================================="
echo "CA Name: $CA_NAME"
echo "DNS Name: $DNS_NAME"
echo "Provisioner: $PROVISIONER_NAME"
echo "=================================================="

# Check if already initialized
if [ -f "$CERTS_DIR/root_ca.crt" ]; then
    echo "✓ Step-CA already initialized"
    echo "Starting Step-CA server..."
    exec step-ca "$CONFIG_DIR/ca.json" --password-file=<(echo "$PASSWORD")
fi

# Create directories
mkdir -p "$CONFIG_DIR" "$CERTS_DIR" "$SECRETS_DIR" "$DB_DIR"

echo "Initializing Certificate Authority..."

# Initialize CA
step ca init \
    --name="$CA_NAME" \
    --dns="$DNS_NAME" \
    --address=":9000" \
    --provisioner="$PROVISIONER_NAME" \
    --password-file=<(echo "$PASSWORD") \
    --provisioner-password-file=<(echo "$PASSWORD") \
    --deployment-type="standalone" \
    --context="docker"

# Move files to correct locations
mv ~/.step/certs/* "$CERTS_DIR/" 2>/dev/null || true
mv ~/.step/secrets/* "$SECRETS_DIR/" 2>/dev/null || true
mv ~/.step/config/ca.json "$CONFIG_DIR/" 2>/dev/null || true
mv ~/.step/config/defaults.json "$CONFIG_DIR/" 2>/dev/null || true
mv ~/.step/db/* "$DB_DIR/" 2>/dev/null || true

# Update paths in ca.json
sed -i "s|$HOME/.step|$STEP_HOME|g" "$CONFIG_DIR/ca.json"

# Set permissions
chmod 600 "$SECRETS_DIR"/*
chmod 644 "$CERTS_DIR"/*

# Get fingerprint
FINGERPRINT=$(step certificate fingerprint "$CERTS_DIR/root_ca.crt")
echo "$FINGERPRINT" > "$CERTS_DIR/root_ca_fingerprint.txt"

echo "=================================================="
echo "✓ Step-CA initialized successfully!"
echo "=================================================="
echo "Root CA Fingerprint: $FINGERPRINT"
echo ""
echo "Root CA Certificate:"
cat "$CERTS_DIR/root_ca.crt"
echo "=================================================="

# Start the CA server
echo "Starting Step-CA server..."
exec step-ca "$CONFIG_DIR/ca.json" --password-file=<(echo "$PASSWORD")
