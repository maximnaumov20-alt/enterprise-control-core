#!/usr/bin/env python3
"""Download and verify recovery-drill evidence from an S3 run prefix."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import subprocess
import tempfile
from urllib.parse import urlsplit


def parse_source(value: str) -> tuple[str, str]:
    parsed = urlsplit(value)
    if parsed.scheme != "s3" or not parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("source must be an S3 URI without query or fragment")
    if parsed.username or parsed.password or parsed.port:
        raise ValueError("source must not contain credentials or a port")
    segments = [part for part in parsed.path.strip("/").split("/") if part]
    if not segments or any(part in {".", ".."} for part in segments):
        raise ValueError("source must identify a bucket prefix without dot segments")
    return parsed.netloc, "/".join(segments)


def run_aws(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["aws", *arguments], check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def sha256_file(path: pathlib.Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def download_file(bucket: str, prefix: str, name: str, output_dir: pathlib.Path) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", name):
        raise ValueError(f"unsafe evidence filename: {name}")
    if output_dir.is_symlink() or not output_dir.is_dir():
        raise ValueError("output directory must be an existing regular directory")
    target = output_dir / name
    if target.exists() or target.is_symlink():
        raise ValueError(f"refusing to overwrite existing output: {target}")

    key = f"{prefix}/{name}"
    uri = f"s3://{bucket}/{key}"
    head = run_aws("s3api", "head-object", "--bucket", bucket, "--key", key, "--output", "json")
    remote = json.loads(head.stdout)
    metadata = remote.get("Metadata")
    expected_hash = metadata.get("sha256") if isinstance(metadata, dict) else None
    expected_size = remote.get("ContentLength")
    if not isinstance(expected_hash, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", expected_hash):
        raise ValueError(f"S3 object has missing or invalid SHA-256 metadata: {uri}")
    if isinstance(expected_size, bool) or not isinstance(expected_size, int) or expected_size <= 0:
        raise ValueError(f"S3 object has invalid content length: {uri}")

    fd, temporary_name = tempfile.mkstemp(prefix=".ecc-recovery-download-", dir=output_dir)
    os.close(fd)
    temporary = pathlib.Path(temporary_name)
    try:
        run_aws("s3", "cp", uri, str(temporary), "--only-show-errors")
        actual_hash, actual_size = sha256_file(temporary)
        if actual_hash != expected_hash.lower() or actual_size != expected_size:
            raise ValueError(f"S3 downloaded content does not match recorded checksum/size: {uri}")
        # Hard-link publication is atomic and fails rather than replacing an existing file.
        os.link(temporary, target)
        print(f"Verified download: {uri} -> {target} sha256={actual_hash} bytes={actual_size}")
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="S3 run prefix, e.g. s3://bucket/ecc/recovery/123/1")
    parser.add_argument("--output-dir", required=True, type=pathlib.Path)
    parser.add_argument("--file", action="append", required=True, help="exact evidence filename; repeat as needed")
    args = parser.parse_args()
    try:
        bucket, prefix = parse_source(args.source)
        if len(args.file) != len(set(args.file)):
            raise ValueError("evidence filenames must be unique")
        args.output_dir.mkdir(parents=True, exist_ok=True)
        if args.output_dir.is_symlink():
            raise ValueError("output directory must not be a symlink")
        for name in args.file:
            download_file(bucket, prefix, name, args.output_dir)
    except (OSError, json.JSONDecodeError, subprocess.CalledProcessError, ValueError) as exc:
        detail = getattr(exc, "stderr", None)
        parser.error(f"{exc}{': ' + detail.strip() if detail else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
