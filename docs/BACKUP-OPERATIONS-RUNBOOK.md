# ECC backup operations runbook

This runbook covers routine checks and first response to backup failures. It does not certify a deployment as production-ready.

## Daily verification

1. Check the scheduled job and its most recent execution:
   ```sh
   systemctl list-timers ecc-backup.timer
   systemctl status ecc-backup.service
   journalctl -u ecc-backup.service --since today --no-pager
   ```
2. Check local backup freshness:
   ```sh
   ECC_BACKUP_DIR=/opt/enterprise-control-core/backups/postgres \
   ECC_BACKUP_MAX_AGE_HOURS=26 ECC_BACKUP_REQUIRE_ENCRYPTED=1 \
   python3 scripts/check-backup-freshness.py
   ```
3. Confirm the most recent S3 upload completed and its checksum/read-back verification passed in the service logs.
4. Periodically verify an encrypted archive using a separately protected age identity:
   ```sh
   ECC_BACKUP_AGE_IDENTITY=/secure/recovery/age-identity.txt \
   bash scripts/verify-backup-postgres.sh /path/to/backup.dump.age
   ```

## Failed or stale backup

1. Do not delete the last known-good local or remote archive.
2. Inspect `journalctl -u ecc-backup.service` and identify whether the failure occurred during PostgreSQL dump, archive validation, age encryption, S3 upload, or read-back verification.
3. Check free disk space, Docker Compose database health, age recipient configuration, AWS credential availability, bucket permissions, and network connectivity. Do not print or paste secrets into logs or tickets.
4. Correct the underlying issue and run a supervised job:
   ```sh
   sudo systemctl start ecc-backup.service
   sudo systemctl status ecc-backup.service
   ```
5. Re-run the freshness check and verify the newly created archive. Confirm the S3 object is present and verified before closing the incident.
6. If the backup is beyond the agreed recovery-point objective, record the duration and notify the service owner.

## Suspected corruption or failed restore

- Preserve the original archive and its checksum; work from a separate copy.
- Verify the archive before attempting restoration.
- Never test a restore against the production database. Use a disposable, isolated environment with network access restricted.
- Follow `scripts/restore-postgres.sh` confirmation requirements and verify application records and audit history after the drill.
- Record restore start/end times, archive identifier, verification result, errors, and measured recovery time. Do not include credentials or sensitive customer data in the report.

## Recovery objectives and escalation

Each deployment owner must set explicit RPO and RTO values, retention periods, escalation contacts, and data-classification requirements before onboarding real business data. The default 26-hour freshness threshold is a technical default, not a business-approved RPO.

Escalate immediately when:
- no valid encrypted backup exists within the agreed RPO;
- S3 checksum/read-back verification fails repeatedly;
- the recovery identity is unavailable;
- an isolated restore fails or recovered records do not match expected evidence;
- backup credentials, encryption identities, or archives may have been exposed.

## Evidence to retain

Keep a restricted incident record containing timestamps, job result, archive name, size, checksum, remote object identifier, verifier result, restore drill result, and corrective action. Store evidence separately from the backup credentials and never store private age identities in the repository.
