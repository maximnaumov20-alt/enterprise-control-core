# Runtime monitoring

ECC includes a dependency readiness probe and a backup freshness healthcheck. The optional runtime monitor combines both into one machine-readable report and can send a minimal alert to an HTTPS webhook.

## Run

From the repository root on the host or monitoring runner:

```bash
ECC_MONITOR_READINESS_URL=http://127.0.0.1:8000/ready \
ECC_BACKUP_DIR=./backups/postgres \
ECC_BACKUP_MAX_AGE_HOURS=26 \
ECC_BACKUP_REQUIRE_ENCRYPTED=1 \
python3 scripts/monitor-runtime.py
```

The readiness URL must be reachable from the process running the monitor. In a Compose deployment, use the API service name and private Compose network from a monitoring container, or the host-published/reverse-proxy endpoint that is actually reachable. Do not publish the API port publicly just to make monitoring work.

Optional environment variables:

- `ECC_MONITOR_TIMEOUT_SECONDS`: per-check/network timeout; defaults to 5 seconds.
- `ECC_MONITOR_WEBHOOK_URL`: optional HTTPS endpoint. On a failed check, the monitor sends a small JSON alert containing only overall status and check names/statuses. It does not send credentials, exception text, backup paths, or response bodies.
- `ECC_BACKUP_DIR`, `ECC_BACKUP_MAX_AGE_HOURS`, `ECC_BACKUP_REQUIRE_ENCRYPTED`: passed through to the existing backup healthcheck.

## Exit codes and output

- `0`: all checks are healthy.
- `1`: readiness or backup check is critical.
- `2`: invalid monitor or backup-check configuration.

The JSON report includes readiness HTTP status and a limited set of backup metadata when healthy. A webhook delivery failure is exposed as `alert_sent: false`; it does not hide the underlying unhealthy status.

## Scheduling and alerting

Run this command from an external scheduler (systemd timer, cron, or an existing monitoring agent) at an interval appropriate to the deployment. Configure the scheduler to alert on non-zero exit status and monitor the scheduler itself. Avoid running multiple overlapping monitor invocations if the webhook receiver cannot tolerate duplicate alerts.

This is a polling primitive, not a monitoring service: it does not retain state, deduplicate alerts, implement alert recovery notifications, or guarantee delivery. Validate the webhook contract and network path in the target environment before relying on it. The repository's CI tests exercise the monitor offline; they do not prove production network reachability or webhook delivery.
