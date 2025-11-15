#!/bin/bash
# ============================================
# START IOT BOX WITH TRAEFIK
# ============================================

set -e

echo "=================================================="
echo "  Starting Odoo IoT Box with Traefik"
echo "=================================================="

# Check if .env exists
if [ ! -f .env ]; then
    echo "Error: .env file not found"
    echo "Run: make setup"
    exit 1
fi

# Load environment
source .env

# Check certificates
if [ ! -f docker/traefik/certs/cert.pem ] || [ ! -f docker/traefik/certs/key.pem ]; then
    echo "Certificates not found. Generating..."
    bash docker/traefik/certs/generate-certs.sh
fi

# Create directories
mkdir -p logs/traefik logs/iot config data

# Start services
echo "Starting Docker containers..."
docker-compose up -d

# Wait for services
echo "Waiting for services to start..."
sleep 10

# Health checks
echo "Checking health..."

# Traefik
if curl -f -k https://localhost:443 > /dev/null 2>&1; then
    echo "✓ Traefik is responding"
else
    echo "✗ Traefik is not responding"
fi

# IoT Box
if curl -f http://localhost:8069/hw_proxy/hello > /dev/null 2>&1; then
    echo "✓ IoT Box is responding"
else
    echo "✗ IoT Box is not responding"
fi

echo "=================================================="
echo "  Services Started!"
echo "=================================================="
echo ""
echo "Access URLs:"
echo "  IoT Box:           https://${IOT_DOMAIN:-iot-box.local}"
echo "  Traefik Dashboard: http://localhost:${TRAEFIK_DASHBOARD_PORT:-8080}"
echo ""
echo "Logs:"
echo "  docker-compose logs -f"
echo "  docker-compose logs -f iot-box"
echo "  docker-compose logs -f traefik"
echo ""
echo "=================================================="