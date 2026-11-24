#!/usr/bin/env python3
"""Build a comparable Markdown history from downloaded ECC recovery-drill reports."""
from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import tempfile
from datetime import datetime, timezone


def load_report(path: pathlib.Path) -> tuple[dict, dict]:
    with path.open("r", encoding="utf-8") as stream:
        report = json.load(stream)
    if not isinstance(report, dict) or report.get("schema_version") != 1 or report.get("report_type") != "ecc_recovery_drill":
        raise ValueError(f"unsupported recovery report: {path}")
    generated_value = report.get("generated_at")
    if not isinstance(generated_value, str) or not generated_value:
        raise ValueError(f"generated_at must be a timezone-aware ISO-8601 timestamp: {path}")
    metadata_path = path.with_suffix(".metadata.json")
    metadata = {}
    if metadata_path.exists():
        with metadata_path.open("r", encoding="utf-8") as stream:
            metadata = json.load(stream)
        if not isinstance(metadata, dict):
            raise ValueError(f"metadata must be a JSON object: {metadata_path}")
    generated = datetime.fromisoformat(generated_value.replace("Z", "+00:00"))
    if generated.tzinfo is None or generated.utcoffset() is None:
        raise ValueError(f"generated_at must include a timezone: {path}")
    return report, {"metadata": metadata, "generated": generated.astimezone(timezone.utc), "path": path.name}


def markdown_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def format_seconds(value: object) -> str:
    if value is None:
        return "not measured"
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("recovery measurements must be finite, non-negative numbers or null")
    return f"{value:g} s"


def build_history(paths: list[pathlib.Path]) -> str:
    entries = []
    for path in paths:
        report, context = load_report(path)
        measurements = report.get("measurements_seconds")
        if not isinstance(measurements, dict):
            raise ValueError(f"measurements_seconds must be an object: {path}")
        if "observed_rto" not in measurements or measurements["observed_rto"] is None:
            raise ValueError(f"observed_rto is required and must be measured: {path}")
        format_seconds(measurements["observed_rto"])
        if measurements.get("observed_rpo") is not None:
            format_seconds(measurements["observed_rpo"])
        if not isinstance(report.get("assessment"), str) or not report["assessment"].strip():
            raise ValueError(f"assessment must be a non-empty string: {path}")
        entries.append((report, context))
    entries.sort(key=lambda item: item[1]["generated"], reverse=True)

    lines = [
        "# ECC recovery drill history",
        "",
        f"Generated at: {datetime.now(timezone.utc).isoformat()}",
        "",
        "This index compares supplied report files only. It is not a durable archive or proof of production recovery performance.",
        "",
        "| Generated (UTC) | Run | Commit | Observed RTO | Observed RPO | Assessment |",
        "|---|---:|---|---:|---:|---|",
    ]
    for report, context in entries:
        metadata = context["metadata"]
        generated = context["generated"].isoformat()
        run_id = metadata.get("run_id", "unknown")
        commit = metadata.get("commit_sha", "unknown")
        measurements = report["measurements_seconds"]
        assessment = report.get("assessment", "unknown")
        lines.append(
            "| " + " | ".join(markdown_cell(value) for value in (
                generated, run_id, commit,
                format_seconds(measurements.get("observed_rto")),
                format_seconds(measurements.get("observed_rpo")),
                assessment,
            )) + " |"
        )
    lines.extend([
        "",
        "RPO is shown as **not measured** when no trustworthy last-recoverable-transaction timestamp was supplied.",
        "Collect reports from separate workflow artifacts and pass their JSON paths together to compare runs.",
        "",
    ])
    return "\n".join(lines)


def write_output(path: pathlib.Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=".ecc-recovery-history-", dir=path.parent)
    temporary = pathlib.Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="append", required=True, type=pathlib.Path,
                        help="recovery report JSON; repeat to compare multiple runs")
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()
    try:
        content = build_history(args.report)
        write_output(args.output, content)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(f"Recovery history written: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
