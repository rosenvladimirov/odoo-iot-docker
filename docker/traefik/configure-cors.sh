#!/bin/bash
# ============================================
# CONFIGURE CORS FROM ENVIRONMENT VARIABLES
# ============================================

set -e

MIDDLEWARES_FILE="/etc/traefik/dynamic/middlewares.yml"
BACKUP_FILE="/etc/traefik/dynamic/middlewares.yml.backup"

# Backup original
if [ ! -f "$BACKUP_FILE" ]; then
    cp "$MIDDLEWARES_FILE" "$BACKUP_FILE"
fi

echo "Configuring CORS middleware..."

# Read environment variables
CORS_ORIGINS="${CORS_ALLOWED_ORIGINS:-*}"
CORS_METHODS="${CORS_ALLOWED_METHODS:-GET,POST,PUT,DELETE,OPTIONS,PATCH}"
CORS_HEADERS="${CORS_ALLOWED_HEADERS:-*}"
CORS_EXPOSED="${CORS_EXPOSED_HEADERS:-*}"
CORS_MAX_AGE="${CORS_MAX_AGE:-86400}"
CORS_CREDENTIALS="${CORS_ALLOW_CREDENTIALS:-true}"

# Convert comma-separated origins to YAML array
IFS=',' read -ra ORIGINS_ARRAY <<< "$CORS_ORIGINS"
ORIGINS_YAML=""
for origin in "${ORIGINS_ARRAY[@]}"; do
    ORIGINS_YAML="${ORIGINS_YAML}          - \"$origin\"\n"
done

# Convert methods to YAML array
IFS=',' read -ra METHODS_ARRAY <<< "$CORS_METHODS"
METHODS_YAML=""
for method in "${METHODS_ARRAY[@]}"; do
    METHODS_YAML="${METHODS_YAML}          - $method\n"
done

# Generate new middlewares config
cat > "$MIDDLEWARES_FILE" <<EOF
# ============================================
# AUTO-GENERATED CORS CONFIGURATION
# Generated at: $(date)
# ============================================

http:
  middlewares:
    # === CORS Middleware ===
    cors-headers:
      headers:
        accessControlAllowMethods:
$(echo -e "$METHODS_YAML")
        accessControlAllowHeaders:
          - "$CORS_HEADERS"
        accessControlAllowOriginList:
$(echo -e "$ORIGINS_YAML")
        accessControlExposeHeaders:
          - "$CORS_EXPOSED"
        accessControlMaxAge: $CORS_MAX_AGE
        accessControlAllowCredentials: $CORS_CREDENTIALS
        addVaryHeader: true

    # === Security Headers ===
    security-headers:
      headers:
        frameDeny: false
        contentTypeNosniff: true
        browserXssFilter: true
        referrerPolicy: "strict-origin-when-cross-origin"
        customFrameOptionsValue: "SAMEORIGIN"
        customResponseHeaders:
          X-Powered-By: "Odoo-IoT-Box"
          Server: ""

    # === Rate Limiting ===
    rate-limit:
      rateLimit:
        average: 100
        period: 1m
        burst: 50

    # === Compression ===
    compression:
      compress: true

    # === Redirect HTTPS ===
    https-redirect:
      redirectScheme:
        scheme: https
        permanent: true

    # === Chain: CORS + Security ===
    iot-chain:
      chain:
        middlewares:
          - cors-headers
          - security-headers
          - compression
EOF

echo "✓ CORS configured successfully"
echo "  Origins: $CORS_ORIGINS"
echo "  Methods: $CORS_METHODS"
echo "  Credentials: $CORS_CREDENTIALS"