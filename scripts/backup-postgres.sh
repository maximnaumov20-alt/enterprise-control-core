#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Run from the repository root. Requires Docker Compose and a healthy "db" service.
BACKUP_DIR="${ECC_BACKUP_DIR:-./backups/postgres}"
RETENTION_DAYS="${ECC_BACKUP_RETENTION_DAYS:-14}"

if ! [[ "$RETENTION_DAYS" =~ ^[0-9]+$ ]] || (( RETENTION_DAYS < 1 )); then
  echo "ECC_BACKUP_RETENTION_DAYS must be a positive integer." >&2
  exit 2
fi

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_file="$BACKUP_DIR/ecc-postgres-$timestamp.dump"
temporary_file="$backup_file.partial"

cleanup() {
  rm -f "$temporary_file"
}
trap cleanup EXIT

docker compose exec -T db pg_isready -U ecc -d ecc >/dev/null
docker compose exec -T db pg_dump -U ecc -d ecc --format=custom --no-owner --no-acl > "$temporary_file"

if [[ ! -s "$temporary_file" ]]; then
  echo "Backup file is empty; refusing to publish it." >&2
  exit 1
fi

# Verify the archive can be read by PostgreSQL tooling before keeping it.
docker compose exec -T db pg_restore --list < "$temporary_file" >/dev/null
mv "$temporary_file" "$backup_file"
chmod 600 "$backup_file"

find "$BACKUP_DIR" -maxdepth 1 -type f -name 'ecc-postgres-*.dump' -mtime "+$RETENTION_DAYS" -delete

echo "Verified backup created: $backup_file"
