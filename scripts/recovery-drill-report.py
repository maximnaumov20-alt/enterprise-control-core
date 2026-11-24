#!/usr/bin/env python3
"""Create a timestamp-based, auditable recovery-drill report."""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import tempfile
from datetime import datetime, timezone


def parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid ISO-8601 timestamp: {value}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamps must include a timezone offset")
    return parsed.astimezone(timezone.utc)


def positive_seconds(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("target must be a positive integer number of seconds") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("target must be a positive integer number of seconds")
    return parsed


def build_report(args: argparse.Namespace) -> dict:
    incident = args.incident_at
    completed = args.restore_completed_at
    started = args.restore_started_at
    committed = args.last_committed_at

    if completed < incident:
        raise ValueError("restore completion cannot precede incident time")
    if started is not None and not incident <= started <= completed:
        raise ValueError("restore start must be between incident and restore completion")
    if committed is not None and committed > incident:
        raise ValueError("last committed transaction cannot be after incident time")
    if args.rpo_target_seconds is not None and committed is None:
        raise ValueError("--rpo-target-seconds requires --last-committed-at")

    rto = (completed - incident).total_seconds()
    execution = (completed - started).total_seconds() if started is not None else None
    rpo = (incident - committed).total_seconds() if committed is not None else None

    rto_met = rto <= args.rto_target_seconds if args.rto_target_seconds is not None else None
    rpo_met = rpo <= args.rpo_target_seconds if args.rpo_target_seconds is not None else None
    assessed = [value for value in (rto_met, rpo_met) if value is not None]
    if not assessed:
        assessment = "not_assessed"
    elif not all(assessed):
        assessment = "target_exceeded"
    else:
        assessment = "within_targets"

    return {
        "schema_version": 1,
        "report_type": "ecc_recovery_drill",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "backup_reference": args.backup_reference,
        "timestamps": {
            "incident_at": incident.isoformat(),
            "restore_started_at": started.isoformat() if started else None,
            "restore_completed_at": completed.isoformat(),
            "last_committed_at": committed.isoformat() if committed else None,
        },
        "measurements_seconds": {
            "observed_rto": rto,
            "restore_execution": execution,
            "observed_rpo": rpo,
        },
        "targets_seconds": {
            "rto": args.rto_target_seconds,
            "rpo": args.rpo_target_seconds,
        },
        "target_met": {"rto": rto_met, "rpo": rpo_met},
        "assessment": assessment,
        "measurement_notes": {
            "rto": "Incident declaration to verified restore completion.",
            "rpo": (
                "Incident time minus last known committed transaction time; "
                "requires a trustworthy transaction timestamp."
            ),
            "missing_rpo": (
                "RPO was not measured. Backup age alone is not proof of data loss."
                if committed is None else None
            ),
        },
    }


def write_report(path: pathlib.Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing report: {path}")
    fd, temporary_name = tempfile.mkstemp(prefix=".ecc-recovery-report-", dir=path.parent)
    temporary = pathlib.Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        # A hard link publishes atomically and fails if another process created the path.
        os.link(temporary, path)
        temporary.unlink()
        os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-reference", required=True)
    parser.add_argument("--incident-at", required=True, type=parse_timestamp)
    parser.add_argument("--restore-started-at", type=parse_timestamp)
    parser.add_argument("--restore-completed-at", required=True, type=parse_timestamp)
    parser.add_argument("--last-committed-at", type=parse_timestamp)
    parser.add_argument("--rto-target-seconds", type=positive_seconds)
    parser.add_argument("--rpo-target-seconds", type=positive_seconds)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()

    if not args.backup_reference.strip() or any(ord(char) < 32 for char in args.backup_reference):
        parser.error("--backup-reference must be non-empty and contain no control characters")
    try:
        report = build_report(args)
        write_report(args.output, report)
    except (ValueError, FileExistsError, OSError) as exc:
        parser.error(str(exc))
    print(f"Recovery drill report written: {args.output}")
    print(f"Assessment: {report['assessment']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
