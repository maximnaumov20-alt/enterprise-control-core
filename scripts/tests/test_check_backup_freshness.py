#!/usr/bin/env python3
"""Offline tests for backup freshness monitoring."""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check-backup-freshness.py"


class BackupFreshnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.backups = pathlib.Path(self.temp.name) / "backups"
        self.backups.mkdir()
        self.env = os.environ.copy()
        self.env.update({
            "ECC_BACKUP_DIR": str(self.backups),
            "ECC_BACKUP_MAX_AGE_HOURS": "24",
            "ECC_BACKUP_REQUIRE_ENCRYPTED": "0",
        })

    def run_check(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT)], cwd=ROOT, env=self.env,
            text=True, capture_output=True, check=False,
        )

    def test_accepts_recent_published_archive(self) -> None:
        archive = self.backups / "ecc-postgres-current.dump"
        archive.write_bytes(b"valid")
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("freshness OK", result.stdout)

    def test_rejects_stale_archive(self) -> None:
        archive = self.backups / "ecc-postgres-old.dump"
        archive.write_bytes(b"valid")
        old = time.time() - 25 * 3600
        os.utime(archive, (old, old))
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("stale", result.stderr)

    def test_ignores_partial_and_empty_files(self) -> None:
        (self.backups / "ecc-postgres-incomplete.dump.partial").write_bytes(b"partial")
        (self.backups / "ecc-postgres-empty.dump").write_bytes(b"")
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("No published", result.stderr)

    def test_can_require_encrypted_archive(self) -> None:
        (self.backups / "ecc-postgres-plain.dump").write_bytes(b"plaintext")
        self.env["ECC_BACKUP_REQUIRE_ENCRYPTED"] = "1"
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("encrypted", result.stderr)

        encrypted = self.backups / "ecc-postgres-current.dump.age"
        encrypted.write_bytes(b"ciphertext")
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_invalid_age_configuration(self) -> None:
        self.env["ECC_BACKUP_MAX_AGE_HOURS"] = "0"
        result = self.run_check()
        self.assertEqual(result.returncode, 2)
        self.assertIn("positive integer", result.stderr)

    def test_ignores_symlinked_backup(self) -> None:
        target = self.backups / "outside.dump"
        target.write_bytes(b"valid")
        link = self.backups / "ecc-postgres-linked.dump"
        link.symlink_to(target)
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("No published", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
