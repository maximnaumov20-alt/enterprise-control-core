#!/usr/bin/env python3
"""Unit tests for ECC runtime monitoring."""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import unittest
from unittest.mock import patch
from urllib.error import URLError

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import importlib.util
SPEC = importlib.util.spec_from_file_location("monitor_runtime", ROOT / "scripts" / "monitor-runtime.py")
assert SPEC is not None and SPEC.loader is not None
monitor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(monitor)


class Response:
    status = 200
    def __enter__(self):
        return self
    def __exit__(self, *_args):
        return False


class RuntimeMonitorTests(unittest.TestCase):
    def test_readiness_success(self):
        with patch.object(monitor.urllib.request, "urlopen", return_value=Response()):
            self.assertEqual(monitor.check_readiness("http://localhost/ready", 1),
                             {"status": "ok", "http_status": 200})

    def test_readiness_connection_failure_is_sanitized(self):
        with patch.object(monitor.urllib.request, "urlopen", side_effect=URLError("secret detail")):
            result = monitor.check_readiness("http://localhost/ready", 1)
        self.assertEqual(result, {"status": "critical", "reason": "readiness endpoint unavailable"})
        self.assertNotIn("secret", json.dumps(result))

    def test_backup_success_returns_only_allowlisted_fields(self):
        completed = subprocess.CompletedProcess([], 0, json.dumps({
            "status": "ok", "age_hours": 1.2, "max_age_hours": 26,
            "size_bytes": 100, "encrypted": True, "backup_dir": "/private/path",
        }), "")
        with patch.object(monitor.subprocess, "run", return_value=completed):
            result = monitor.check_backup(1)
        self.assertEqual(result["status"], "ok")
        self.assertNotIn("backup_dir", result)

    def test_backup_invalid_output_is_critical(self):
        completed = subprocess.CompletedProcess([], 1, "not-json", "sensitive output")
        with patch.object(monitor.subprocess, "run", return_value=completed):
            result = monitor.check_backup(1)
        self.assertEqual(result["status"], "critical")
        self.assertNotIn("sensitive", json.dumps(result))

    def test_backup_timeout_is_critical(self):
        with patch.object(monitor.subprocess, "run", side_effect=subprocess.TimeoutExpired("check", 1)):
            self.assertEqual(monitor.check_backup(1)["status"], "critical")

    def test_webhook_requires_https(self):
        env = os.environ.copy()
        env.update({
            "ECC_MONITOR_READINESS_URL": "http://127.0.0.1:8000/ready",
            "ECC_MONITOR_WEBHOOK_URL": "http://example.test/hook",
        })
        with patch.dict("os.environ", env):
            result = subprocess.run([sys.executable, str(ROOT / "scripts" / "monitor-runtime.py")],
                                    env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["status"], "configuration_error")


if __name__ == "__main__":
    unittest.main(verbosity=2)
