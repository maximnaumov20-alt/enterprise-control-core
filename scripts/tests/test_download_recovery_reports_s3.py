#!/usr/bin/env python3
"""Offline tests for verified recovery evidence downloads."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import textwrap

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "download-recovery-reports-s3.py"


def make_fake_aws(directory: pathlib.Path) -> pathlib.Path:
    executable = directory / "aws"
    executable.write_text(textwrap.dedent("""\\
        #!/usr/bin/env python3
        import hashlib, json, os, pathlib, sys
        args = sys.argv[1:]
        root = pathlib.Path(os.environ["FAKE_S3_ROOT"])
        bucket = args[args.index("--bucket") + 1] if "--bucket" in args else None
        key = args[args.index("--key") + 1] if "--key" in args else None
        if args[:2] == ["s3api", "head-object"]:
            payload = (root / bucket / key).read_bytes()
            digest = hashlib.sha256(payload).hexdigest()
            if os.environ.get("FAKE_S3_BAD_HASH") == "1":
                digest = "0" * 64
            size = len(payload) + (1 if os.environ.get("FAKE_S3_BAD_SIZE") == "1" else 0)
            print(json.dumps({"Metadata": {"sha256": digest}, "ContentLength": size}))
        elif args[:2] == ["s3", "cp"] and args[2].startswith("s3://"):
            _, location = args[2].split("://", 1)
            b, k = location.split("/", 1)
            pathlib.Path(args[3]).write_bytes((root / b / k).read_bytes())
            if os.environ.get("FAKE_S3_CORRUPT_DOWNLOAD") == "1":
                pathlib.Path(args[3]).write_bytes(b"corrupt")
        else:
            raise SystemExit("unexpected fake AWS invocation: " + repr(args))
    """), encoding="utf-8")
    executable.chmod(0o700)
    return executable


def invoke(root: pathlib.Path, output: pathlib.Path, names: list[str],
           extra_env: dict[str, str] | None = None,
           source: str = "s3://test-bucket/ecc/recovery/123/1") -> subprocess.CompletedProcess[str]:
    fake_bin = root / "bin"
    fake_bin.mkdir(exist_ok=True)
    make_fake_aws(fake_bin)
    env = os.environ.copy()
    env["PATH"] = str(fake_bin) + os.pathsep + env["PATH"]
    env["FAKE_S3_ROOT"] = str(root / "objects")
    if extra_env:
        env.update(extra_env)
    command = [sys.executable, str(SCRIPT), "--source", source, "--output-dir", str(output)]
    for name in names:
        command.extend(["--file", name])
    return subprocess.run(command, text=True, capture_output=True, env=env)


def seed(root: pathlib.Path, name: str, payload: bytes = b"evidence"):
    path = root / "objects" / "test-bucket" / "ecc/recovery/123/1" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def test_downloads_multiple_files_and_verifies_bytes():
    with tempfile.TemporaryDirectory() as directory:
        root = pathlib.Path(directory)
        output = root / "out"
        seed(root, "drill-123.json", b'{"report":true}')
        seed(root, "drill-123.metadata.json", b'{"run_id":123}')
        seed(root, "history.md", b"# History")
        result = invoke(root, output, ["drill-123.json", "drill-123.metadata.json", "history.md"])
        assert result.returncode == 0, result.stderr
        assert result.stdout.count("Verified download:") == 3
        assert (output / "history.md").read_bytes() == b"# History"


def test_rejects_bad_source_and_unsafe_or_duplicate_names():
    with tempfile.TemporaryDirectory() as directory:
        root = pathlib.Path(directory)
        output = root / "out"
        seed(root, "report.json")
        assert invoke(root, output, ["report.json"], source="https://example.com/x").returncode != 0
        assert invoke(root, output, ["../report.json"]).returncode != 0
        assert invoke(root, output, ["report.json", "report.json"]).returncode != 0


def test_rejects_checksum_size_and_corrupt_download():
    for env in ({"FAKE_S3_BAD_HASH": "1"}, {"FAKE_S3_BAD_SIZE": "1"}, {"FAKE_S3_CORRUPT_DOWNLOAD": "1"}):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            seed(root, "report.json")
            result = invoke(root, root / "out", ["report.json"], extra_env=env)
            assert result.returncode != 0, env
            assert not (root / "out" / "report.json").exists()


def test_refuses_to_overwrite():
    with tempfile.TemporaryDirectory() as directory:
        root = pathlib.Path(directory)
        seed(root, "report.json")
        output = root / "out"
        output.mkdir()
        target = output / "report.json"
        target.write_text("keep", encoding="utf-8")
        result = invoke(root, output, ["report.json"])
        assert result.returncode != 0
        assert target.read_text(encoding="utf-8") == "keep"


if __name__ == "__main__":
    cases = [test_downloads_multiple_files_and_verifies_bytes,
             test_rejects_bad_source_and_unsafe_or_duplicate_names,
             test_rejects_checksum_size_and_corrupt_download,
             test_refuses_to_overwrite]
    for case in cases:
        case()
    print(f"{len(cases)} recovery evidence download tests passed")
