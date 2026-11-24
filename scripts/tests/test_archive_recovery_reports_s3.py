#!/usr/bin/env python3
"""Offline tests for the recovery evidence S3 archiver."""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile
import textwrap


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "archive-recovery-reports-s3.py"


def make_fake_aws(directory: pathlib.Path) -> pathlib.Path:
    executable = directory / "aws"
    executable.write_text(textwrap.dedent("""\
        #!/usr/bin/env python3
        import hashlib
        import json
        import os
        import pathlib
        import sys
        args = sys.argv[1:]
        root = pathlib.Path(os.environ["FAKE_S3_ROOT"])
        if args[:2] == ["s3", "cp"]:
            source, destination = args[2], args[3]
            if source.startswith("s3://"):
                bucket, key = source[5:].split("/", 1)
                pathlib.Path(destination).write_bytes((root / bucket / key).read_bytes())
            else:
                bucket, key = destination[5:].split("/", 1)
                target = root / bucket / key
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(pathlib.Path(source).read_bytes())
                if os.environ.get("FAKE_S3_CORRUPT_UPLOAD") == "1":
                    target.write_bytes(b"corrupt")
        elif args[:2] == ["s3api", "head-object"]:
            bucket = args[args.index("--bucket") + 1]
            key = args[args.index("--key") + 1]
            payload = (root / bucket / key).read_bytes()
            digest = hashlib.sha256(payload).hexdigest()
            if os.environ.get("FAKE_S3_BAD_METADATA") == "1":
                digest = "0" * 64
            print(json.dumps({
                "Metadata": {"sha256": digest},
                "ContentLength": len(payload),
            }))
        else:
            raise SystemExit("unexpected fake AWS invocation: " + repr(args))
    """), encoding="utf-8")
    executable.chmod(0o700)
    return executable


def invoke(files: list[pathlib.Path], destination: str = "s3://test-bucket/ecc/recovery",
           run_id: str = "1234", attempt: str = "2",
           extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as directory:
        temp = pathlib.Path(directory)
        fake_bin = temp / "bin"
        fake_bin.mkdir()
        make_fake_aws(fake_bin)
        env = os.environ.copy()
        env["PATH"] = str(fake_bin) + os.pathsep + env["PATH"]
        env["FAKE_S3_ROOT"] = str(temp / "objects")
        if extra_env:
            env.update(extra_env)
        command = [
            sys.executable, str(SCRIPT),
            "--destination", destination,
            "--run-id", run_id,
            "--run-attempt", attempt,
        ]
        for path in files:
            command.extend(["--file", str(path)])
        return subprocess.run(command, text=True, capture_output=True, env=env)


def test_archives_multiple_files_and_verifies_bytes() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = pathlib.Path(directory)
        report = root / "drill-1234.json"
        metadata = root / "drill-1234.metadata.json"
        history = root / "history.md"
        report.write_text('{"report_type":"ecc_recovery_drill"}\n', encoding="utf-8")
        metadata.write_text('{"run_id":1234}\n', encoding="utf-8")
        history.write_text("# Recovery history\n", encoding="utf-8")
        result = invoke([report, metadata, history])
        assert result.returncode == 0, result.stderr
        assert result.stdout.count("Verified archive:") == 3


def test_rejects_bad_destination_and_nonpositive_run_ids() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source = pathlib.Path(directory) / "report.json"
        source.write_text("{}")
        for destination in ("https://example.com/archive", "s3://bucket/../escape", "s3:///missing"):
            result = invoke([source], destination=destination)
            assert result.returncode != 0
        assert invoke([source], run_id="0").returncode != 0
        assert invoke([source], attempt="-1").returncode != 0


def test_rejects_duplicate_names_and_unsafe_inputs() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = pathlib.Path(directory)
        one = root / "one"
        other = root / "other"
        other.mkdir()
        two = other / "one"
        one.write_text("first")
        two.write_text("second")
        assert invoke([one, two]).returncode != 0
        symlink = root / "link.json"
        symlink.symlink_to(one)
        assert invoke([symlink]).returncode != 0


def test_rejects_metadata_mismatch() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source = pathlib.Path(directory) / "report.json"
        source.write_text('{"ok":true}')
        result = invoke([source], extra_env={"FAKE_S3_BAD_METADATA": "1"})
        assert result.returncode != 0
        assert "metadata checksum mismatch" in result.stderr


def test_rejects_corrupted_upload() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source = pathlib.Path(directory) / "report.json"
        source.write_text('{"ok":true}')
        result = invoke([source], extra_env={"FAKE_S3_CORRUPT_UPLOAD": "1"})
        assert result.returncode != 0
        assert "metadata checksum mismatch" in result.stderr


if __name__ == "__main__":
    tests = [
        test_archives_multiple_files_and_verifies_bytes,
        test_rejects_bad_destination_and_nonpositive_run_ids,
        test_rejects_duplicate_names_and_unsafe_inputs,
        test_rejects_metadata_mismatch,
        test_rejects_corrupted_upload,
    ]
    for test_case in tests:
        test_case()
    print(f"{len(tests)} recovery archive tests passed")
