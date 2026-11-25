#!/usr/bin/env python3
"""Offline tests for read-only PostgreSQL backup verification."""
from __future__ import annotations

import os
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verify-backup-postgres.sh"
ARCHIVE = b"valid-postgres-archive-fixture"

FAKE_DOCKER = r'''#!/usr/bin/env bash
set -Eeuo pipefail
case " $* " in
  *" pg_restore --list "*)
    payload="$(cat)"
    [[ "$payload" == "valid-postgres-archive-fixture" ]] || exit 42
    [[ "${FAKE_ARCHIVE_FAIL:-0}" != "1" ]] || exit 43
    ;;
  *)
    echo "unexpected docker invocation: $*" >&2
    exit 90
    ;;
esac
'''

FAKE_AGE = r'''#!/usr/bin/env bash
set -Eeuo pipefail
identity=""
input=""
while (($#)); do
  case "$1" in
    --decrypt) shift ;;
    --identity) shift; identity="$1"; shift ;;
    *) input="$1"; shift ;;
  esac
done
[[ -n "$identity" && -n "$input" ]]
[[ "$(cat "$identity")" == "fixture identity" ]] || exit 45
[[ -s "$input" ]]
printf '%s' "valid-postgres-archive-fixture"
'''


class VerifyBackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.docker = self.bin / "docker"
        self.docker.write_text(FAKE_DOCKER, encoding="utf-8")
        self.docker.chmod(0o755)
        self.age = self.bin / "age"
        self.age.write_text(FAKE_AGE, encoding="utf-8")
        self.age.chmod(0o755)
        self.backup = self.root / "backup.dump"
        self.backup.write_bytes(ARCHIVE)
        self.identity = self.root / "identity.txt"
        self.identity.write_text("fixture identity", encoding="utf-8")
        self.env = os.environ.copy()
        self.env.update({
            "PATH": f"{self.bin}:{self.env.get('PATH', '')}",
            "ECC_BACKUP_AGE_IDENTITY": str(self.identity),
        })

    def run_verify(self, path: pathlib.Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(SCRIPT), str(path or self.backup)],
            cwd=ROOT, env=self.env, text=True, capture_output=True, check=False,
        )

    def test_verifies_plaintext_archive_without_modifying_database(self) -> None:
        result = self.run_verify()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("archive is valid", result.stdout)

    def test_verifies_encrypted_archive_by_streaming_decryption(self) -> None:
        encrypted = self.root / "backup.dump.age"
        encrypted.write_bytes(b"encrypted fixture")
        result = self.run_verify(encrypted)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("archive is valid", result.stdout)

    def test_rejects_wrong_identity(self) -> None:
        encrypted = self.root / "backup.dump.age"
        encrypted.write_bytes(b"encrypted fixture")
        self.identity.write_text("wrong identity", encoding="utf-8")
        result = self.run_verify(encrypted)
        self.assertNotEqual(result.returncode, 0)

    def test_rejects_invalid_archive(self) -> None:
        self.env["FAKE_ARCHIVE_FAIL"] = "1"
        result = self.run_verify()
        self.assertNotEqual(result.returncode, 0)

    def test_rejects_unknown_suffix_before_docker_access(self) -> None:
        unknown = self.root / "backup.tar"
        unknown.write_bytes(ARCHIVE)
        result = self.run_verify(unknown)
        self.assertEqual(result.returncode, 2)
        self.assertIn("must end in", result.stderr)

    def test_requires_identity_for_encrypted_archive(self) -> None:
        encrypted = self.root / "backup.dump.age"
        encrypted.write_bytes(b"encrypted fixture")
        self.env.pop("ECC_BACKUP_AGE_IDENTITY")
        result = self.run_verify(encrypted)
        self.assertEqual(result.returncode, 2)
        self.assertIn("requires readable", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
