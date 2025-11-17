#!/bin/bash
set -e

echo "=== Setting up IoT Box Infrastructure ==="

NETWORK_NAME="iot-network"
SUBNET="172.25.0.0/16"

# Зареждане на конфигурация от .env файл
if [ -f .env ]; then
    echo "Loading configuration from .env file..."
    export $(grep -v '^#' .env | grep -E 'STEP_CA_PASSWORD|IOT_DOMAIN' | xargs)
fi

PASSWORD="${STEP_CA_PASSWORD:-changeme-secure-password-here}"
DOMAIN="${IOT_DOMAIN:-iot-box.local}"
CERTS_OUTPUT_DIR="./certs"

echo "Using password: ${PASSWORD:0:4}***"
echo "Domain: $DOMAIN"

# Създаване на мрежа
echo ""
echo "Setting up Docker network..."
if docker network inspect $NETWORK_NAME >/dev/null 2>&1; then
    echo "✓ Network '$NETWORK_NAME' already exists"
else
    echo "Creating network '$NETWORK_NAME' with subnet $SUBNET..."
    docker network create \
      --driver bridge \
      --subnet $SUBNET \
      $NETWORK_NAME
    echo "✓ Network '$NETWORK_NAME' created"
fi

# Почистване
echo ""
echo "Cleaning old data..."
docker compose down 2>/dev/null || true

# Създаване на всички Docker volumes
echo ""
echo "Creating Docker volumes..."

VOLUMES=(
    "step-ca-data"
    "postgres-data"
    "cups-config"
    "cups-spool"
    "cups-logs"
    "cups-run"
    "traefik-logs"
    "iot-logs"
    "iot-data"
)

for volume in "${VOLUMES[@]}"; do
    if docker volume inspect $volume >/dev/null 2>&1; then
        echo "  ⚠ Volume '$volume' exists - removing..."
        docker volume rm $volume
    fi
    docker volume create $volume >/dev/null
    echo "  ✓ Created volume: $volume"
done

# Създаване на локални директории за bind mounts
echo ""
echo "Creating local directories..."
mkdir -p ./docker/traefik/dynamic
mkdir -p ./docker/traefik/static
echo "  ✓ ./docker/traefik/{dynamic,static}"

# Създаване на локална директория за експортиране на сертификати
mkdir -p "$CERTS_OUTPUT_DIR"
echo "  ✓ $CERTS_OUTPUT_DIR (for certificate export)"

echo ""
echo "Starting Step-CA (will auto-initialize)..."
docker compose up -d step-ca

# Изчакване Step-CA да се инициализира
echo "Waiting for Step-CA to initialize..."
sleep 10

# Изчакване на API
echo ""
echo "Waiting for Step-CA API..."
MAX_TRIES=60
COUNT=0
while [ $COUNT -lt $MAX_TRIES ]; do
    if curl -k -s https://localhost:9000/health 2>/dev/null | grep -q "ok"; then
        echo "✓ Step-CA API is responding!"
        break
    fi
    COUNT=$((COUNT+1))
    if [ $((COUNT % 10)) -eq 0 ]; then
        echo "Still waiting... ($COUNT/$MAX_TRIES)"
    fi
    sleep 2
done

if [ $COUNT -ge $MAX_TRIES ]; then
    echo "ERROR: Step-CA API did not respond"
    echo "=== Step-CA logs ==="
    docker logs step-ca
    exit 1
fi

echo ""
echo "=== Step-CA is ready! ==="
sleep 3

# Проверка дали root CA съществува
echo "Verifying Step-CA initialization..."
if ! docker exec step-ca test -f /home/step/certs/root_ca.crt; then
    echo "ERROR: Step-CA root CA certificate not found!"
    docker logs step-ca
    exit 1
fi

echo "✓ Step-CA initialized successfully"

# Експортиране на CA сертификати
echo ""
echo "Exporting CA certificates..."
docker cp step-ca:/home/step/certs/root_ca.crt "$CERTS_OUTPUT_DIR/root_ca.crt"
docker cp step-ca:/home/step/certs/intermediate_ca.crt "$CERTS_OUTPUT_DIR/intermediate_ca.crt"

# Създайте пълен chain
cat "$CERTS_OUTPUT_DIR/intermediate_ca.crt" "$CERTS_OUTPUT_DIR/root_ca.crt" > "$CERTS_OUTPUT_DIR/ca-chain.crt"

echo "✓ CA certificates exported"

# Генериране на сертификат за основния домейн
echo ""
echo "Generating certificate for $DOMAIN..."
docker exec step-ca step ca certificate \
    "$DOMAIN" \
    /home/step/certs/cert.pem \
    /home/step/certs/key.pem \
    --ca-url https://localhost:9000 \
    --root /home/step/certs/root_ca.crt \
    --provisioner admin \
    --provisioner-password-file /home/step/secrets/password \
    --force

# Експортиране на сертификатите
docker cp step-ca:/home/step/certs/cert.pem "$CERTS_OUTPUT_DIR/cert.pem"
docker cp step-ca:/home/step/certs/key.pem "$CERTS_OUTPUT_DIR/key.pem"

# Създайте fullchain
cat "$CERTS_OUTPUT_DIR/cert.pem" "$CERTS_OUTPUT_DIR/intermediate_ca.crt" > "$CERTS_OUTPUT_DIR/cert-fullchain.pem"

echo "✓ Certificate for $DOMAIN created"

# Генериране на сертификат за Traefik
echo "Generating certificate for traefik.$DOMAIN..."
docker exec step-ca step ca certificate \
    "traefik.$DOMAIN" \
    /home/step/certs/traefik-cert.pem \
    /home/step/certs/traefik-key.pem \
    --ca-url https://localhost:9000 \
    --root /home/step/certs/root_ca.crt \
    --provisioner admin \
    --provisioner-password-file /home/step/secrets/password \
    --force

docker cp step-ca:/home/step/certs/traefik-cert.pem "$CERTS_OUTPUT_DIR/traefik-cert.pem"
docker cp step-ca:/home/step/certs/traefik-key.pem "$CERTS_OUTPUT_DIR/traefik-key.pem"

# Създайте fullchain за Traefik
cat "$CERTS_OUTPUT_DIR/traefik-cert.pem" "$CERTS_OUTPUT_DIR/intermediate_ca.crt" > "$CERTS_OUTPUT_DIR/traefik-cert-fullchain.pem"

echo "✓ Certificate for traefik.$DOMAIN created"

# Копия за Odoo
echo "Creating certificate copies..."
cp "$CERTS_OUTPUT_DIR/cert-fullchain.pem" "$CERTS_OUTPUT_DIR/odoo-public-cert.pem"
cp "$CERTS_OUTPUT_DIR/key.pem" "$CERTS_OUTPUT_DIR/odoo-private-key.pem"

# Permissions
chmod 644 "$CERTS_OUTPUT_DIR"/*.crt 2>/dev/null || true
chmod 644 "$CERTS_OUTPUT_DIR"/*cert*.pem 2>/dev/null || true
chmod 600 "$CERTS_OUTPUT_DIR"/*key*.pem 2>/dev/null || true

echo ""
echo "=== Setup Complete! ==="
echo ""
echo "Created Docker volumes:"
docker volume ls | grep -E "$(IFS=\|; echo "${VOLUMES[*]}")"

echo ""
echo "Exported certificates to $CERTS_OUTPUT_DIR:"
ls -lh "$CERTS_OUTPUT_DIR/"

# Проверка
echo ""
echo "=== Verifying Certificates ==="
openssl x509 -in "$CERTS_OUTPUT_DIR/root_ca.crt" -noout -subject -dates
echo ""
openssl x509 -in "$CERTS_OUTPUT_DIR/cert.pem" -noout -subject -dates
echo ""
openssl verify -CAfile "$CERTS_OUTPUT_DIR/ca-chain.crt" "$CERTS_OUTPUT_DIR/cert.pem"

echo ""
echo "✅ All done! Infrastructure is ready."
echo ""
echo "Next steps:"
echo "  1. Start all services: docker compose up -d"
echo "  2. Check logs: docker compose logs -f"
echo "  3. Access IoT Box: https://$DOMAIN"
echo "  4. Access Traefik Dashboard: https://traefik.$DOMAIN"
echo ""
echo "Note: Add $CERTS_OUTPUT_DIR/root_ca.crt to your browser's trusted certificates"