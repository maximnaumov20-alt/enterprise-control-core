# Production deployment checklist

ECC is not production-ready. This document records the minimum deployment boundary expected for a future controlled pilot; completing these steps does not replace an independent security review.

## Network boundary

- The Compose frontend port binds to `127.0.0.1:5173` by default. Keep it loopback-only when a TLS reverse proxy runs on the same host.
- Terminate HTTPS at a maintained reverse proxy or load balancer. Redirect HTTP to HTTPS and enable HSTS only after HTTPS is confirmed for the entire hostname.
- Configure the proxy to forward requests to the loopback frontend port. Do not expose the API (8000), PostgreSQL (5432), or Redis (6379) to the public network.
- If the reverse proxy is on another host, use a private network or VPN and firewall the frontend listener to that proxy's source addresses. Only then set `ECC_FRONTEND_BIND_ADDRESS` to an appropriate private interface address. Do not bind to all interfaces without matching firewall rules.
- Set `CORS_ORIGINS` to the exact HTTPS origin(s) used by the browser. Do not use wildcard origins with authenticated requests.
- The bundled Nginx overwrites `X-Real-IP` with the TCP peer address it sees. The API trusts this header only because the API is private to the Compose network and the bundled gateway sets it.
- The bundled Nginx does **not** currently resolve client addresses from `X-Forwarded-For` or configure trusted upstream proxy CIDRs. If another reverse proxy or load balancer sits in front of it, the application may see that proxy's address for every request. This can make the API's per-IP login throttling apply to all users as one client.
- For a deployment with an upstream proxy, enforce login rate limiting at that trusted edge as well. Do not blindly copy a client-supplied `X-Real-IP` or `X-Forwarded-For` value into the trusted header. A future ECC trusted-proxy configuration must use explicit proxy CIDRs and be tested against spoofed forwarding headers before relying on it.
- Never publish the API port or allow untrusted hosts to reach the API container directly.

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
