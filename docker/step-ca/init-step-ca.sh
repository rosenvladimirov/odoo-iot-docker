#!/bin/bash
# ============================================
# INITIALIZE STEP-CA CERTIFICATE AUTHORITY
# ============================================

set -e

STEP_CA_CONTAINER="step-ca"
DOMAIN="${IOT_DOMAIN:-iot-box.local}"
PASSWORD="${STEP_CA_PASSWORD:-changeme}"

echo "=================================================="
echo "  Initializing Step-CA Certificate Authority"
echo "=================================================="

# Wait for Step-CA to be ready
echo "Waiting for Step-CA to start..."
sleep 5

# Check if already initialized
if docker-compose exec -T $STEP_CA_CONTAINER test -f /home/step/certs/root_ca.crt; then
    echo "✓ Step-CA already initialized"
    exit 0
fi

echo "Initializing Step-CA..."

# Initialize CA (if not done automatically)
docker-compose exec -T $STEP_CA_CONTAINER step ca init \
    --name="IoT Box CA" \
    --dns="$DOMAIN" \
    --address=":9000" \
    --provisioner="admin" \
    --password-file=<(echo "$PASSWORD")

# Start CA
docker-compose restart $STEP_CA_CONTAINER

# Wait for restart
sleep 5

echo "=================================================="
echo "✓ Step-CA initialized successfully!"
echo "=================================================="
echo ""
echo "Root CA certificate:"
docker-compose exec -T $STEP_CA_CONTAINER step ca root
echo ""
echo "To trust this CA on your system:"
echo "  docker-compose exec step-ca step ca root > root_ca.crt"
echo "  sudo cp root_ca.crt /usr/local/share/ca-certificates/"
echo "  sudo update-ca-certificates"
echo "=================================================="