# Security policy

## Development status

ECC is an early-development project. Authentication, role checks, organization-scoped data access and basic audit events are implemented, but this is not a production security certification. Do not expose it to the public internet or store real sensitive or business-critical information until the remaining controls and an independent security review are complete.

## Implemented controls

- Passwords are hashed with Argon2 through pwdlib; plaintext passwords are never stored.
- API access uses short-lived signed bearer tokens. The signing key is supplied through `ECC_SECRET_KEY`; the API refuses authentication when it is missing. Keys shorter than 32 bytes are rejected.
- The API container runs as a non-root user, has a health check and is not published directly to the host in the Compose deployment; browser traffic reaches it through the Nginx gateway.
- The production frontend is served by Nginx with a same-origin API reverse proxy and baseline content-type, frame, referrer and content-security-policy headers.
- Roles are `admin`, `editor` and `viewer`. The API enforces permissions server-side.
- Asset, software licence and maintenance request access is scoped to the authenticated user's organization. Maintenance records use composite foreign keys to prevent cross-organization asset and user references. Viewers can access only the maintenance requests they created. Organization IDs are never accepted from client request bodies.
- Software licence records store external contract references, not activation keys or other secrets. Employee directory access is restricted to administrators and editors.
- Asset inventory identifiers are unique within an organization rather than globally.
- Basic audit events are recorded for asset and user changes and are visible only to organization administrators.
- Users can change their password after authenticating; new passwords must be at least 12 characters.
- Initial administrator creation is a deployment/bootstrap operation, not a public registration endpoint.

## Important remaining limitations

- Access tokens currently have no refresh-token or server-side revocation list. Disabling an account blocks its next request, but a stolen token remains usable until expiry.
- Login throttling, account lockout, MFA, password reset and email verification are not implemented.
- Audit events are application-level records, not tamper-proof or cryptographically chained. Database administrators can alter them.
- Organization provisioning is currently performed by the bootstrap command. There is no self-service tenant signup.
- There is no independent penetration test, formal threat model, production backup/restore certification or compliance assessment.
- Deployment still requires HTTPS at the public edge, secure secret storage, restrictive network rules, monitoring, dependency scanning and operational procedures.

## Reporting a vulnerability

A private vulnerability reporting channel has not yet been configured. Do not publish exploit details in a public issue. Before inviting external users or contributors, the repository owner must enable GitHub private vulnerability reporting or publish a dedicated security contact.

## Minimum requirements before production

- Independent security review and penetration testing
- HTTPS at the deployment boundary and strict network access controls
- Rate limiting and abuse protection for authentication
- MFA and secure account recovery
- Refresh-token rotation or another revocation strategy
- Tamper-evident audit retention and restricted database administration
- Encrypted off-host backups and regularly tested restores
- Dependency, container and application security scanning
- Documented incident response, monitoring and retention policies
