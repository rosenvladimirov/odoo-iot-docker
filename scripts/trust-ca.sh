#!/bin/bash
# ============================================
# TRUST STEP-CA ROOT CERTIFICATE
# ============================================

set -e

echo "=================================================="
echo "  Trust Step-CA Root Certificate"
echo "=================================================="

CA_CERT_PATH="./docker/step-ca/certs/root_ca.crt"

if [ ! -f "$CA_CERT_PATH" ]; then
    echo "✗ Root CA certificate not found: $CA_CERT_PATH"
    echo "Run setup-ca.sh first"
    exit 1
fi

# Detect OS
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    echo "Installing on Linux..."

    # Copy certificate
    sudo cp "$CA_CERT_PATH" /usr/local/share/ca-certificates/iot-box-ca.crt

    # Update certificates
    sudo update-ca-certificates

    echo "✓ Certificate installed"

elif [[ "$OSTYPE" == "darwin"* ]]; then
    echo "Installing on macOS..."

    # Add to keychain
    sudo security add-trusted-cert \
        -d -r trustRoot \
        -k /Library/Keychains/System.keychain \
        "$CA_CERT_PATH"

    echo "✓ Certificate installed"

else
    echo "⚠ Unsupported OS: $OSTYPE"
    echo "Manually import: $CA_CERT_PATH"
    exit 1
fi

echo "=================================================="
echo "✓ Root CA certificate trusted!"
echo "=================================================="
echo ""
echo "You can now access https://$(grep IOT_DOMAIN .env | cut -d'=' -f2)"
echo "without security warnings."
echo ""
echo "=================================================="