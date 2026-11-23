#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Run from the repository root. Requires Docker Compose and a healthy "db" service.
BACKUP_DIR="${ECC_BACKUP_DIR:-./backups/postgres}"
RETENTION_DAYS="${ECC_BACKUP_RETENTION_DAYS:-14}"
AGE_RECIPIENT="${ECC_BACKUP_AGE_RECIPIENT:-}"

if ! [[ "$RETENTION_DAYS" =~ ^[0-9]+$ ]] || (( RETENTION_DAYS < 1 )); then
  echo "ECC_BACKUP_RETENTION_DAYS must be a positive integer." >&2
  exit 2
fi

if [[ -n "$AGE_RECIPIENT" ]] && ! command -v age >/dev/null 2>&1; then
  echo "age is required when ECC_BACKUP_AGE_RECIPIENT is set." >&2
  exit 2
fi

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
base_file="$BACKUP_DIR/ecc-postgres-$timestamp.dump"
temporary_file="$base_file.partial"
encrypted_file="$base_file.age"
encrypted_temporary="$encrypted_file.partial"

cleanup() {
  rm -f "$temporary_file" "$encrypted_temporary"
}
trap cleanup EXIT

docker compose exec -T db pg_isready -U ecc -d ecc >/dev/null
docker compose exec -T db pg_dump -U ecc -d ecc --format=custom --no-owner --no-acl > "$temporary_file"

if [[ ! -s "$temporary_file" ]]; then
  echo "Backup file is empty; refusing to publish it." >&2
  exit 1
fi

# Verify the PostgreSQL archive before optional encryption.
docker compose exec -T db pg_restore --list < "$temporary_file" >/dev/null

if [[ -n "$AGE_RECIPIENT" ]]; then
  age --encrypt --recipient "$AGE_RECIPIENT" --output "$encrypted_temporary" "$temporary_file"
  if [[ ! -s "$encrypted_temporary" ]]; then
    echo "Encrypted backup is empty; refusing to publish it." >&2
    exit 1
  fi
  mv "$encrypted_temporary" "$encrypted_file"
  chmod 600 "$encrypted_file"
  rm -f "$temporary_file"
  published_file="$encrypted_file"
else
  mv "$temporary_file" "$base_file"
  chmod 600 "$base_file"
  published_file="$base_file"
  echo "WARNING: backup is not encrypted. Set ECC_BACKUP_AGE_RECIPIENT to enable age encryption." >&2
fi

find "$BACKUP_DIR" -maxdepth 1 -type f \( -name 'ecc-postgres-*.dump' -o -name 'ecc-postgres-*.dump.age' \) -mtime "+$RETENTION_DAYS" -delete

echo "Verified backup created: $published_file"
