#!/usr/bin/env python3
"""Offline regression tests for the destructive PostgreSQL restore wrapper."""
from __future__ import annotations

import os
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "restore-postgres.sh"
ARCHIVE = b"valid-postgres-archive-fixture"

FAKE_DOCKER = r'''#!/usr/bin/env bash
set -Eeuo pipefail
case " $* " in
  *" pg_isready "*) exit 0 ;;
  *" psql "*)
    printf '%s' "${FAKE_DB_NAME:-ecc}"
    ;;
  *" pg_restore --list "*)
    payload="$(cat)"
    [[ "$payload" == "valid-postgres-archive-fixture" ]] || exit 42
    [[ "${FAKE_ARCHIVE_LIST_FAIL:-0}" != "1" ]] || exit 43
    ;;
  *" pg_restore --clean "*)
    payload="$(cat)"
    printf '%s' "$payload" > "$FAKE_RESTORE_CAPTURE"
    [[ "${FAKE_RESTORE_FAIL:-0}" != "1" ]] || exit 44
    ;;
  *)
    echo "unexpected docker invocation: $*" >&2
    exit 90
    ;;
esac
'''

FAKE_AGE = r'''#!/usr/bin/env bash
set -Eeuo pipefail
[[ "${FAKE_AGE_FAIL:-0}" != "1" ]] || exit 45
output=""
input=""
while (($#)); do
  case "$1" in
    --decrypt)
      shift
      ;;
    --identity)
      shift
      [[ $# -gt 0 ]]
      shift
      ;;
    --output)
      shift
      [[ $# -gt 0 ]]
      output="$1"
      shift
      ;;
    *)
      input="$1"
      shift
      ;;
  esac
done
[[ -n "$output" && -n "$input" ]]
cat "$input" > "$output"
'''

class RestorePostgresTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.tmpdir = self.root / "tmp"
        self.tmpdir.mkdir()
        self.backup = self.root / "backup.dump"
        self.backup.write_bytes(ARCHIVE)
        self.capture = self.root / "restored.bin"
        self.identity = self.root / "backup.agekey"
        self.identity.write_text("fixture identity", encoding="utf-8")
        self.docker = self.bin / "docker"
        self.docker.write_text(FAKE_DOCKER, encoding="utf-8")
        self.docker.chmod(0o755)
        self.age = self.bin / "age"
        self.age.write_text(FAKE_AGE, encoding="utf-8")
        self.age.chmod(0o755)
        self.env = os.environ.copy()
        self.env.update({
            "PATH": f"{self.bin}:{self.env['PATH']}",
            "TMPDIR": str(self.tmpdir),
            "ECC_RESTORE_CONFIRM": "YES",
            "ECC_BACKUP_AGE_IDENTITY": str(self.identity),
            "FAKE_RESTORE_CAPTURE": str(self.capture),
        })

    def run_restore(self, backup: pathlib.Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(SCRIPT), str(backup or self.backup)],
            cwd=ROOT,
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_restores_plaintext_archive_after_validation(self) -> None:
        result = self.run_restore()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.capture.read_bytes(), ARCHIVE)
        self.assertIn("Restore command completed", result.stdout)

    def test_requires_explicit_confirmation_before_docker_access(self) -> None:
        self.env.pop("ECC_RESTORE_CONFIRM")
        result = self.run_restore()
        self.assertEqual(result.returncode, 2)
        self.assertIn("Refusing restore", result.stderr)
        self.assertFalse(self.capture.exists())

    def test_rejects_wrong_target_database_before_restore(self) -> None:
        self.env["FAKE_DB_NAME"] = "postgres"
        result = self.run_restore()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("expected database 'ecc'", result.stderr)
        self.assertFalse(self.capture.exists())

    def test_rejects_invalid_archive_before_modifying_database(self) -> None:
        self.env["FAKE_ARCHIVE_LIST_FAIL"] = "1"
        result = self.run_restore()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.capture.exists())

    def test_decrypts_encrypted_archive_and_removes_plaintext_temporary_file(self) -> None:
        encrypted = self.root / "backup.dump.age"
        encrypted.write_bytes(b"encrypted fixture")
        result = self.run_restore(encrypted)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.capture.read_bytes(), ARCHIVE)
        self.assertEqual(list(self.tmpdir.glob("ecc-restore.*")), [])

    def test_decryption_failure_does_not_restore_or_leave_plaintext(self) -> None:
        encrypted = self.root / "backup.dump.age"
        encrypted.write_bytes(b"encrypted fixture")
        self.env["FAKE_AGE_FAIL"] = "1"
        result = self.run_restore(encrypted)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.capture.exists())
        self.assertEqual(list(self.tmpdir.glob("ecc-restore.*")), [])

    def test_restore_failure_is_propagated_and_plaintext_is_cleaned(self) -> None:
        encrypted = self.root / "backup.dump.age"
        encrypted.write_bytes(b"encrypted fixture")
        self.env["FAKE_RESTORE_FAIL"] = "1"
        result = self.run_restore(encrypted)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(self.tmpdir.glob("ecc-restore.*")), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
