# Database migrations and backups

ECC uses Alembic for versioned database schema changes. The API does not create or alter tables at startup.

## Apply migrations locally

From the repository root:

```bash
cd backend
python -m pip install -r requirements.txt
alembic upgrade head
```

Set `DATABASE_URL` before running Alembic to target PostgreSQL or another supported SQLAlchemy database. If it is not set, local development uses `sqlite:///./data/ecc.db`.

To inspect the current revision:

```bash
alembic current
```

To create a migration after changing SQLAlchemy models:

```bash
alembic revision --autogenerate -m "describe schema change"
```

Review every generated migration before applying it. Autogeneration is a draft, not a safety guarantee. Back up the database before applying schema changes.

## Docker Compose

Copy `.env.example` to `.env`, generate different random values for `POSTGRES_PASSWORD` and `ECC_SECRET_KEY`, and set a unique administrator email and password. Do not commit `.env`.

The `migrate` one-shot service applies pending migrations. The `bootstrap-admin` service creates the initial organization administrator idempotently, and the API waits for both services to complete successfully:

```bash
docker compose up --build
```

The initial administrator credentials are used only by the one-shot bootstrap container. To provision another organization, run the bootstrap command with a new `ECC_ORGANIZATION_NAME` and a new administrator email; do not reuse an email already registered in the system.

## Backups

For PostgreSQL, create a logical backup before upgrades:

```bash
docker compose exec -T db pg_dump -U ecc -d ecc > ecc-backup.sql
```

Restore only into a deliberately selected database after checking the target:

```bash
docker compose exec -T db psql -U ecc -d ecc < ecc-backup.sql
```

These commands are basic operator guidance, not a tested disaster-recovery service. Production deployments still need encrypted off-host backups, retention rules, access controls and regularly tested restore procedures.
