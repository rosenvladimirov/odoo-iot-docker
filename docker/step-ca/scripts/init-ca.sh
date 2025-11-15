#!/usr/bin/env bash
set -euo pipefail

# Използваме същия STEPPATH, който виждаме в контейнера (env показва /home/step)
export STEPPATH=/home/step

CONFIG_DIR=/home/step/config
SECRETS_DIR=/home/step/secrets
CERTS_DIR=/home/step/certs
DB_DIR=/home/step/db

echo "=================================================="
echo "  Step-CA Initialization"
echo "=================================================="

# Ако вече има ca.json на очакваното място – приемаме, че CA е инициализирана
if [ -f "${CONFIG_DIR}/ca.json" ]; then
  echo "[init-ca] Existing CA config found at ${CONFIG_DIR}/ca.json"
  echo "[init-ca] Skipping initialization."
  exit 0
fi

mkdir -p "${CONFIG_DIR}" "${SECRETS_DIR}" "${CERTS_DIR}" "${DB_DIR}"

STEP_CA_NAME="${STEP_CA_NAME:-IoT Box CA}"
IOT_DOMAIN="${IOT_DOMAIN:-iot-box.local}"
STEP_CA_PROVISIONER="${STEP_CA_PROVISIONER:-admin}"
STEP_CA_PASSWORD="${STEP_CA_PASSWORD:-changeme}"

PASS_FILE="${SECRETS_DIR}/password.txt"
echo "${STEP_CA_PASSWORD}" > "${PASS_FILE}"
chmod 600 "${PASS_FILE}"

echo "CA Name: ${STEP_CA_NAME}"
echo "DNS Name: ${IOT_DOMAIN}"
echo "Provisioner: ${STEP_CA_PROVISIONER}"
echo "=================================================="
echo "Initializing Certificate Authority..."
echo

step ca init \
  --name "${STEP_CA_NAME}" \
  --dns "${IOT_DOMAIN}" \
  --address ":9000" \
  --provisioner "${STEP_CA_PROVISIONER}" \
  --password-file "${PASS_FILE}" \
  --deployment-type standalone \
  --remote-management=false

# При STEPPATH=/home/step, step ca init ще ползва:
#   certs:  /home/step/certs
#   secrets:/home/step/secrets
#   db:     /home/step/db
#   config: /home/step/config/ca.json
if [ -f "${CONFIG_DIR}/ca.json" ]; then
  echo "[init-ca] CA config is at ${CONFIG_DIR}/ca.json"
else
  echo "[init-ca] ERROR: Expected CA config at ${CONFIG_DIR}/ca.json not found!"
  exit 1
fi

echo "[init-ca] Step CA initialized successfully."
echo "=================================================="