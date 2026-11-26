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
- a dedicated system account `ecc-cleanup` exists and is **not** a member of the `docker` group;
- `sudo` is installed and the exact command below is authorized by a root-owned sudoers file.

The service invokes Docker through a command-scoped sudo rule. It does not grant the service account general Docker socket access or membership in the privileged `docker` group. Keep the sudoers file root-owned and mode `0440`; validate it with `visudo -cf`.

Install and enable:

```sh
sudo useradd --system --no-create-home --shell /usr/sbin/nologin ecc-cleanup
sudo install -o root -g root -m 0440 ops/sudoers.d/ecc-auth-cleanup /etc/sudoers.d/ecc-auth-cleanup
sudo visudo -cf /etc/sudoers.d/ecc-auth-cleanup
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
