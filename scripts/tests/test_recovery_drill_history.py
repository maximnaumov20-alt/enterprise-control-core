#!/usr/bin/env python3
"""Tests for the ECC recovery history summary."""
from __future__ import annotations

import json
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "recovery-drill-history.py"


def report(generated: str, rto: int, rpo: int | None) -> dict:
    return {
        "schema_version": 1,
        "report_type": "ecc_recovery_drill",
        "generated_at": generated,
        "backup_reference": "test-backup",
        "measurements_seconds": {
            "observed_rto": rto,
            "restore_execution": rto - 60,
            "observed_rpo": rpo,
        },
        "assessment": "not_assessed",
    }


class RecoveryHistoryTests(unittest.TestCase):
    def test_sorts_runs_and_marks_unmeasured_rpo(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            older = root / "drill-100.json"
            newer = root / "drill-200.json"
            older.write_text(json.dumps(report("2026-11-23T10:00:00+00:00", 120, None)), encoding="utf-8")
            newer.write_text(json.dumps(report("2026-11-24T10:00:00+00:00", 90, 30)), encoding="utf-8")
            older.with_suffix(".metadata.json").write_text(
                json.dumps({"run_id": 100, "commit_sha": "abc123"}), encoding="utf-8"
            )
            output = root / "history.md"
            result = subprocess.run(
                ["python", str(SCRIPT), "--report", str(older), "--report", str(newer),
                 "--output", str(output)],
                cwd=ROOT, text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            content = output.read_text(encoding="utf-8")
            self.assertLess(content.index("2026-11-24"), content.index("2026-11-23"))
            self.assertIn("not measured", content)
            self.assertIn("100", content)
            self.assertIn("abc123", content)

    def test_rejects_timezone_naive_generated_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            path = root / "bad.json"
            path.write_text(json.dumps(report("2026-11-24T10:00:00", 90, None)), encoding="utf-8")
            result = subprocess.run(
                ["python", str(SCRIPT), "--report", str(path), "--output", str(root / "history.md")],
                cwd=ROOT, text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
