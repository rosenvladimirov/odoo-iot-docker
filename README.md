# Odoo IoT Box - Docker Edition with Step-CA

## Certificate Management with Step-CA

This project uses **Step-CA** (Smallstep Certificate Authority) for automated certificate management:

### Features
- ✅ Self-signed CA for local development
- ✅ Automatic certificate generation
- ✅ Auto-renewal every 24 hours
- ✅ Trusted certificates (no browser warnings)
- ✅ REST API for certificate management

### Quick Start
```bash
# 1. Complete setup (creates CA, generates certs, starts services)
make init

# 2. Access IoT Box (no security warnings!)
https://iot-box.local
```

### Manual Setup
```bash
# 1. Create .env file
make setup

# 2. Setup Certificate Authority
make setup-ca

# 3. Trust CA on your machine (removes browser warnings)
make trust-ca

# 4. Start services
make up
```

### Certificate Management
```bash
# Generate new certificate
make gen-cert

# Renew certificate
make renew-cert

# Check certificate expiration
make check-cert

# View CA information
make ca-info
```

### Step-CA Dashboard

Access Step-CA API:
```
https://localhost:9000/health
```

### Troubleshooting

**Certificate not trusted:**
```bash
make trust-ca
```

**Generate new certificates:**
```bash
make gen-cert DOMAIN=my-iot-box.local
```

**Check logs:**
```bash
make logs-ca
```

### Architecture
```
┌─────────────┐
│   Browser   │
└──────┬──────┘
       │ HTTPS (trusted!)
┌──────▼──────┐
│   Traefik   │
│   (443)     │
└──────┬──────┘
       │
┌──────▼──────┐      ┌────────────┐
│  IoT Box    │◄─────┤  Step-CA   │
│  (8069)     │      │  (9000)    │
└─────────────┘      └────────────┘
                     Auto-renewal
```