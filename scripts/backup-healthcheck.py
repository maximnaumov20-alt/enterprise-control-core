#!/usr/bin/env python3
"""Machine-readable health status for ECC PostgreSQL backup monitoring."""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time


def inspect_backup() -> dict[str, object]:
    directory = pathlib.Path(os.environ.get("ECC_BACKUP_DIR", "./backups/postgres"))
    raw_age = os.environ.get("ECC_BACKUP_MAX_AGE_HOURS", "26")
    require_encrypted = os.environ.get("ECC_BACKUP_REQUIRE_ENCRYPTED", "1")
    try:
        max_age_hours = int(raw_age)
    except ValueError:
        return {"status": "configuration_error", "reason": "ECC_BACKUP_MAX_AGE_HOURS must be a positive integer"}
    if max_age_hours < 1 or require_encrypted not in {"0", "1"}:
        return {"status": "configuration_error", "reason": "Invalid backup freshness configuration"}
    if not directory.is_dir():
        return {"status": "critical", "reason": "Backup directory does not exist", "backup_dir": str(directory)}

    patterns = ("ecc-postgres-*.dump.age",) if require_encrypted == "1" else (
        "ecc-postgres-*.dump.age", "ecc-postgres-*.dump"
    )
    files: list[pathlib.Path] = []
    for pattern in patterns:
        for path in directory.glob(pattern):
            try:
                if path.is_file() and not path.is_symlink() and path.stat().st_size > 0:
                    files.append(path)
            except OSError:
                continue
    if not files:
        return {
            "status": "critical",
            "reason": "No published non-empty backup found",
            "backup_dir": str(directory),
            "encrypted_required": require_encrypted == "1",
        }

    newest = max(files, key=lambda item: item.stat().st_mtime)
    stat = newest.stat()
    age_seconds = time.time() - stat.st_mtime
    if age_seconds < -300:
        return {"status": "critical", "reason": "Newest backup timestamp is more than five minutes in the future",
                "backup_file": newest.name, "backup_mtime": int(stat.st_mtime)}
    age_hours = max(0.0, age_seconds / 3600)
    status = "ok" if age_seconds <= max_age_hours * 3600 else "critical"
    result: dict[str, object] = {
        "status": status,
        "backup_file": newest.name,
        "backup_mtime": int(stat.st_mtime),
        "age_hours": round(age_hours, 2),
        "max_age_hours": max_age_hours,
        "size_bytes": stat.st_size,
        "encrypted": newest.name.endswith(".dump.age"),
        "backup_dir": str(directory),
    }
    if status != "ok":
        result["reason"] = "Newest backup exceeds configured maximum age"
    return result


def main() -> int:
    result = inspect_backup()
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "ok" else 2 if result["status"] == "configuration_error" else 1


if __name__ == "__main__":
    raise SystemExit(main())
