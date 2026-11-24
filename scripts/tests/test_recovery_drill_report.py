#!/usr/bin/env python3
"""Tests for the ECC recovery-drill report generator."""
from __future__ import annotations

import json
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "recovery-drill-report.py"


class RecoveryDrillReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = pathlib.Path(self.temp.name) / "report.json"

    def run_report(self, *extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                "python", str(SCRIPT),
                "--backup-reference", "s3://bucket/backup.dump.age",
                "--incident-at", "2026-11-24T10:00:00Z",
                "--restore-started-at", "2026-11-24T10:05:00Z",
                "--restore-completed-at", "2026-11-24T10:25:00Z",
                "--output", str(self.output),
                *extra,
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_reports_rto_rpo_and_targets_met(self) -> None:
        result = self.run_report(
            "--last-committed-at", "2026-11-24T09:59:00Z",
            "--rto-target-seconds", "1800",
            "--rpo-target-seconds", "120",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(self.output.read_text())
        self.assertEqual(report["measurements_seconds"]["observed_rto"], 1500)
        self.assertEqual(report["measurements_seconds"]["restore_execution"], 1200)
        self.assertEqual(report["measurements_seconds"]["observed_rpo"], 60)
        self.assertEqual(report["target_met"], {"rpo": True, "rto": True})
        self.assertEqual(report["assessment"], "within_targets")
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)

    def test_marks_rto_target_exceeded(self) -> None:
        result = self.run_report("--rto-target-seconds", "900")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(self.output.read_text())
        self.assertEqual(report["target_met"]["rto"], False)
        self.assertEqual(report["assessment"], "target_exceeded")

    def test_does_not_invent_rpo_from_backup_age(self) -> None:
        result = self.run_report("--rto-target-seconds", "1800")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(self.output.read_text())
        self.assertIsNone(report["measurements_seconds"]["observed_rpo"])
        self.assertIsNone(report["target_met"]["rpo"])
        self.assertIn("not measured", report["measurement_notes"]["missing_rpo"])

    def test_rpo_target_requires_last_committed_timestamp(self) -> None:
        result = self.run_report("--rpo-target-seconds", "120")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())

    def test_rejects_naive_timestamps(self) -> None:
        result = self.run_report("--incident-at", "2026-11-24T10:00:00")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())

    def test_rejects_last_commit_after_incident(self) -> None:
        result = self.run_report("--last-committed-at", "2026-11-24T10:01:00Z")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())

    def test_rejects_restore_completion_before_incident(self) -> None:
        result = subprocess.run(
            ["python", str(SCRIPT), "--backup-reference", "backup",
             "--incident-at", "2026-11-24T10:00:00Z",
             "--restore-completed-at", "2026-11-24T09:59:00Z",
             "--output", str(self.output)],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())

    def test_refuses_to_overwrite_existing_report(self) -> None:
        self.output.write_text("keep-me")
        result = self.run_report()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.output.read_text(), "keep-me")


if __name__ == "__main__":
    unittest.main(verbosity=2)
