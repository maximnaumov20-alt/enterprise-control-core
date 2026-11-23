#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Upload one already-encrypted age archive to an S3-compatible AWS bucket.
# Credentials should come from an IAM role or the standard AWS credential chain.
if [[ $# -ne 1 ]]; then
  echo "Usage: bash scripts/upload-backup-s3.sh <backup.dump.age>" >&2
  exit 2
fi

backup_file="$1"
destination="${ECC_BACKUP_S3_URI:-}"
sse_mode="${ECC_BACKUP_S3_SSE:-AES256}"

if [[ "$backup_file" != *.dump.age || ! -f "$backup_file" || ! -r "$backup_file" || ! -s "$backup_file" ]]; then
  echo "Refusing upload: provide a readable, non-empty .dump.age archive." >&2
  exit 2
fi

if [[ ! "$destination" =~ ^s3://[a-z0-9][a-z0-9.-]{1,61}[a-z0-9](/[^?]*)?$ ]]; then
  echo "ECC_BACKUP_S3_URI must be an s3://bucket/optional-prefix URI." >&2
  exit 2
fi

if [[ "$sse_mode" != "AES256" && "$sse_mode" != "aws:kms" ]]; then
  echo "ECC_BACKUP_S3_SSE must be AES256 or aws:kms." >&2
  exit 2
fi

if ! command -v aws >/dev/null 2>&1 || ! command -v sha256sum >/dev/null 2>&1; then
  echo "AWS CLI and sha256sum are required." >&2
  exit 2
fi

bucket_and_prefix="${destination#s3://}"
bucket="${bucket_and_prefix%%/*}"
prefix=""
if [[ "$bucket_and_prefix" == */* ]]; then
  prefix="${bucket_and_prefix#*/}"
  prefix="${prefix%/}"
fi
object_name="$(basename "$backup_file")"
if [[ -n "$prefix" ]]; then
  object_key="$prefix/$object_name"
else
  object_key="$object_name"
fi

checksum="$(sha256sum "$backup_file" | awk '{print $1}')"
file_size="$(wc -c < "$backup_file" | tr -d '[:space:]')"
if [[ ! "$checksum" =~ ^[a-f0-9]{64}$ || ! "$file_size" =~ ^[0-9]+$ ]]; then
  echo "Could not calculate a valid local archive checksum or size." >&2
  exit 1
fi

aws_args=(--sse "$sse_mode")
if [[ "$sse_mode" == "aws:kms" ]]; then
  kms_key_id="${ECC_BACKUP_S3_KMS_KEY_ID:-}"
  if [[ -z "$kms_key_id" ]]; then
    echo "ECC_BACKUP_S3_KMS_KEY_ID is required when SSE mode is aws:kms." >&2
    exit 2
  fi
  aws_args+=(--sse-kms-key-id "$kms_key_id")
fi

object_uri="s3://$bucket/$object_key"
aws s3 cp "$backup_file" "$object_uri" \
  --only-show-errors \
  --metadata "sha256=$checksum" \
  "${aws_args[@]}"

head_json="$(aws s3api head-object --bucket "$bucket" --key "$object_key")"
read -r remote_checksum remote_size < <(
  python -c 'import json,sys; d=json.load(sys.stdin); print(d.get("Metadata",{}).get("sha256",""), d.get("ContentLength",""))' <<< "$head_json"
)

if [[ "$remote_checksum" != "$checksum" || "$remote_size" != "$file_size" ]]; then
  echo "Remote verification failed: S3 metadata checksum or object size differs." >&2
  exit 1
fi

# Read the object back and compare actual bytes; metadata alone is not proof of integrity.
verification_file="$(mktemp "${TMPDIR:-/tmp}/ecc-s3-verify.XXXXXX")"
chmod 600 "$verification_file"
cleanup() {
  rm -f "$verification_file"
}
trap cleanup EXIT

aws s3 cp "$object_uri" "$verification_file" --only-show-errors
downloaded_checksum="$(sha256sum "$verification_file" | awk '{print $1}')"
downloaded_size="$(wc -c < "$verification_file" | tr -d '[:space:]')"
if [[ "$downloaded_checksum" != "$checksum" || "$downloaded_size" != "$file_size" ]]; then
  echo "Remote read-back verification failed: downloaded archive bytes differ from the local file." >&2
  exit 1
fi

echo "Encrypted backup uploaded and read-back verified: $object_uri"
echo "SHA-256: $checksum"
