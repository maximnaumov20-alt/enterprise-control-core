# Security policy

## Development status

This project is in early development and is not suitable for production use. The current API has no authentication, authorization, tenant isolation, audit logging or production-grade migration system.

Do not expose it to the public internet or store personal, confidential or business-critical information in it.

## Reporting a vulnerability

Until a private security contact is configured, do not publish exploit details in a public issue. Contact the repository owner through the private GitHub contact options.

## Minimum requirements before production

- Authentication with secure password hashing and session/token lifecycle controls
- Authorization checks on every protected operation
- Tenant isolation enforced server-side
- Input validation and safe error handling
- Rate limiting and security headers
- TLS termination and secrets management
- Database migrations, backups and tested restore procedures
- Dependency, container and application security scanning
- Security logging with access controls and retention policy
- Independent security assessment
