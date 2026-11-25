#!/usr/bin/env python3
"""Offline tests for JSON backup health monitoring."""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "backup-healthcheck.py"


class BackupHealthcheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = pathlib.Path(self.temp.name)
        self.env = os.environ.copy()
        self.env.update({
            "ECC_BACKUP_DIR": str(self.directory),
            "ECC_BACKUP_MAX_AGE_HOURS": "26",
            "ECC_BACKUP_REQUIRE_ENCRYPTED": "1",
        })

    def run_check(self) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
        result = subprocess.run([os.environ.get("PYTHON", "python3"), str(SCRIPT)],
                                env=self.env, text=True, capture_output=True, check=False)
        return result, json.loads(result.stdout)

    def test_reports_recent_encrypted_backup(self) -> None:
        (self.directory / "ecc-postgres-recent.dump.age").write_bytes(b"ciphertext")
        result, payload = self.run_check()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["encrypted"])
        self.assertEqual(payload["size_bytes"], 10)

    def test_reports_missing_backup_as_critical(self) -> None:
        result, payload = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(payload["status"], "critical")

    def test_rejects_plaintext_when_encryption_is_required(self) -> None:
        (self.directory / "ecc-postgres-plain.dump").write_bytes(b"archive")
        result, payload = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(payload["status"], "critical")

    def test_reports_stale_backup_as_critical(self) -> None:
        archive = self.directory / "ecc-postgres-old.dump.age"
        archive.write_bytes(b"ciphertext")
        old = time.time() - 27 * 3600
        os.utime(archive, (old, old))
        result, payload = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(payload["status"], "critical")
        self.assertEqual(payload["backup_file"], archive.name)

    def test_ignores_symlinked_backup(self) -> None:
        target = self.directory / "target"
        target.write_bytes(b"ciphertext")
        (self.directory / "ecc-postgres-link.dump.age").symlink_to(target)
        result, payload = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(payload["status"], "critical")

    def test_invalid_configuration_has_distinct_exit_code(self) -> None:
        self.env["ECC_BACKUP_MAX_AGE_HOURS"] = "zero"
        result, payload = self.run_check()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(payload["status"], "configuration_error")


if __name__ == "__main__":
    unittest.main(verbosity=2)
