#!/bin/bash
# ============================================
# SETUP STEP-CA CERTIFICATE AUTHORITY
# ============================================

set -e

echo "=================================================="
echo "  Step-CA Setup Script"
echo "=================================================="

# Load environment
if [ ! -f .env ]; then
    echo "Error: .env file not found"
    echo "Run: cp .env.example .env"
    exit 1
fi

source .env

DOMAIN="${IOT_DOMAIN:-iot-box.local}"

echo "Domain: $DOMAIN"
echo ""

# Create directories
mkdir -p docker/step-ca/certs docker/step-ca/config docker/step-ca/scripts logs/step-ca

# Start Step-CA
echo "Starting Step-CA container..."
docker-compose up -d step-ca

# Wait for initialization
echo "Waiting for Step-CA to initialize (30s)..."
sleep 30

# Check if initialized
if docker-compose exec -T step-ca test -f /home/step/certs/root_ca.crt; then
    echo "✓ Step-CA initialized successfully"
else
    echo "✗ Step-CA initialization failed"
    echo "Check logs: docker-compose logs step-ca"
    exit 1
fi

# Generate certificate for IoT Box domain
echo ""
echo "Generating certificate for $DOMAIN..."
docker-compose exec -T step-ca /usr/local/bin/generate-cert.sh "$DOMAIN"

echo ""
echo "=================================================="
echo "✓ Step-CA setup complete!"
echo "=================================================="
echo ""
echo "Next steps:"
echo "  1. Trust the CA: make trust-ca"
echo "  2. Start services: make up"
echo "  3. Access IoT Box: https://$DOMAIN"
echo ""
echo "=================================================="