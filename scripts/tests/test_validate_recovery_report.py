#!/usr/bin/env python3
"""Tests for ECC recovery report validation."""
from __future__ import annotations

import copy
import json
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "validate-recovery-report.py"


def valid_report() -> dict:
    return {
        "schema_version": 1,
        "report_type": "ecc_recovery_drill",
        "generated_at": "2026-11-24T10:26:00+00:00",
        "backup_reference": "s3://bucket/backup.dump.age",
        "timestamps": {
            "incident_at": "2026-11-24T10:00:00+00:00",
            "restore_started_at": "2026-11-24T10:05:00+00:00",
            "restore_completed_at": "2026-11-24T10:25:00+00:00",
            "last_recoverable_commit_at": None,
        },
        "measurements_seconds": {
            "observed_rto": 1500,
            "restore_execution": 1200,
            "observed_rpo": None,
        },
        "targets_seconds": {"rto": 1800, "rpo": None},
        "target_met": {"rto": True, "rpo": None},
        "assessment": "within_targets",
        "measurement_notes": {
            "rto": "Incident declaration to verified restore completion.",
            "rpo": "Requires a trustworthy transaction timestamp.",
            "missing_rpo": "RPO was not measured. Backup age alone is not proof of data loss.",
        },
    }


class RecoveryReportValidationTests(unittest.TestCase):
    def validate(self, report: dict) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            return subprocess.run(
                ["python", str(SCRIPT), str(path)],
                cwd=ROOT, text=True, capture_output=True, check=False,
            )

    def test_accepts_complete_report_with_unmeasured_rpo(self) -> None:
        result = self.validate(valid_report())
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_missing_required_measurement(self) -> None:
        report = valid_report()
        del report["measurements_seconds"]["observed_rto"]
        result = self.validate(report)
        self.assertNotEqual(result.returncode, 0)

    def test_rejects_measurement_that_disagrees_with_timestamps(self) -> None:
        report = valid_report()
        report["measurements_seconds"]["observed_rto"] = 42
        result = self.validate(report)
        self.assertNotEqual(result.returncode, 0)

    def test_rejects_naive_timestamp(self) -> None:
        report = valid_report()
        report["timestamps"]["incident_at"] = "2026-11-24T10:00:00"
        result = self.validate(report)
        self.assertNotEqual(result.returncode, 0)

    def test_rejects_unmeasured_rpo_without_explicit_note(self) -> None:
        report = valid_report()
        report["measurement_notes"]["missing_rpo"] = None
        result = self.validate(report)
        self.assertNotEqual(result.returncode, 0)

    def test_rejects_inconsistent_target_assessment(self) -> None:
        report = valid_report()
        report["target_met"]["rto"] = False
        result = self.validate(report)
        self.assertNotEqual(result.returncode, 0)

    def test_rejects_unknown_schema(self) -> None:
        report = valid_report()
        report["schema_version"] = 2
        result = self.validate(report)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
