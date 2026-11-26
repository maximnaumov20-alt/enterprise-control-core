# Expired authentication artifact cleanup

The API does not run an in-process scheduler. Cleanup is an explicit, idempotent command so multiple API workers cannot accidentally create competing timer loops.

## What is removed

`python -m app.cleanup_expired_auth` deletes:

- MFA login challenges whose `expires_at` is at or before the current UTC time, including already-consumed challenges after their expiry;
- MFA factors that are still `pending` and whose `pending_expires_at` is at or before the current UTC time.

Enabled MFA factors, recovery codes, authentication sessions, users, and audit events are not touched. The command reports deletion counts and can safely be run more than once.

## systemd deployment

The supplied units assume:

- ECC is installed at `/opt/enterprise-control-core`;
- the API is running as the Compose service `api`;
- a host account `ecc-backup` can access the Docker socket through the `docker` group.

Docker socket access is privileged. Use a dedicated host account and follow the host's Docker access policy; do not grant this access to an untrusted account.

Install and enable:

```sh
sudo install -m 0644 ops/systemd/ecc-auth-cleanup.service /etc/systemd/system/
sudo install -m 0644 ops/systemd/ecc-auth-cleanup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ecc-auth-cleanup.timer
```

Verify:

```sh
systemctl list-timers ecc-auth-cleanup.timer
sudo systemctl start ecc-auth-cleanup.service
systemctl status ecc-auth-cleanup.service
journalctl -u ecc-auth-cleanup.service --since today
```

The timer runs approximately every 15 minutes. A missed run is recovered after boot. For non-systemd deployments, schedule the same command with the platform's protected scheduler. Do not expose a public HTTP cleanup endpoint.
