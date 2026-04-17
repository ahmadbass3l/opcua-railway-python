# Security Guide

> **Audience:** Developers and system integrators.
> This document covers both OPC UA transport security and HTTP-layer hardening.

---

## Current security posture

| Layer | Current state | Risk |
|---|---|---|
| OPC UA transport | `SecurityMode: None` | Traffic is unencrypted on the wire |
| OPC UA session | Anonymous | No authentication on the hardware connection |
| HTTP (SSE/REST) | No auth | Anyone on the network can read the stream |
| Database | Password auth | Acceptable for internal Docker network |

**The current configuration is appropriate for a trusted private LAN (e.g. an
isolated sensor network) during development and testing. Before connecting to
any routed or public network, apply the hardening steps below.**

---

## Step 1 — OPC UA Transport Security (`SignAndEncrypt`)

### What it provides

- `Sign`: Each message is signed — tamper detection, no confidentiality
- `SignAndEncrypt`: Signed + AES-256 encrypted — equivalent to TLS

### How OPC UA security works

OPC UA uses an **application certificate** (X.509) rather than a CA-signed TLS
certificate. Both client and server exchange and trust each other's certificate
explicitly (trust list, not PKI chain).

### 1.1 Generate a self-signed application certificate

```bash
# Using openssl (or use the opcua-asyncio built-in helper)
openssl req -x509 -newkey rsa:2048 -keyout client_key.pem -out client_cert.pem \
  -days 3650 -nodes \
  -subj "/CN=opcua-railway-python/O=YourOrg/C=AT" \
  -addext "subjectAltName=URI:urn:opcua-railway-python:client"
```

Or use the asyncua helper:

```bash
python -c "
from asyncua.crypto.cert_gen import generate_self_signed_app_certificate
generate_self_signed_app_certificate(
    'client_cert.pem', 'client_key.pem',
    app_uri='urn:opcua-railway-python:client',
    hostnames=['localhost'],
    subject={'CN':'opcua-railway-python','O':'YourOrg','C':'AT'}
)
"
```

### 1.2 Trust the certificate on the hardware server

The exact steps depend on your hardware vendor, but the general procedure is:

1. Export `client_cert.pem` (the public certificate)
2. Copy it to the hardware's **trusted clients** certificate store
   (often accessible via a web UI, file share, or vendor tool)
3. Add the hardware's server certificate to a local trust store

```bash
# Download the server cert (UaExpert or openssl s_client can extract it)
# Place it at: certs/server_cert.pem
```

### 1.3 Configure the service

Add these environment variables:

```env
OPCUA_SECURITY_POLICY=Basic256Sha256
OPCUA_SECURITY_MODE=SignAndEncrypt
OPCUA_CLIENT_CERT=/certs/client_cert.pem
OPCUA_CLIENT_KEY=/certs/client_key.pem
OPCUA_SERVER_CERT=/certs/server_cert.pem
```

Update `opcua_client.py` — replace the `Client` instantiation:

```python
from asyncua.crypto.security_policies import SecurityPolicyBasic256Sha256

async with Client(url=settings.opcua_endpoint) as client:
    await client.set_security(
        SecurityPolicyBasic256Sha256,
        certificate=settings.opcua_client_cert,
        private_key=settings.opcua_client_key,
        server_certificate=settings.opcua_server_cert,
        mode=ua.MessageSecurityMode.SignAndEncrypt,
    )
    # ... rest of connection logic
```

### 1.4 Username/password authentication (alternative to anonymous)

```python
await client.set_user("opcua_user")
await client.set_password("strong_password")
```

Add to environment:
```env
OPCUA_USERNAME=opcua_user
OPCUA_PASSWORD=strong_password
```

---

## Step 2 — HTTP Layer Authentication

### Option A: API Key (simplest, recommended for internal services)

Add a FastAPI dependency:

```python
from fastapi import Header, HTTPException

API_KEY = os.environ["API_KEY"]

async def verify_api_key(x_api_key: str = Header(...)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")
```

Apply to routes:

```python
@app.get("/stream", dependencies=[Depends(verify_api_key)])
@app.get("/readings", dependencies=[Depends(verify_api_key)])
```

### Option B: JWT Bearer tokens (recommended for browser frontends)

```bash
pip install python-jose[cryptography] passlib[bcrypt]
```

```python
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

async def get_current_user(token: str = Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        return payload
    except JWTError:
        raise HTTPException(status_code=401)
```

### Option C: mTLS (recommended for service-to-service)

Run uvicorn with client certificate verification:

```python
uvicorn.run(
    "main:app",
    ssl_certfile="/certs/server_cert.pem",
    ssl_keyfile="/certs/server_key.pem",
    ssl_ca_certs="/certs/ca_cert.pem",
    ssl_cert_reqs="CERT_REQUIRED",
)
```

---

## Step 3 — HTTPS for the HTTP server

```python
uvicorn.run(
    "main:app",
    host="0.0.0.0",
    port=8443,
    ssl_certfile="/certs/server_cert.pem",
    ssl_keyfile="/certs/server_key.pem",
)
```

Or terminate TLS at a reverse proxy (nginx, Traefik) in front of the service —
preferred for production.

---

## Step 4 — Database hardening

```sql
-- Create a dedicated low-privilege user for the service
CREATE USER railway_service WITH PASSWORD 'strong_random_password';

-- Grant only what is needed
GRANT CONNECT ON DATABASE railway TO railway_service;
GRANT USAGE ON SCHEMA public TO railway_service;
GRANT INSERT ON sensor_readings TO railway_service;
GRANT SELECT ON sensor_readings, readings_1min TO railway_service;
```

Update `DB_DSN` to use the restricted user.

---

## Step 5 — Network isolation (Docker)

In `docker-compose.yml`, use an isolated bridge network so the DB is
never exposed on the host network:

```yaml
networks:
  internal:
    driver: bridge

services:
  timescaledb:
    networks: [internal]
    # No 'ports:' mapping — not accessible from outside Docker
  python-service:
    networks: [internal]
    ports: ["8080:8080"]   # Only the HTTP API is exposed
```

---

## Security checklist

- [ ] OPC UA `SecurityMode: None` only on air-gapped/private LAN
- [ ] Enable `SignAndEncrypt` before connecting over any routed network
- [ ] Set `OPCUA_USERNAME` / `OPCUA_PASSWORD` or use X.509 client auth
- [ ] Add HTTP API key or JWT auth before exposing to any external client
- [ ] Use HTTPS (TLS) for all HTTP endpoints in production
- [ ] Use a restricted DB user — no DDL privileges for the service account
- [ ] Rotate secrets via environment variables — never hardcode credentials
- [ ] Isolate the TimescaleDB container on an internal Docker network
