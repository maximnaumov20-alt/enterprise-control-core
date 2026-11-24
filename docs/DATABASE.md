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

## Off-host upload to Amazon S3

The optional uploader sends an already-encrypted `.dump.age` archive to an S3 bucket and verifies the uploaded object's recorded SHA-256 metadata and size, then downloads the object and compares the actual bytes with the local archive. It refuses plaintext dumps.

Install AWS CLI v2 and configure credentials through an instance/task role or the standard AWS credential chain. Do not put access keys in the repository, script arguments, or committed environment files. The identity used by the job needs `s3:PutObject`, `s3:HeadObject` and `s3:GetObject` permissions scoped to the backup prefix: upload uses the first permission, metadata verification uses the second, and the mandatory read-back integrity check uses the third. If SSE-KMS is enabled, grant only the required KMS permissions for the selected key, including `kms:GenerateDataKey` for upload and `kms:Decrypt` for read-back/download, subject to the key policy and AWS configuration.

Example:

```bash
ECC_BACKUP_S3_URI=s3://company-ecc-backups/production \
  bash scripts/upload-backup-s3.sh ./backups/postgres/ecc-postgres-YYYYMMDDTHHMMSSZ.dump.age
```

The uploader uses S3-managed encryption (SSE-S3) by default. To use a customer-managed KMS key, set `ECC_BACKUP_S3_SSE=aws:kms` and `ECC_BACKUP_S3_KMS_KEY_ID`; grant the upload identity the minimum KMS permissions needed for that key. The archive remains age-encrypted independently of S3 server-side encryption.

Configure bucket versioning, lifecycle retention, public-access blocking, least-privilege policies and (where supported/required) Object Lock in the cloud account. Those bucket controls are infrastructure configuration and are not created by this script. Keep the age private identity separate from both the application host and the bucket. Periodically test downloading an object and restoring it in an isolated environment.

The upload script performs a full read-back and compares SHA-256 and byte size with the local archive. This confirms the bytes returned by the S3 endpoint at upload time, but it is not a restore drill or a guarantee of long-term durability. Periodic download, decryption and isolated database restore tests remain necessary.

Use the verified downloader to retrieve one encrypted object without overwriting an existing file:

```bash
bash scripts/download-backup-s3.sh \
  s3://company-ecc-backups/production/ecc-postgres-YYYYMMDDTHHMMSSZ.dump.age \
  ./recovery/ecc-postgres-YYYYMMDDTHHMMSSZ.dump.age
```

The downloader validates the object URI, requires SHA-256 metadata and a positive content length, downloads to a private temporary file, compares the actual bytes with the remote metadata, and only then publishes the output file. It does not decrypt or restore the database; use the restore procedure below after verifying the target environment.

The CI restore drill uses a fake AWS CLI endpoint to exercise the download client logic, then decrypts the downloaded archive and restores it into a separate Docker Compose project with an independent PostgreSQL volume. It verifies both a restored asset and its audit event. This validates the end-to-end client-side recovery path without AWS credentials or access to the primary CI database; it does not test AWS availability, IAM policy behavior or long-term object durability.

The CI suite also exercises the uploader offline with a fake AWS CLI: it checks successful upload/read-back, rejects metadata checksum and size mismatches plus corrupted downloaded bytes, refuses plaintext archives, and validates SSE-KMS argument handling. This tests client-side behavior only; it does not contact AWS or prove provider-side durability.

## Automated backup cycle

For a host that already runs the ECC Docker Compose stack, `scripts/run-backup.sh` performs one complete cycle: it requires age encryption and an S3 destination, creates and validates a fresh PostgreSQL archive, then uploads that exact new archive. It uses a non-blocking `flock` lock to prevent overlapping jobs and refuses to fall back to plaintext or silently upload an older retained archive.

Example manual run:

```bash
ECC_BACKUP_AGE_RECIPIENT=age1... \
ECC_BACKUP_S3_URI=s3://company-ecc-backups/production \
  bash scripts/run-backup.sh
```

Schedule this command with the host's systemd timer or cron only after configuring a protected environment file outside the repository. The job identity needs Docker access, the age public recipient, and narrowly scoped AWS credentials (prefer an instance/task role). Keep the age private identity off the application host. Send stdout/stderr to the host's centralized logs and alert on non-zero exit status and missing expected runs. Do not place AWS access keys or the age private identity in the repository or timer unit.

The repository does not enable a schedule on a real server: deployment host, service account, secrets storage, monitoring destination and backup window must be configured by the operator. A successful run confirms archive creation and S3 metadata/size checks only; it does not replace periodic download, decrypt and isolated restore drills.

## Recovery-drill measurements (RPO/RTO)

The report generator records measured recovery timestamps and evaluates only targets explicitly supplied by the operator. It does not schedule drills or independently verify that a service is healthy; timestamps must come from trusted monitoring or the recovery procedure.

Example:

```bash
python scripts/recovery-drill-report.py \
  --backup-reference s3://company-ecc-backups/production/ecc-postgres-YYYYMMDDTHHMMSSZ.dump.age \
  --incident-at 2026-11-24T10:00:00Z \
  --restore-started-at 2026-11-24T10:05:00Z \
  --restore-completed-at 2026-11-24T10:25:00Z \
  --last-committed-at 2026-11-24T09:59:00Z \
  --rto-target-seconds 1800 \
  --rpo-target-seconds 120 \
  --output ./recovery-reports/drill-2026-11-24.json
```

RTO is measured from incident declaration to verified restore completion. RPO is measured only when the operator supplies a trustworthy timestamp for the last committed transaction before the incident. If that timestamp is unavailable, the report leaves RPO null and explicitly marks it as not measured; backup age is not silently substituted for data-loss measurement. Reports are written with owner-only permissions and existing reports are never overwritten.

The report is an evidence record, not a compliance certification. It does not prove the accuracy of supplied timestamps, continuous service availability, backup integrity, or successful application-level validation. Store reports in an access-controlled location and retain the logs and test evidence used to produce them.

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
