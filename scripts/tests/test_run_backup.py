#!/usr/bin/env python3
"""Offline tests for the encrypted backup-cycle orchestrator."""
from __future__ import annotations

import os
import pathlib
import subprocess
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
ORCHESTRATOR = ROOT / "scripts" / "run-backup.sh"

FAKE_BACKUP = r'''#!/usr/bin/env bash
set -Eeuo pipefail
mkdir -p "$ECC_BACKUP_DIR"
case "${FAKE_BACKUP_MODE:-success}" in
  success)
    printf 'encrypted archive fixture' > "$ECC_BACKUP_DIR/ecc-postgres-fresh.dump.age"
    ;;
  plaintext)
    printf 'plaintext fixture' > "$ECC_BACKUP_DIR/ecc-postgres-plaintext.dump"
    ;;
  none)
    ;;
  fail)
    exit 17
    ;;
  *)
    exit 90
    ;;
esac
'''

FAKE_UPLOADER = r'''#!/usr/bin/env bash
set -Eeuo pipefail
printf '%s\n' "$1" > "$FAKE_UPLOAD_CAPTURE"
test -s "$1"
if [[ "${FAKE_UPLOAD_FAIL:-0}" == "1" ]]; then
  exit 23
fi
'''


class BackupCycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = pathlib.Path(self.temp.name)
        self.scripts = self.base / "scripts"
        self.scripts.mkdir()
        self.backup_dir = self.base / "backups"
        self.capture = self.base / "uploaded-path.txt"

        (self.scripts / "run-backup.sh").write_bytes(ORCHESTRATOR.read_bytes())
        for name, source in (
            ("backup-postgres.sh", FAKE_BACKUP),
            ("upload-backup-s3.sh", FAKE_UPLOADER),
        ):
            path = self.scripts / name
            path.write_text(source, encoding="utf-8")
            path.chmod(0o755)

        self.env = os.environ.copy()
        self.env.update({
            "ECC_BACKUP_DIR": str(self.backup_dir),
            "ECC_BACKUP_AGE_RECIPIENT": "age1testrecipient",
            "ECC_BACKUP_S3_URI": "s3://ecc-test-bucket/nightly",
            "FAKE_UPLOAD_CAPTURE": str(self.capture),
        })

    def run_cycle(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(self.scripts / "run-backup.sh")],
            cwd=self.base,
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_uploads_the_new_encrypted_archive(self) -> None:
        result = self.run_cycle()
        self.assertEqual(result.returncode, 0, result.stderr)
        uploaded = pathlib.Path(self.capture.read_text(encoding="utf-8").strip())
        self.assertEqual(uploaded.name, "ecc-postgres-fresh.dump.age")
        self.assertEqual(uploaded.read_bytes(), b"encrypted archive fixture")
        self.assertIn("backup cycle completed", result.stdout)

    def test_does_not_upload_an_older_retained_archive(self) -> None:
        self.backup_dir.mkdir(parents=True)
        old_archive = self.backup_dir / "ecc-postgres-old.dump.age"
        old_archive.write_bytes(b"old encrypted archive")
        old_time = time.time() - 3600
        os.utime(old_archive, (old_time, old_time))
        self.env["FAKE_BACKUP_MODE"] = "none"

        result = self.run_cycle()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no new encrypted archive", result.stderr)
        self.assertFalse(self.capture.exists())

    def test_refuses_to_upload_a_plaintext_only_backup(self) -> None:
        self.env["FAKE_BACKUP_MODE"] = "plaintext"
        result = self.run_cycle()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no new encrypted archive", result.stderr)
        self.assertFalse(self.capture.exists())

    def test_backup_failure_stops_the_cycle(self) -> None:
        self.env["FAKE_BACKUP_MODE"] = "fail"
        result = self.run_cycle()
        self.assertEqual(result.returncode, 17)
        self.assertFalse(self.capture.exists())

    def test_upload_failure_is_propagated(self) -> None:
        self.env["FAKE_UPLOAD_FAIL"] = "1"
        result = self.run_cycle()
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertTrue(self.capture.exists())

    def test_encryption_recipient_is_required(self) -> None:
        self.env.pop("ECC_BACKUP_AGE_RECIPIENT")
        result = self.run_cycle()
        self.assertEqual(result.returncode, 2)
        self.assertIn("ECC_BACKUP_AGE_RECIPIENT is required", result.stderr)
        self.assertFalse(self.backup_dir.exists())

    def test_s3_destination_is_required(self) -> None:
        self.env.pop("ECC_BACKUP_S3_URI")
        result = self.run_cycle()
        self.assertEqual(result.returncode, 2)
        self.assertIn("ECC_BACKUP_S3_URI is required", result.stderr)
        self.assertFalse(self.backup_dir.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
