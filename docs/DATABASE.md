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

The script checks PostgreSQL readiness, creates a custom-format dump, validates it with `pg_restore --list`, applies owner-only file permissions where supported and retains 14 days by default.

### Encrypt backups with age

Install the `age` utility on the host and generate a recipient/identity pair using an approved secure process. Keep the private identity outside the server and backup destination, ideally in an organization-managed secrets vault. Only the public recipient belongs in the backup job configuration.

```bash
age-keygen -o /secure/location/ecc-backup-identity.txt
age-keygen -y /secure/location/ecc-backup-identity.txt
ECC_BACKUP_AGE_RECIPIENT=age1... bash scripts/backup-postgres.sh
```

The first command creates a private identity; the second prints its public recipient. Replace `age1...` with that output. Do not commit or share the private identity. The script writes a `.dump.age` archive when encryption is enabled. Without `ECC_BACKUP_AGE_RECIPIENT`, it creates an unencrypted `.dump` and prints a warning.

Optional settings:

```bash
ECC_BACKUP_DIR=./backups/postgres ECC_BACKUP_RETENTION_DAYS=30 \
  ECC_BACKUP_AGE_RECIPIENT=age1... bash scripts/backup-postgres.sh
```

Encryption protects archive contents at rest but does not create an off-host copy, protect a compromised host during backup, or guarantee key recovery. Store encrypted archives in a separate failure domain with access controls and retention protections. Test private-key recovery independently.

## Restore

Restore replaces database objects in the configured `ecc` database. Stop application writes first, confirm the Compose project points to the intended environment and preserve a separate backup of the current database.

For an unencrypted archive:

```bash
ECC_RESTORE_CONFIRM=YES bash scripts/restore-postgres.sh ./backups/postgres/ecc-postgres-YYYYMMDDTHHMMSSZ.dump
```

For an encrypted archive:

```bash
ECC_BACKUP_AGE_IDENTITY=/secure/location/ecc-backup-identity.txt \
  ECC_RESTORE_CONFIRM=YES \
  bash scripts/restore-postgres.sh ./backups/postgres/ecc-postgres-YYYYMMDDTHHMMSSZ.dump.age
```

The script requires explicit confirmation, checks the target database name and validates the plaintext PostgreSQL archive before restoring. Decrypted temporary files are created with restrictive permissions and removed on exit. PostgreSQL executes the restore in one transaction; a failed restore is rolled back where PostgreSQL supports transactional execution. Verify application health, migration state and representative records before allowing users back in.

These scripts are an operational starting point, not a certified disaster-recovery system. Off-host replication, point-in-time recovery, automated scheduling, immutable retention, independent key escrow and measured RTO/RPO objectives remain separate operational responsibilities. Test recovery regularly in an isolated environment before production use.
