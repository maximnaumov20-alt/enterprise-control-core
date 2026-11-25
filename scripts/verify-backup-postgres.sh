#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Validate a local PostgreSQL custom archive without modifying the database.
if [[ $# -ne 1 ]]; then
  echo "Usage: bash scripts/verify-backup-postgres.sh <backup.dump[.age]>" >&2
  exit 2
fi

backup_file="$1"
if [[ ! -f "$backup_file" || ! -r "$backup_file" || ! -s "$backup_file" ]]; then
  echo "Backup file must exist, be readable, and be non-empty." >&2
  exit 2
fi

case "$backup_file" in
  *.dump)
    docker compose exec -T db pg_restore --list < "$backup_file" >/dev/null
    ;;
  *.dump.age)
    identity="${ECC_BACKUP_AGE_IDENTITY:-}"
    if [[ -z "$identity" || ! -f "$identity" || ! -r "$identity" ]]; then
      echo "Encrypted backup requires readable ECC_BACKUP_AGE_IDENTITY." >&2
      exit 2
    fi
    if ! command -v age >/dev/null 2>&1; then
      echo "age is required to verify this encrypted backup." >&2
      exit 2
    fi
    # Stream plaintext directly into pg_restore; never persist decrypted data.
    age --decrypt --identity "$identity" "$backup_file" |
      docker compose exec -T db pg_restore --list >/dev/null
    ;;
  *)
    echo "Backup file must end in .dump or .dump.age." >&2
    exit 2
    ;;
esac

echo "PostgreSQL backup archive is valid: $backup_file"
