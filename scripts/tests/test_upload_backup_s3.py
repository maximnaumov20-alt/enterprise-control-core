#!/usr/bin/env python3
"""Offline tests for the S3 backup uploader using a deterministic fake AWS CLI."""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
UPLOADER = ROOT / "scripts" / "upload-backup-s3.sh"

FAKE_AWS = r'''#!/usr/bin/env python3
import json
import os
import pathlib
import sys

args = sys.argv[1:]
state_path = pathlib.Path(os.environ["FAKE_AWS_STATE"])
if args[:2] == ["s3", "cp"]:
    source = pathlib.Path(args[2])
    metadata_index = args.index("--metadata")
    checksum = args[metadata_index + 1].split("=", 1)[1]
    state = {
        "checksum": checksum,
        "size": source.stat().st_size,
        "args": args,
        "source": str(source),
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    raise SystemExit(0)
if args[:2] == ["s3", "cp"] and args[2].startswith("s3://"):
    state = json.loads(state_path.read_text(encoding="utf-8"))
    destination = pathlib.Path(args[3])
    payload = pathlib.Path(state["source"]).read_bytes()
    if os.environ.get("FAKE_AWS_MODE") == "bad-readback":
        payload = payload + b"corruption"
    destination.write_bytes(payload)
    raise SystemExit(0)
if args[:2] == ["s3api", "head-object"]:
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if os.environ.get("FAKE_AWS_MODE") == "bad-checksum":
        state["checksum"] = "0" * 64
    if os.environ.get("FAKE_AWS_MODE") == "bad-size":
        state["size"] += 1
    print(json.dumps({
        "Metadata": {"sha256": state["checksum"]},
        "ContentLength": state["size"],
    }))
    raise SystemExit(0)
print("unexpected fake AWS invocation: " + repr(args), file=sys.stderr)
raise SystemExit(90)
'''


class S3UploaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = pathlib.Path(self.temp.name)
        self.bin_dir = self.base / "bin"
        self.bin_dir.mkdir()
        fake = self.bin_dir / "aws"
        fake.write_text(FAKE_AWS, encoding="utf-8")
        fake.chmod(0o755)
        self.archive = self.base / "test-backup.dump.age"
        self.archive.write_bytes(b"encrypted-test-archive-content")
        self.state = self.base / "aws-state.json"
        self.env = os.environ.copy()
        self.env.update({
            "PATH": str(self.bin_dir) + os.pathsep + self.env.get("PATH", ""),
            "FAKE_AWS_STATE": str(self.state),
            "ECC_BACKUP_S3_URI": "s3://ecc-test-bucket/nightly",
        })

    def run_uploader(self, archive: pathlib.Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(UPLOADER), str(archive or self.archive)],
            cwd=ROOT,
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_upload_and_metadata_verification_succeed(self) -> None:
        result = self.run_uploader()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Encrypted backup uploaded and read-back verified", result.stdout)
        state = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertIn("s3://ecc-test-bucket/nightly/test-backup.dump.age", state["args"])

    def test_checksum_mismatch_is_rejected(self) -> None:
        self.env["FAKE_AWS_MODE"] = "bad-checksum"
        result = self.run_uploader()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Remote verification failed", result.stderr)

    def test_size_mismatch_is_rejected(self) -> None:
        self.env["FAKE_AWS_MODE"] = "bad-size"
        result = self.run_uploader()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Remote verification failed", result.stderr)

    def test_downloaded_bytes_mismatch_is_rejected(self) -> None:
        self.env["FAKE_AWS_MODE"] = "bad-readback"
        result = self.run_uploader()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Remote read-back verification failed", result.stderr)

    def test_plaintext_dump_is_rejected_before_aws_call(self) -> None:
        plaintext = self.base / "backup.dump"
        plaintext.write_bytes(b"not-encrypted")
        result = self.run_uploader(plaintext)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.state.exists())

    def test_kms_arguments_are_forwarded(self) -> None:
        self.env["ECC_BACKUP_S3_SSE"] = "aws:kms"
        self.env["ECC_BACKUP_S3_KMS_KEY_ID"] = "alias/ecc-backups"
        result = self.run_uploader()
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertIn("--sse-kms-key-id", state["args"])
        self.assertIn("alias/ecc-backups", state["args"])

    def test_kms_without_key_id_is_rejected(self) -> None:
        self.env["ECC_BACKUP_S3_SSE"] = "aws:kms"
        result = self.run_uploader()
        self.assertEqual(result.returncode, 2)
        self.assertIn("ECC_BACKUP_S3_KMS_KEY_ID is required", result.stderr)
        self.assertFalse(self.state.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
