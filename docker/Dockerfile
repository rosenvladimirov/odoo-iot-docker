FROM ubuntu:24.04

# === BUILD ARGS ===
ARG DEBIAN_FRONTEND=noninteractive
ARG IOT_VERSION=1.0.0

# === LABELS ===
LABEL maintainer="vladimirov.rosen@gmail.com"
LABEL description="Odoo IoT Box - Docker Edition"
LABEL version="${IOT_VERSION}"

# === ENVIRONMENT ===
ENV IOT_IN_DOCKER=true \
    IOT_VERSION=${IOT_VERSION} \
    PYTHONUNBUFFERED=1 \
    LANG=C.UTF-8

# === SYSTEM PACKAGES ===
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Core utilities
    ca-certificates \
    curl \
    wget \
    git \
    sudo \
    gnupg \
    # Python
    python3 \
    python3-pip \
    python3-dev \
    python3-venv \
    # Networking
    iputils-ping \
    net-tools \
    iproute2 \
    dbus \
    libdbus-1-dev \
    # USB/Serial
    usbutils \
    pciutils \
    # Build tools (for Python packages)
    gcc \
    g++ \
    make \
    libssl-dev \
    libffi-dev \
    libxml2-dev \
    libxslt1-dev \
    libjpeg-dev \
    zlib1g-dev \
    # CUPS client libraries (не daemon!)
    libcups2-dev \
    # Printer support
    python3-cups \
    # Serial/USB Python deps
    libusb-1.0-0-dev \
    libudev-dev \
    # WiFi management
    python3-dbus \
    # Cleanup
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# === PYTHON PACKAGES ===
COPY requirements.txt /tmp/
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# === CREATE APP USER ===
RUN useradd -m -s /bin/bash iotbox \
    && mkdir -p /app /app/config /app/logs /app/certs /app/data \
    && chown -R iotbox:iotbox /app

# === COPY APPLICATION ===
WORKDIR /app
COPY --chown=iotbox:iotbox . /app/

# === ENTRYPOINT ===
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# === VOLUMES ===
VOLUME ["/app/config", "/app/logs", "/app/certs", "/app/data"]

# === EXPOSE PORTS ===
EXPOSE 8069

# === HEALTH CHECK ===
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8069/hw_proxy/hello || exit 1

# === USER ===
USER iotbox

# === START ===
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python3", "/app/odoo-bin", "--config=/app/config/odoo.conf"]