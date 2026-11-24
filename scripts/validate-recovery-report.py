#!/usr/bin/env python3
"""Validate the structure and internal consistency of an ECC recovery report."""
from __future__ import annotations

import argparse
import json
import math
import pathlib
from datetime import datetime


def timestamp(value: object, field: str, *, nullable: bool = False) -> datetime | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a timezone-aware ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} is not a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone offset")
    return parsed


def nonnegative_number(value: object, field: str, *, nullable: bool = False) -> float | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a non-negative number")
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{field} must be a finite non-negative number")
    return float(value)


def validate_report(report: object) -> None:
    if not isinstance(report, dict):
        raise ValueError("report root must be a JSON object")
    if report.get("schema_version") != 1 or report.get("report_type") != "ecc_recovery_drill":
        raise ValueError("unsupported recovery report schema or report_type")
    if not isinstance(report.get("backup_reference"), str) or not report["backup_reference"].strip():
        raise ValueError("backup_reference must be a non-empty string")

    generated = timestamp(report.get("generated_at"), "generated_at")
    times = report.get("timestamps")
    measurements = report.get("measurements_seconds")
    targets = report.get("targets_seconds")
    target_met = report.get("target_met")
    notes = report.get("measurement_notes")
    if not all(isinstance(item, dict) for item in (times, measurements, targets, target_met, notes)):
        raise ValueError("timestamps, measurements_seconds, targets_seconds, target_met and measurement_notes must be objects")

    incident = timestamp(times.get("incident_at"), "timestamps.incident_at")
    started = timestamp(times.get("restore_started_at"), "timestamps.restore_started_at", nullable=True)
    completed = timestamp(times.get("restore_completed_at"), "timestamps.restore_completed_at")
    committed = timestamp(times.get("last_recoverable_commit_at"), "timestamps.last_recoverable_commit_at", nullable=True)
    if completed < incident:
        raise ValueError("restore completion precedes incident")
    if started is not None and not incident <= started <= completed:
        raise ValueError("restore start is outside incident/completion interval")
    if committed is not None and committed > incident:
        raise ValueError("last recoverable commit is after incident")

    expected_rto = (completed - incident).total_seconds()
    expected_execution = (completed - started).total_seconds() if started else None
    expected_rpo = (incident - committed).total_seconds() if committed else None
    observed_rto = nonnegative_number(measurements.get("observed_rto"), "observed_rto")
    observed_execution = nonnegative_number(measurements.get("restore_execution"), "restore_execution", nullable=True)
    observed_rpo = nonnegative_number(measurements.get("observed_rpo"), "observed_rpo", nullable=True)
    for field, actual, expected in (
        ("observed_rto", observed_rto, expected_rto),
        ("restore_execution", observed_execution, expected_execution),
        ("observed_rpo", observed_rpo, expected_rpo),
    ):
        if (actual is None) != (expected is None) or (actual is not None and not math.isclose(actual, expected, abs_tol=0.001)):
            raise ValueError(f"{field} does not match the supplied timestamps")

    expected_flags: dict[str, bool | None] = {}
    for metric in ("rto", "rpo"):
        target = targets.get(metric)
        if target is not None and (isinstance(target, bool) or not isinstance(target, int) or target <= 0):
            raise ValueError(f"targets_seconds.{metric} must be null or a positive integer")
        measured = observed_rto if metric == "rto" else observed_rpo
        if target is not None and measured is None:
            raise ValueError(f"{metric.upper()} target is set but the measurement is missing")
        expected_flags[metric] = measured <= target if target is not None else None
        if target_met.get(metric) is not expected_flags[metric]:
            raise ValueError(f"target_met.{metric} does not match measurement and target")

    assessed = [flag for flag in expected_flags.values() if flag is not None]
    expected_assessment = "not_assessed" if not assessed else ("within_targets" if all(assessed) else "target_exceeded")
    if report.get("assessment") != expected_assessment:
        raise ValueError("assessment does not match target results")
    if not isinstance(notes.get("rto"), str) or not notes["rto"].strip():
        raise ValueError("measurement_notes.rto is required")
    if not isinstance(notes.get("rpo"), str) or not notes["rpo"].strip():
        raise ValueError("measurement_notes.rpo is required")
    missing_note = notes.get("missing_rpo")
    if observed_rpo is None:
        if not isinstance(missing_note, str) or "not measured" not in missing_note.lower():
            raise ValueError("missing RPO must be explicitly marked as not measured")
    elif missing_note is not None:
        raise ValueError("missing_rpo must be null when RPO is measured")
    if generated is None:
        raise ValueError("generated_at is required")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=pathlib.Path)
    args = parser.parse_args()
    try:
        with args.report.open("r", encoding="utf-8") as stream:
            data = json.load(stream)
        validate_report(data)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))
    print(f"Recovery report valid: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
