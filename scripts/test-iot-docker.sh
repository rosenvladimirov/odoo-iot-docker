#!/bin/bash
set -e

echo "=================================================="
echo "  IoT Box Tests with Step-CA"
echo "=================================================="
echo ""

source .env
DOMAIN="${IOT_DOMAIN:-iot-box.local}"

# Test 1: Step-CA Health
echo "1. Testing Step-CA..."
if curl -f -k https://localhost:${STEP_CA_PORT:-9000}/health 2>&1 | grep -q "ok"; then
    echo "   ✓ Step-CA responding"
else
    echo "   ✗ Step-CA not responding"
fi

# Test 2: Certificate exists
echo "2. Checking certificates..."
if [ -f "./docker/step-ca/certs/cert.pem" ]; then
    EXPIRY=$(docker-compose exec -T step-ca step certificate inspect /home/step/certs/cert.pem --format=json | jq -r '.validity.end')
    echo "   ✓ Certificate exists (expires: $EXPIRY)"
else
    echo "   ✗ Certificate not found"
fi

# Test 3: Traefik
echo "3. Testing Traefik..."
if curl -f -k https://localhost 2>&1 > /dev/null; then
    echo "   ✓ Traefik responding"
else
    echo "   ✗ Traefik not responding"
fi

# Test 4: IoT Box via Traefik
echo "4. Testing IoT Box..."
if curl -f -k https://$DOMAIN/hw_proxy/hello 2>&1 | grep -q "ping"; then
    echo "   ✓ IoT Box responding"
else
    echo "   ✗ IoT Box not responding"
fi

# Test 5: Certificate validation (with trusted CA)
echo "5. Testing certificate trust..."
if curl -f https://$DOMAIN/hw_proxy/hello 2>&1 | grep -q "ping"; then
    echo "   ✓ Certificate trusted (no warnings)"
else
    echo "   ⚠ Certificate not trusted (run: make trust-ca)"
fi

# Test 6: CORS
echo "6. Testing CORS..."
CORS=$(curl -s -k -H "Origin: https://example.com" \
    -H "Access-Control-Request-Method: POST" \
    -X OPTIONS https://$DOMAIN/hw_proxy/hello -I | grep -i "access-control")
if [ -n "$CORS" ]; then
    echo "   ✓ CORS headers present"
else
    echo "   ✗ CORS headers missing"
fi

# Test 7: Auto-renewal daemon
echo "7. Checking renewal daemon..."
if docker-compose exec -T step-ca pgrep -f "renewal-daemon" > /dev/null; then
    echo "   ✓ Renewal daemon running"
else
    echo "   ✗ Renewal daemon not running"
fi

echo ""
echo "=================================================="
echo "  Tests Complete!"
echo "=================================================="