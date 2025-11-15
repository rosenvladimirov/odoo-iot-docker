#!/bin/bash
# ============================================
# GENERATE SELF-SIGNED SSL CERTIFICATES
# ============================================

set -e

CERT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOMAIN="${IOT_DOMAIN:-iot-box.local}"
DAYS=3650  # 10 years

echo "=================================================="
echo "Generating Self-Signed SSL Certificate"
echo "=================================================="
echo "Domain: $DOMAIN"
echo "Valid for: $DAYS days"
echo "Output: $CERT_DIR"
echo "=================================================="

# Check if certificates already exist
if [ -f "$CERT_DIR/cert.pem" ] && [ -f "$CERT_DIR/key.pem" ]; then
    echo "⚠ Certificates already exist!"
    read -p "Overwrite? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Cancelled."
        exit 0
    fi
fi

# Create directory
mkdir -p "$CERT_DIR"
cd "$CERT_DIR"

# Generate private key
echo "1. Generating private key..."
openssl genrsa -out key.pem 4096

# Create certificate signing request
echo "2. Creating certificate signing request..."
cat > openssl.cnf <<EOF
[req]
default_bits = 4096
prompt = no
default_md = sha256
distinguished_name = dn
req_extensions = v3_req

[dn]
C=BG
ST=Sofia
L=Sofia
O=Odoo IoT Box
OU=IoT Department
CN=$DOMAIN

[v3_req]
basicConstraints = CA:FALSE
keyUsage = nonRepudiation, digitalSignature, keyEncipherment
subjectAltName = @alt_names

[alt_names]
DNS.1 = $DOMAIN
DNS.2 = localhost
DNS.3 = *.${DOMAIN}
IP.1 = 127.0.0.1
IP.2 = 192.168.1.100
EOF

# Generate certificate
echo "3. Generating self-signed certificate..."
openssl req \
    -new \
    -x509 \
    -nodes \
    -days $DAYS \
    -key key.pem \
    -out cert.pem \
    -config openssl.cnf \
    -extensions v3_req

# Verify
echo "4. Verifying certificate..."
openssl x509 -in cert.pem -text -noout | grep -A 1 "Subject:"

# Set permissions
chmod 600 key.pem
chmod 644 cert.pem

# Cleanup
rm openssl.cnf

echo "=================================================="
echo "✓ Certificates generated successfully!"
echo "=================================================="
echo "Certificate: $CERT_DIR/cert.pem"
echo "Private Key: $CERT_DIR/key.pem"
echo "=================================================="
echo ""
echo "⚠ IMPORTANT: Self-signed certificates will show"
echo "   security warnings in browsers. To fix:"
echo ""
echo "   1. Import cert.pem to your browser/system"
echo "   2. Or use Let's Encrypt (set SSL_MODE=letsencrypt)"
echo "=================================================="