#!/usr/bin/env bash
set -e

DB_NAME="${DB_NAME:-odoo}"
DB_HOST="${DB_HOST:-db}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-odoo}"
DB_PASSWORD="${DB_PASSWORD:-odoo}"

echo "Waiting for Postgres at ${DB_HOST}:${DB_PORT} (db=${DB_NAME}, user=${DB_USER})..."

python3 - << EOF
import time
import psycopg2
while True:
    try:
        conn = psycopg2.connect(
            dbname='${DB_NAME}',
            user='${DB_USER}',
            password='${DB_PASSWORD}',
            host='${DB_HOST}',
            port=${DB_PORT},
        )
        conn.close()
        break
    except Exception:
        time.sleep(2)
EOF

echo "Initializing Odoo database '${DB_NAME}' with -i base..."
python3 /app/odoo-bin -i base -d "${DB_NAME}" --stop-after-init
echo "Odoo database initialization completed."