#!/usr/bin/env python3
"""Offline regression tests for PostgreSQL backup archive publication."""
from __future__ import annotations

import os
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "backup-postgres.sh"
ARCHIVE = b"valid-postgres-archive-fixture"

FAKE_DOCKER = r'''#!/usr/bin/env bash
set -Eeuo pipefail
case " $* " in
  *" pg_isready "*) exit 0 ;;
  *" pg_dump "*)
    if [[ "${FAKE_DUMP_MODE:-valid}" == "empty" ]]; then
      exit 0
    fi
    printf '%s' 'valid-postgres-archive-fixture'
    ;;
  *" pg_restore "*)
    if [[ "${FAKE_RESTORE_LIST_FAIL:-0}" == "1" ]]; then
      exit 42
    fi
    payload="$(cat)"
    [[ "$payload" == "valid-postgres-archive-fixture" ]]
    ;;
  *)
    echo "unexpected docker invocation: $*" >&2
    exit 90
    ;;
esac
'''

FAKE_AGE = r'''#!/usr/bin/env bash
set -Eeuo pipefail
[[ "${FAKE_AGE_FAIL:-0}" != "1" ]] || exit 44
output=""
input=""
while (($#)); do
  case "$1" in
    --output) output="$2"; shift 2 ;;
    --encrypt|--recipient) shift; [[ "$1" == "--recipient" ]] && shift ;;
    *) input="$1"; shift ;;
  esac
done
[[ -n "$output" && -n "$input" ]]
printf 'encrypted:' > "$output"
cat "$input" >> "$output"
'''

class BackupPostgresTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.backups = self.root / "backups"
        self.docker = self.bin / "docker"
        self.docker.write_text(FAKE_DOCKER, encoding="utf-8")
        self.docker.chmod(0o755)
        self.age = self.bin / "age"
        self.age.write_text(FAKE_AGE, encoding="utf-8")
        self.age.chmod(0o755)
        self.env = os.environ.copy()
        self.env.update({
            "PATH": f"{self.bin}:{self.env['PATH']}",
            "ECC_BACKUP_DIR": str(self.backups),
            "ECC_BACKUP_RETENTION_DAYS": "14",
        })

    def run_backup(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(SCRIPT)],
            cwd=ROOT,
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_publishes_verified_plaintext_archive_with_restricted_permissions(self) -> None:
        result = self.run_backup()
        self.assertEqual(result.returncode, 0, result.stderr)
        files = list(self.backups.glob("ecc-postgres-*.dump"))
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].read_bytes(), ARCHIVE)
        self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
        self.assertIn("not encrypted", result.stderr)
        self.assertFalse(list(self.backups.glob("*.partial")))

    def test_publishes_encrypted_archive_and_removes_plaintext(self) -> None:
        self.env["ECC_BACKUP_AGE_RECIPIENT"] = "age1fixture"
        result = self.run_backup()
        self.assertEqual(result.returncode, 0, result.stderr)
        encrypted = list(self.backups.glob("ecc-postgres-*.dump.age"))
        self.assertEqual(len(encrypted), 1)
        self.assertEqual(encrypted[0].read_bytes(), b"encrypted:" + ARCHIVE)
        self.assertEqual(encrypted[0].stat().st_mode & 0o777, 0o600)
        self.assertFalse(list(self.backups.glob("ecc-postgres-*.dump")))
        self.assertFalse(list(self.backups.glob("*.partial")))

    def test_rejects_empty_dump(self) -> None:
        self.env["FAKE_DUMP_MODE"] = "empty"
        result = self.run_backup()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("empty", result.stderr.lower())
        self.assertFalse(list(self.backups.glob("ecc-postgres-*")))

    def test_rejects_invalid_archive_before_publication(self) -> None:
        self.env["FAKE_RESTORE_LIST_FAIL"] = "1"
        result = self.run_backup()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(list(self.backups.glob("ecc-postgres-*")))
        self.assertFalse(list(self.backups.glob("*.partial")))

    def test_encryption_failure_does_not_publish_backup(self) -> None:
        self.env["ECC_BACKUP_AGE_RECIPIENT"] = "age1fixture"
        self.env["FAKE_AGE_FAIL"] = "1"
        result = self.run_backup()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(list(self.backups.glob("ecc-postgres-*")))
        self.assertFalse(list(self.backups.glob("*.partial")))

    def test_invalid_retention_is_rejected_before_backup(self) -> None:
        self.env["ECC_BACKUP_RETENTION_DAYS"] = "0"
        result = self.run_backup()
        self.assertEqual(result.returncode, 2)
        self.assertIn("positive integer", result.stderr)
        self.assertFalse(self.backups.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
