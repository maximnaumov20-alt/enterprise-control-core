# Production deployment checklist

ECC is not production-ready. This document records the minimum deployment boundary expected for a future controlled pilot; completing these steps does not replace an independent security review.

## Network boundary

- The Compose frontend port binds to `127.0.0.1:5173` by default. Keep it loopback-only when a TLS reverse proxy runs on the same host.
- Terminate HTTPS at a maintained reverse proxy or load balancer. Redirect HTTP to HTTPS and enable HSTS only after HTTPS is confirmed for the entire hostname.
- Configure the proxy to forward requests to the loopback frontend port. Do not expose the API (8000), PostgreSQL (5432), or Redis (6379) to the public network.
- If the reverse proxy is on another host, use a private network or VPN and firewall the frontend listener to that proxy's source addresses. Only then set `ECC_FRONTEND_BIND_ADDRESS` to an appropriate private interface address. Do not bind to all interfaces without matching firewall rules.
- Set `CORS_ORIGINS` to the exact HTTPS origin(s) used by the browser. Do not use wildcard origins with authenticated requests.
- The API trusts `X-Real-IP` only from the supplied Nginx gateway, which overwrites the header. Preserve that trust boundary; do not publish the API port or place an untrusted proxy in front of it.

## Secrets and identity

- Generate unique random values for `POSTGRES_PASSWORD` and `ECC_SECRET_KEY`; the signing key must contain at least 32 bytes.
- Use a unique administrator password and protect the `.env` file with restrictive filesystem permissions (for example, owner read/write only).
- Prefer a secrets manager or protected deployment secret store over a long-lived plaintext environment file.
- Never commit `.env`, credentials, database dumps, age private identities or cloud access keys.
- Keep the age backup private identity outside the application host where practical; store and test recovery access separately.

## Operations

- Pin container images to reviewed versions or immutable digests as part of release management; apply updates through reviewed pull requests.
- Configure host firewalling, OS patching, Docker daemon access restrictions and log rotation.
- Monitor application, proxy, database and Redis health; alert on failed backup jobs, failed restore drills, repeated authentication failures and storage exhaustion.
- Configure encrypted off-host backups, retention and access controls. Perform scheduled download, decrypt and isolated restore drills.
- Define and measure RPO/RTO, incident response, data retention, account provisioning/deprovisioning and security contact procedures.
- Run independent penetration testing and review tenant isolation before any real sensitive or business-critical data is loaded.

## Compose usage

For a same-host reverse proxy, retain the defaults in `.env`:

```dotenv
ECC_FRONTEND_BIND_ADDRESS=127.0.0.1
ECC_FRONTEND_PORT=5173
CORS_ORIGINS=https://ecc.example.com
```

The reverse proxy should connect to `http://127.0.0.1:5173` and provide HTTPS to clients. The application itself does not configure certificates or TLS.

For local development, `CORS_ORIGINS=http://localhost:5173` remains suitable. The frontend is reachable at `http://127.0.0.1:5173` on the Docker host.

## Explicit non-goals of this checklist

This file does not provision TLS, cloud networking, a secrets manager, monitoring, a backup schedule, immutable storage, MFA, or disaster recovery. Those require environment-specific infrastructure and validation. Do not interpret a successful Compose start as production certification.
