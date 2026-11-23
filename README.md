# Enterprise Control Center (ECC)

Open-core platform for managing company IT assets, software inventory and maintenance requests.

> **Status: early development.** This repository is a starter foundation, not a production-ready enterprise system. Authentication, authorization, audit integrity, backup/restore procedures and security review are not implemented yet. Do not use it with sensitive or production data.

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

The first commit provides a small asset-inventory API foundation. The other modules will be added incrementally.

## Run locally

Requirements: Python 3.12+.

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

The API will be available at http://127.0.0.1:8000 and interactive documentation at http://127.0.0.1:8000/docs.

For PostgreSQL using Docker:

```bash
docker compose up --build
```

## Project structure

```
backend/
  app/
    main.py
  requirements.txt
docs/
  ROADMAP.md
  SECURITY.md
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
