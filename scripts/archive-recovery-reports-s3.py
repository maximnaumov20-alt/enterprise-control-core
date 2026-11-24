#!/usr/bin/env python3
"""Archive recovery-drill evidence to S3 and verify every object by read-back."""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import tempfile
from urllib.parse import urlsplit


def parse_destination(value: str) -> tuple[str, str]:
    parsed = urlsplit(value)
    if parsed.scheme != "s3" or not parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("destination must be an S3 URI without query or fragment")
    if parsed.username or parsed.password or parsed.port:
        raise ValueError("destination must not contain credentials or a port")
    segments = [part for part in parsed.path.strip("/").split("/") if part]
    if any(part in {".", ".."} for part in segments):
        raise ValueError("destination prefix must not contain dot segments")
    return parsed.netloc, "/".join(segments)


def sha256_file(path: pathlib.Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def run_aws(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["aws", *arguments],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def archive_file(path: pathlib.Path, bucket: str, key: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"archive input must be a regular, non-symlink file: {path}")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", path.name):
        raise ValueError(f"unsafe archive filename: {path.name}")
    expected_hash, expected_size = sha256_file(path)
    uri = f"s3://{bucket}/{key}"

    run_aws(
        "s3", "cp", str(path), uri,
        "--only-show-errors",
        "--metadata", f"sha256={expected_hash}",
    )
    head = run_aws("s3api", "head-object", "--bucket", bucket, "--key", key, "--output", "json")
    remote = json.loads(head.stdout)
    metadata = remote.get("Metadata")
    if not isinstance(metadata, dict) or metadata.get("sha256") != expected_hash:
        raise ValueError(f"S3 metadata checksum mismatch: {uri}")
    if remote.get("ContentLength") != expected_size:
        raise ValueError(f"S3 object size mismatch: {uri}")

    with tempfile.TemporaryDirectory(prefix="ecc-recovery-archive-") as directory:
        downloaded = pathlib.Path(directory) / path.name
        run_aws("s3", "cp", uri, str(downloaded), "--only-show-errors")
        actual_hash, actual_size = sha256_file(downloaded)
    if actual_hash != expected_hash or actual_size != expected_size:
        raise ValueError(f"S3 read-back content mismatch: {uri}")
    print(f"Verified archive: {uri} sha256={expected_hash} bytes={expected_size}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", required=True, help="S3 bucket/prefix URI")
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--run-attempt", required=True, type=int)
    parser.add_argument("--file", action="append", required=True, type=pathlib.Path,
                        help="evidence file to archive; repeat for each file")
    args = parser.parse_args()

    try:
        if args.run_id <= 0 or args.run_attempt <= 0:
            raise ValueError("run-id and run-attempt must be positive integers")
        bucket, prefix = parse_destination(args.destination)
        names = [path.name for path in args.file]
        if len(names) != len(set(names)):
            raise ValueError("archive input filenames must be unique")
        run_prefix = "/".join(part for part in (prefix, str(args.run_id), str(args.run_attempt)) if part)
        for path in args.file:
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"archive input must be a regular, non-symlink file: {path}")
            key = f"{run_prefix}/{path.name}"
            archive_file(path, bucket, key)
    except (OSError, json.JSONDecodeError, subprocess.CalledProcessError, ValueError) as exc:
        detail = getattr(exc, "stderr", None)
        parser.error(f"{exc}{': ' + detail.strip() if detail else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
