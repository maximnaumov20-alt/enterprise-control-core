#!/usr/bin/env python3
"""Fail when no recent, published PostgreSQL backup is available."""
from __future__ import annotations

import os
import pathlib
import sys
import time


def main() -> int:
    backup_dir = pathlib.Path(os.environ.get("ECC_BACKUP_DIR", "./backups/postgres"))
    raw_max_age = os.environ.get("ECC_BACKUP_MAX_AGE_HOURS", "26")
    require_encrypted = os.environ.get("ECC_BACKUP_REQUIRE_ENCRYPTED", "0")

    try:
        max_age_hours = int(raw_max_age)
    except ValueError:
        print("ECC_BACKUP_MAX_AGE_HOURS must be a positive integer.", file=sys.stderr)
        return 2
    if max_age_hours < 1:
        print("ECC_BACKUP_MAX_AGE_HOURS must be a positive integer.", file=sys.stderr)
        return 2
    if require_encrypted not in {"0", "1"}:
        print("ECC_BACKUP_REQUIRE_ENCRYPTED must be 0 or 1.", file=sys.stderr)
        return 2
    if not backup_dir.is_dir():
        print(f"Backup directory does not exist: {backup_dir}", file=sys.stderr)
        return 1

    patterns = ("ecc-postgres-*.dump.age",) if require_encrypted == "1" else (
        "ecc-postgres-*.dump.age",
        "ecc-postgres-*.dump",
    )
    candidates: list[pathlib.Path] = []
    for pattern in patterns:
        candidates.extend(
            path for path in backup_dir.glob(pattern)
            if path.is_file() and not path.is_symlink() and path.stat().st_size > 0
        )
    if not candidates:
        requirement = "encrypted " if require_encrypted == "1" else ""
        print(f"No published, non-empty {requirement}PostgreSQL backup found in {backup_dir}.", file=sys.stderr)
        return 1

    newest = max(candidates, key=lambda path: path.stat().st_mtime)
    age_seconds = time.time() - newest.stat().st_mtime
    if age_seconds < -300:
        print(f"Newest backup timestamp is more than five minutes in the future: {newest}", file=sys.stderr)
        return 1
    max_age_seconds = max_age_hours * 3600
    if age_seconds > max_age_seconds:
        age_hours = age_seconds / 3600
        print(
            f"Backup is stale: newest archive is {age_hours:.1f} hours old "
            f"(limit {max_age_hours} hours): {newest}",
            file=sys.stderr,
        )
        return 1

    print(f"Backup freshness OK: {newest} is {max(0, age_seconds) / 3600:.1f} hours old.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
