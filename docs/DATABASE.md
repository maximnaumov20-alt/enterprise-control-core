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

## Logical backups

The repository provides a backup script for the PostgreSQL Compose service. Run it from the repository root:

```bash
bash scripts/backup-postgres.sh
```

The script:
- checks that PostgreSQL is accepting connections;
- creates a PostgreSQL custom-format dump;
- verifies the archive with `pg_restore --list` before publishing it;
- writes files with owner-only permissions where supported;
- retains the most recent 14 days by default.

Optional settings:

```bash
ECC_BACKUP_DIR=./backups/postgres ECC_BACKUP_RETENTION_DAYS=30 bash scripts/backup-postgres.sh
```

Backups are stored locally and are **not encrypted or copied off-host by this script**. Treat them as sensitive database exports. Restrict host access, encrypt them using an approved storage or backup system, and maintain a separate off-host copy. Do not commit backup files to Git.

## Restore

Restore replaces database objects in the configured `ecc` database. Stop application writes first and confirm that the Compose project points to the intended environment. Keep a separate backup of the current database before proceeding.

```bash
ECC_RESTORE_CONFIRM=YES bash scripts/restore-postgres.sh ./backups/postgres/ecc-postgres-YYYYMMDDTHHMMSSZ.dump
```

The script refuses to run without the explicit confirmation variable, checks the target database name and validates the archive before restoring. PostgreSQL executes the restore in one transaction; a failed restore is rolled back where PostgreSQL supports transactional execution. Afterward, verify application health, migration state and representative records before allowing users back in.

These scripts are an operational starting point, not a certified disaster-recovery system. They do not provide encryption, off-site replication, point-in-time recovery, automated scheduling, cloud retention locking or a tested recovery-time/recovery-point guarantee. Test recovery regularly in an isolated environment before production use.
