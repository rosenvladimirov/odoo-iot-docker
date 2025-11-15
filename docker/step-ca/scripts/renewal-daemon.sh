#!/bin/bash
# ============================================
# AUTO-RENEWAL DAEMON
# ============================================

set -e

CHECK_INTERVAL="${RENEWAL_CHECK_INTERVAL:-86400}"  # 24 hours
CERT_DIR="/home/step/certs"

echo "=================================================="
echo "  Certificate Renewal Daemon Started"
echo "=================================================="
echo "Check interval: $CHECK_INTERVAL seconds"
echo "Certificate directory: $CERT_DIR"
echo "=================================================="

while true; do
    echo "[$(date)] Checking certificates for renewal..."

    for cert_file in "$CERT_DIR"/*.crt; do
        # Skip root and intermediate CAs
        if [[ "$cert_file" == *"root_ca"* ]] || [[ "$cert_file" == *"intermediate_ca"* ]]; then
            continue
        fi

        if [ -f "$cert_file" ]; then
            BASENAME=$(basename "$cert_file" .crt)
            KEY_FILE="$CERT_DIR/${BASENAME}.key"

            if [ ! -f "$KEY_FILE" ]; then
                echo "  ⚠ Key not found for $cert_file"
                continue
            fi

            # Check if renewal is needed
            /usr/local/bin/renew-cert.sh "$BASENAME" "$CERT_DIR" || true
        fi
    done

    echo "[$(date)] Check complete. Next check in $CHECK_INTERVAL seconds."
    sleep "$CHECK_INTERVAL"
done