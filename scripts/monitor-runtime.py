#!/usr/bin/env python3
"""Poll ECC readiness and backup health; emit safe JSON and optional webhook alerts."""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request


ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKUP_CHECK = ROOT / "scripts" / "backup-healthcheck.py"


def check_readiness(url: str, timeout: float) -> dict[str, object]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            healthy = 200 <= response.status < 300
            return {"status": "ok" if healthy else "critical", "http_status": response.status}
    except urllib.error.HTTPError as exc:
        return {"status": "critical", "http_status": exc.code}
    except (urllib.error.URLError, TimeoutError, OSError):
        return {"status": "critical", "reason": "readiness endpoint unavailable"}


def check_backup(timeout: float) -> dict[str, object]:
    try:
        completed = subprocess.run(
            [sys.executable, str(BACKUP_CHECK)],
            check=False, capture_output=True, text=True, timeout=timeout,
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired:
        return {"status": "critical", "reason": "backup healthcheck timed out"}
    except OSError:
        return {"status": "critical", "reason": "backup healthcheck could not be started"}
    try:
        payload = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError):
        return {"status": "critical", "reason": "backup healthcheck returned invalid output"}
    if completed.returncode == 2 or payload.get("status") == "configuration_error":
        return {"status": "configuration_error", "reason": "backup healthcheck configuration is invalid"}
    if completed.returncode != 0 or payload.get("status") != "ok":
        # Do not forward filesystem paths or arbitrary subprocess output to monitoring.
        return {"status": "critical", "reason": "backup healthcheck reported a problem"}
    return {
        "status": "ok",
        "age_hours": payload.get("age_hours"),
        "max_age_hours": payload.get("max_age_hours"),
        "size_bytes": payload.get("size_bytes"),
        "encrypted": payload.get("encrypted"),
    }


def send_alert(url: str, report: dict[str, object], timeout: float) -> bool:
    body = json.dumps({
        "text": "ECC monitoring detected an unhealthy check.",
        "status": report["status"],
        "checks": {name: value["status"] for name, value in report["checks"].items()},
    }).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def main() -> int:
    readiness_url = os.environ.get("ECC_MONITOR_READINESS_URL", "http://127.0.0.1:8000/ready")
    webhook_url = os.environ.get("ECC_MONITOR_WEBHOOK_URL", "").strip()
    try:
        timeout = float(os.environ.get("ECC_MONITOR_TIMEOUT_SECONDS", "5"))
    except ValueError:
        print(json.dumps({"status": "configuration_error", "reason": "invalid timeout"}))
        return 2
    parsed = urllib.parse.urlparse(readiness_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or timeout <= 0:
        print(json.dumps({"status": "configuration_error", "reason": "invalid monitoring configuration"}))
        return 2
    if webhook_url:
        webhook = urllib.parse.urlparse(webhook_url)
        if webhook.scheme != "https" or not webhook.netloc:
            print(json.dumps({"status": "configuration_error", "reason": "webhook must use HTTPS"}))
            return 2

    checks = {
        "readiness": check_readiness(readiness_url, timeout),
        "backup": check_backup(timeout),
    }
    statuses = [check["status"] for check in checks.values()]
    status = "ok" if all(item == "ok" for item in statuses) else (
        "configuration_error" if "configuration_error" in statuses else "critical"
    )
    report: dict[str, object] = {"status": status, "checks": checks}
    if status != "ok" and webhook_url:
        report["alert_sent"] = send_alert(webhook_url, report, timeout)
    print(json.dumps(report, sort_keys=True))
    return 0 if status == "ok" else 2 if status == "configuration_error" else 1


if __name__ == "__main__":
    raise SystemExit(main())
