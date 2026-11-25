#!/usr/bin/env python3
"""Offline tests for downloading and verifying encrypted S3 backup objects."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOWNLOADER = ROOT / "scripts" / "download-backup-s3.sh"

FAKE_AWS = r'''#!/usr/bin/env python3
import hashlib
import json
import os
import pathlib
import sys

args = sys.argv[1:]
source = pathlib.Path(os.environ["FAKE_AWS_SOURCE"])
payload = source.read_bytes()
if args[:2] == ["s3api", "head-object"]:
    checksum = hashlib.sha256(payload).hexdigest()
    size = len(payload)
    mode = os.environ.get("FAKE_AWS_MODE", "")
    if mode == "bad-checksum":
        checksum = "0" * 64
    if mode == "bad-size":
        size += 1
    print(json.dumps({"Metadata": {"sha256": checksum}, "ContentLength": size}))
    raise SystemExit(0)
if args[:2] == ["s3", "cp"] and args[2].startswith("s3://"):
    destination = pathlib.Path(args[3])
    if os.environ.get("FAKE_AWS_MODE") == "bad-bytes":
        destination.write_bytes(payload + b"corruption")
    elif os.environ.get("FAKE_AWS_MODE") == "partial-download":
        destination.write_bytes(b"partial")
        raise SystemExit(42)
    else:
        destination.write_bytes(payload)
    raise SystemExit(0)
print("unexpected fake AWS invocation: " + repr(args), file=sys.stderr)
raise SystemExit(90)
'''


class S3DownloaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = pathlib.Path(self.temp.name)
        self.bin_dir = self.base / "bin"
        self.bin_dir.mkdir()
        fake = self.bin_dir / "aws"
        fake.write_text(FAKE_AWS, encoding="utf-8")
        fake.chmod(0o755)
        self.source = self.base / "source.dump.age"
        self.payload = b"encrypted-postgres-archive-fixture"
        self.source.write_bytes(self.payload)
        self.output = self.base / "downloads" / "restored.dump.age"
        self.env = os.environ.copy()
        self.env.update({
            "PATH": str(self.bin_dir) + os.pathsep + self.env.get("PATH", ""),
            "FAKE_AWS_SOURCE": str(self.source),
        })

    def run_downloader(self, uri: str = "s3://ecc-test-bucket/nightly/backup.dump.age"):
        return subprocess.run(
            ["bash", str(DOWNLOADER), uri, str(self.output)],
            cwd=ROOT,
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_downloads_verified_archive(self) -> None:
        result = self.run_downloader()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.output.read_bytes(), self.payload)
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        self.assertIn("downloaded and verified", result.stdout)

    def test_rejects_checksum_mismatch(self) -> None:
        self.env["FAKE_AWS_MODE"] = "bad-checksum"
        result = self.run_downloader()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())

    def test_rejects_size_mismatch(self) -> None:
        self.env["FAKE_AWS_MODE"] = "bad-size"
        result = self.run_downloader()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())

    def test_rejects_corrupted_downloaded_bytes(self) -> None:
        self.env["FAKE_AWS_MODE"] = "bad-bytes"
        result = self.run_downloader()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())
        self.assertIn("checksum or size differs", result.stderr)

    def test_cleans_partial_file_after_transient_download_failure(self) -> None:
        temp_dir = self.base / "tmp"
        temp_dir.mkdir()
        self.env["TMPDIR"] = str(temp_dir)
        self.env["FAKE_AWS_MODE"] = "partial-download"
        result = self.run_downloader()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.output.parent.glob(".ecc-download.*")), [])

    def test_rejects_non_archive_uri_before_aws_call(self) -> None:
        result = self.run_downloader("s3://ecc-test-bucket/nightly/backup.dump")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.output.parent.exists())

    def test_refuses_to_overwrite_existing_output(self) -> None:
        self.output.parent.mkdir()
        self.output.write_bytes(b"keep-existing")
        result = self.run_downloader()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.output.read_bytes(), b"keep-existing")


if __name__ == "__main__":
    unittest.main(verbosity=2)
