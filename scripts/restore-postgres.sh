#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Destructive operation: explicitly require ECC_RESTORE_CONFIRM=YES.
if [[ "${ECC_RESTORE_CONFIRM:-}" != "YES" ]]; then
  echo "Refusing restore. Set ECC_RESTORE_CONFIRM=YES after verifying the target database." >&2
  exit 2
fi

if [[ $# -ne 1 ]]; then
  echo "Usage: ECC_RESTORE_CONFIRM=YES bash scripts/restore-postgres.sh <backup.dump>" >&2
  exit 2
fi

backup_file="$1"
if [[ ! -f "$backup_file" || ! -r "$backup_file" || ! -s "$backup_file" ]]; then
  echo "Backup file must exist, be readable, and be non-empty." >&2
  exit 2
fi

docker compose exec -T db pg_isready -U ecc -d ecc >/dev/null
target_database="$(docker compose exec -T db psql -U ecc -d ecc -Atqc 'SELECT current_database()')"
if [[ "$target_database" != "ecc" ]]; then
  echo "Refusing restore: expected database 'ecc', got '$target_database'." >&2
  exit 1
fi

# Validate archive metadata before changing the target database.
docker compose exec -T db pg_restore --list < "$backup_file" >/dev/null

echo "Restoring into database '$target_database'. Existing objects may be replaced."
docker compose exec -T db pg_restore \
  --clean --if-exists --no-owner --no-acl --exit-on-error --single-transaction \
  -U ecc -d ecc < "$backup_file"

echo "Restore command completed. Verify application health and data before resuming use."
