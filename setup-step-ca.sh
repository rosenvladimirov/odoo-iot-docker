#!/bin/bash
set -e

echo "=== Setting up Step-CA ==="

# Конфигурация
STEP_CA_DIR="./docker/step-ca/local-pki"
PASSWORD="changeme-secure-password-here"
CA_NAME="IoT Box CA"
DOMAIN="iot-box.local"

# Почистване
echo "Cleaning old data..."
rm -rf "$STEP_CA_DIR"
mkdir -p "$STEP_CA_DIR/secrets"
mkdir -p "$STEP_CA_DIR/certs"

# Създаване на парола файл
echo "$PASSWORD" > "$STEP_CA_DIR/secrets/password"
chmod 600 "$STEP_CA_DIR/secrets/password"

# Инициализация на Step-CA
echo "Initializing Step-CA..."
docker run --rm \
  -v "$PWD/$STEP_CA_DIR:/home/step" \
  smallstep/step-ca:latest \
  step ca init \
    --name "$CA_NAME" \
    --dns "step-ca" \
    --dns "localhost" \
    --dns "$DOMAIN" \
    --address ":9000" \
    --provisioner "admin" \
    --password-file "/home/step/secrets/password" \
    --provisioner-password-file "/home/step/secrets/password"

echo "Step-CA initialized!"
echo "Files created:"
ls -la "$STEP_CA_DIR/"
ls -la "$STEP_CA_DIR/certs/" 2>/dev/null || echo "No certs dir yet"
ls -la "$STEP_CA_DIR/secrets/" 2>/dev/null || echo "No secrets dir yet"

# Промяна на permissions за Docker
echo "Setting permissions..."
sudo chown -R 1000:1000 "$STEP_CA_DIR" || chown -R 1000:1000 "$STEP_CA_DIR"

# Стартиране на Step-CA контейнера
echo "Starting Step-CA container..."
docker compose up -d step-ca

# Изчакване да стартира
echo "Waiting for Step-CA to be ready..."
sleep 15

# Проверка на healthcheck
MAX_TRIES=30
COUNT=0
until docker exec step-ca step ca health --ca-url=https://localhost:9000 --root=/home/step/certs/root_ca.crt 2>/dev/null; do
  COUNT=$((COUNT+1))
  if [ $COUNT -gt $MAX_TRIES ]; then
    echo "ERROR: Step-CA failed to start after $MAX_TRIES attempts"
    echo "=== Step-CA logs ==="
    docker logs step-ca
    echo "=== Files in container ==="
    docker exec step-ca ls -la /home/step/
    docker exec step-ca ls -la /home/step/certs/ || true
    exit 1
  fi
  echo "Waiting... ($COUNT/$MAX_TRIES)"
  sleep 2
done

echo "=== Step-CA is ready! ==="

# Показване на файловете в контейнера
echo "Files in Step-CA container:"
docker exec step-ca ls -la /home/step/
docker exec step-ca ls -la /home/step/certs/

# Копиране на Root CA сертификат
echo "Copying root CA certificate..."
docker exec step-ca cat /home/step/certs/root_ca.crt > "$STEP_CA_DIR/certs/root_ca.crt"

echo "Root CA copied. Verifying..."
if [ ! -s "$STEP_CA_DIR/certs/root_ca.crt" ]; then
    echo "ERROR: root_ca.crt is empty!"
    exit 1
fi

# Генериране на сертификат за основния домейн
echo "Generating certificate for $DOMAIN..."
docker exec step-ca step ca certificate \
  "$DOMAIN" \
  /home/step/certs/cert.pem \
  /home/step/certs/key.pem \
  --provisioner admin \
  --password-file /home/step/secrets/password \
  --force

# Копиране на сертификатите
docker exec step-ca cat /home/step/certs/cert.pem > "$STEP_CA_DIR/certs/cert.pem"
docker exec step-ca cat /home/step/certs/key.pem > "$STEP_CA_DIR/certs/key.pem"

# Генериране на сертификат за Traefik
echo "Generating certificate for traefik.$DOMAIN..."
docker exec step-ca step ca certificate \
  "traefik.$DOMAIN" \
  /home/step/certs/traefik-cert.pem \
  /home/step/certs/traefik-key.pem \
  --provisioner admin \
  --password-file /home/step/secrets/password \
  --force

docker exec step-ca cat /home/step/certs/traefik-cert.pem > "$STEP_CA_DIR/certs/traefik-cert.pem"
docker exec step-ca cat /home/step/certs/traefik-key.pem > "$STEP_CA_DIR/certs/traefik-key.pem"

# Копия за Odoo
cp "$STEP_CA_DIR/certs/cert.pem" "$STEP_CA_DIR/certs/odoo-public-cert.pem"
cp "$STEP_CA_DIR/certs/key.pem" "$STEP_CA_DIR/certs/odoo-private-key.pem"

# Permissions
chmod 644 "$STEP_CA_DIR/certs"/*.pem
chmod 600 "$STEP_CA_DIR/certs"/*key*.pem

echo "=== Setup Complete! ==="
echo "Generated certificates:"
ls -lh "$STEP_CA_DIR/certs/"

# Проверка на сертификата
echo ""
echo "Certificate details:"
openssl x509 -in "$STEP_CA_DIR/certs/cert.pem" -text -noout | grep -A2 "Subject:"
openssl x509 -in "$STEP_CA_DIR/certs/cert.pem" -text -noout | grep -A5 "Subject Alternative Name" || echo "No SAN found"

echo ""
echo "Root CA details:"
openssl x509 -in "$STEP_CA_DIR/certs/root_ca.crt" -text -noout | grep -A2 "Subject:"