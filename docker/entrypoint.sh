#!/bin/bash
set -e

echo "=================================================="
echo "  Odoo IoT Box - Docker Edition"
echo "=================================================="

# === IDENTIFIER ===
if [ -z "$IOT_IDENTIFIER" ]; then
    CONTAINER_ID=$(hostname)
    export IOT_IDENTIFIER="docker-${CONTAINER_ID:0:12}"
    echo "Generated IOT_IDENTIFIER: $IOT_IDENTIFIER"
else
    echo "Using IOT_IDENTIFIER: $IOT_IDENTIFIER"
fi

# === DIRECTORIES ===
mkdir -p /app/config /app/logs /app/certs /app/data
echo "✓ Directories created"

# === ODOO CONFIG ===
if [ ! -f /app/config/odoo.conf ]; then
    echo "Creating default odoo.conf..."
    cat > /app/config/odoo.conf <<EOF
[options]
addons_path = /app/addons
data_dir = /app/data
log_level = info
logfile = /app/logs/odoo.log
log_handler = :INFO,werkzeug:WARNING

# HTTP settings (Traefik handles HTTPS)
http_port = 8069
proxy_mode = True

[iot.box]
# IoT Box settings (auto-populated)

[devtools]
# Development options
EOF
    echo "✓ Created odoo.conf"
else
    echo "✓ Using existing odoo.conf"
fi

# ❌ NGINX SECTION REMOVED

# === PERMISSIONS ===
# Ensure USB devices are accessible
if [ -d /dev/bus/usb ]; then
    echo "✓ USB devices accessible"
fi

# === DBUS ===
if [ -S /var/run/dbus/system_bus_socket ]; then
    echo "✓ DBus socket available"
else
    echo "⚠ DBus socket not found (WiFi management may not work)"
fi

# === START APPLICATION ===
echo "=================================================="
echo "Starting Odoo IoT Box..."
echo "Traefik will handle reverse proxy and SSL"
echo "=================================================="

exec "$@"