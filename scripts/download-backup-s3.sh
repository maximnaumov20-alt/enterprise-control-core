#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Download one encrypted PostgreSQL archive and verify its metadata and bytes.
if [[ $# -ne 2 ]]; then
  echo "Usage: bash scripts/download-backup-s3.sh <s3://bucket/key.dump.age> <output.dump.age>" >&2
  exit 2
fi

object_uri="$1"
output_file="$2"

if [[ ! "$object_uri" =~ ^s3://[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]/[^?]+\.dump\.age$ ]]; then
  echo "Refusing download: provide an s3://bucket/key.dump.age URI." >&2
  exit 2
fi
if [[ "$output_file" != *.dump.age ]]; then
  echo "Output file must use the .dump.age suffix." >&2
  exit 2
fi
if [[ -e "$output_file" ]]; then
  echo "Refusing download: output file already exists." >&2
  exit 2
fi
if ! command -v aws >/dev/null 2>&1 || ! command -v sha256sum >/dev/null 2>&1; then
  echo "AWS CLI and sha256sum are required." >&2
  exit 2
fi

bucket_and_key="${object_uri#s3://}"
bucket="${bucket_and_key%%/*}"
object_key="${bucket_and_key#*/}"
head_json="$(aws s3api head-object --bucket "$bucket" --key "$object_key")"
read -r expected_checksum expected_size < <(
  python -c 'import json,sys; d=json.load(sys.stdin); print(d.get("Metadata",{}).get("sha256",""), d.get("ContentLength",""))' <<< "$head_json"
)
if [[ ! "$expected_checksum" =~ ^[a-f0-9]{64}$ || ! "$expected_size" =~ ^[0-9]+$ ]] || (( expected_size < 1 )); then
  echo "Remote object is missing valid SHA-256 metadata or content length." >&2
  exit 1
fi

output_dir="$(dirname "$output_file")"
mkdir -p "$output_dir"
temporary_file="$(mktemp "$output_dir/.ecc-download.XXXXXX")"
chmod 600 "$temporary_file"
cleanup() {
  rm -f "$temporary_file"
}
trap cleanup EXIT

aws s3 cp "$object_uri" "$temporary_file" --only-show-errors
actual_checksum="$(sha256sum "$temporary_file" | awk '{print $1}')"
actual_size="$(wc -c < "$temporary_file" | tr -d '[:space:]')"
if [[ "$actual_checksum" != "$expected_checksum" || "$actual_size" != "$expected_size" ]]; then
  echo "Downloaded backup verification failed: checksum or size differs from S3 metadata." >&2
  exit 1
fi

if [[ -e "$output_file" ]]; then
  echo "Refusing to replace an output file created during download." >&2
  exit 2
fi
mv "$temporary_file" "$output_file"
chmod 600 "$output_file"
echo "Encrypted backup downloaded and verified: $output_file"
echo "SHA-256: $actual_checksum"
