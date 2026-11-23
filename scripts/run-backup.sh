#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Create an encrypted PostgreSQL backup and upload it to S3.
# Run from the repository root under a host identity with Docker and AWS access.
BACKUP_DIR="${ECC_BACKUP_DIR:-./backups/postgres}"
RECIPIENT="${ECC_BACKUP_AGE_RECIPIENT:-}"
S3_URI="${ECC_BACKUP_S3_URI:-}"

if [[ -z "$RECIPIENT" ]]; then
  echo "ECC_BACKUP_AGE_RECIPIENT is required; refusing to create an unencrypted scheduled backup." >&2
  exit 2
fi
if [[ -z "$S3_URI" ]]; then
  echo "ECC_BACKUP_S3_URI is required." >&2
  exit 2
fi
if ! command -v flock >/dev/null 2>&1; then
  echo "flock is required to prevent overlapping backup jobs." >&2
  exit 2
fi

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
exec 9>"$BACKUP_DIR/.ecc-backup.lock"
if ! flock -n 9; then
  echo "Another ECC backup job is already running; refusing overlap." >&2
  exit 1
fi

# Preserve sub-second precision: find -newermt is strict, so whole-second timestamps can miss a backup created in the same second.
started_at="$(date +%s.%N)"
ECC_BACKUP_DIR="$BACKUP_DIR" ECC_BACKUP_AGE_RECIPIENT="$RECIPIENT" \
  bash scripts/backup-postgres.sh

# Select only an archive produced by this run, never an older retained backup.
backup_file=""
while IFS= read -r candidate; do
  backup_file="$candidate"
done < <(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'ecc-postgres-*.dump.age' -newermt "@$started_at" -print | sort)

if [[ -z "$backup_file" ]]; then
  echo "Backup command completed but no new encrypted archive was found." >&2
  exit 1
fi

ECC_BACKUP_S3_URI="$S3_URI" \
  ECC_BACKUP_S3_SSE="${ECC_BACKUP_S3_SSE:-AES256}" \
  ECC_BACKUP_S3_KMS_KEY_ID="${ECC_BACKUP_S3_KMS_KEY_ID:-}" \
  bash scripts/upload-backup-s3.sh "$backup_file"

echo "ECC backup cycle completed: $backup_file"
