# Enterprise Control Center (ECC)

Open-core platform for managing company IT assets, software inventory and maintenance requests.

> **Status: early development.** This repository is a starter foundation, not a production-ready enterprise system. Authentication, role-based permissions, organization-scoped asset access and basic audit events are implemented. Production hardening, independent security review, MFA, rate limiting and tamper-evident audit storage remain outstanding. Do not use it with sensitive or production data.

## Community edition

The initial development scope is:
- IT asset inventory
- Employee and department directory
- Software and licence register
- Maintenance requests
- Audit history and reporting

## Architecture

- **Backend:** Python 3.12, FastAPI, SQLAlchemy
- **Database:** PostgreSQL (Docker Compose); SQLite can be used for local development
- **API:** REST, documented by OpenAPI at `/docs`
- **Deployment:** Docker

The current development build includes a tenant-scoped asset inventory API, bearer-token authentication, admin/editor/viewer roles, organization user administration, basic audit history and a React interface with sign-in, password change and role-aware controls. Employee directory, software/licence inventory and maintenance workflows will be added incrementally.

## Run locally

Requirements: Python 3.12+.

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head

# macOS/Linux: generate a signing key and set the initial admin credentials.
export ECC_SECRET_KEY="$(openssl rand -hex 32)"
export ECC_ADMIN_EMAIL="admin@example.com"
export ECC_ADMIN_PASSWORD="replace-with-a-unique-password-of-at-least-12-characters"
export ECC_ORGANIZATION_NAME="Example Organization"
python -m app.create_admin

uvicorn app.main:app --reload
```

On Windows PowerShell, set the same variables with `$env:NAME="value"` before running `python -m app.create_admin` and Uvicorn. Keep the signing key and administrator password private. The administrator bootstrap command is safe to rerun for an existing email; it does not reset the password.

The API will be available at http://127.0.0.1:8000 and interactive documentation at http://127.0.0.1:8000/docs.

For the full local development stack (PostgreSQL, API and web interface):

```bash
cp .env.example .env
# Replace all placeholders with unique secrets and administrator credentials.
docker compose up --build
```

The API requires a bearer token. Docker Compose applies migrations and creates the first organization administrator before starting the API. See [Access control and organization administration](docs/ACCESS_CONTROL.md), [Security policy](docs/SECURITY.md) and [Database migrations and backups](docs/DATABASE.md) for details.

Open the web interface at http://localhost:5173. The API is available at http://localhost:8000 and its interactive documentation at http://localhost:8000/docs.

Database schema changes are managed with Alembic. See [Database migrations and backups](docs/DATABASE.md) for migration, backup and restore instructions.

## Project structure

```
backend/
  app/
    main.py
    create_admin.py
  migrations/
    versions/
  alembic.ini
  requirements.txt
frontend/
  src/
    App.tsx
    api.ts
    style.css
  Dockerfile
  package.json
docs/
  ROADMAP.md
  SECURITY.md
  DATABASE.md
  ACCESS_CONTROL.md
docker-compose.yml
Dockerfile

```

## Development principles

1. Security and tenant isolation must be designed before multi-company features are introduced.
2. Every feature must include tests and migration considerations.
3. Secrets must never be committed to the repository.
4. Community and commercial components must have explicit, reviewed boundaries.
5. Production readiness requires independent security review and operational testing.

## Licensing

No open-source licence has been selected yet. Until a licence is added, standard copyright applies; public visibility alone does not grant permission to reuse, modify or redistribute this code. The licensing model must be decided before accepting outside contributions or advertising the project as open source.

## Contributing

Contribution rules will be published after the licence and governance model are selected. Please open an issue to discuss a proposed feature before submitting substantial work.
