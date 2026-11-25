# Scheduled encrypted backups with systemd

These units schedule the existing backup cycle; they do not provision AWS, Docker, age identities, or the host account.

## Prerequisites

- Linux host with systemd, Docker Engine/Compose, AWS CLI credentials, `age`, and `flock`.
- ECC checked out at `/opt/enterprise-control-core`.
- A dedicated host account named `ecc-backup` that can access the Docker Compose project and the required AWS credentials. Docker-group membership grants broad host privileges; prefer a narrowly scoped root-owned wrapper/policy if your environment supports it.
- A valid age recipient and an S3 bucket/prefix with least-privilege write/read permissions.

## Install

1. Create the service account and directories using your host's account-management policy.
2. Ensure the account can read the repository and run the required Docker Compose commands. Make the backup directory writable only by the service account.
3. Copy `ops/systemd/backup.env.example` to `/etc/ecc/backup.env`, replace every placeholder, and restrict it:
   ```sh
   sudo install -d -m 0750 -o root -g ecc-backup /etc/ecc
   sudo install -m 0640 -o root -g ecc-backup ops/systemd/backup.env.example /etc/ecc/backup.env
   sudoedit /etc/ecc/backup.env
   ```
4. Review paths and the `User`, `Group`, and writable directories in `ecc-backup.service`. The default unit assumes the repository is at `/opt/enterprise-control-core`.
5. Install and enable:
   ```sh
   sudo install -m 0644 ops/systemd/ecc-backup.service /etc/systemd/system/ecc-backup.service
   sudo install -m 0644 ops/systemd/ecc-backup.timer /etc/systemd/system/ecc-backup.timer
   sudo systemctl daemon-reload
   sudo systemctl enable --now ecc-backup.timer
   ```

## Verify and operate

- Inspect schedule: `systemctl list-timers ecc-backup.timer`
- Run a supervised test: `sudo systemctl start ecc-backup.service`
- Inspect result: `systemctl status ecc-backup.service`
- Read logs: `journalctl -u ecc-backup.service --since today`
- Check local freshness: run `ECC_BACKUP_DIR=... ECC_BACKUP_MAX_AGE_HOURS=26 ECC_BACKUP_REQUIRE_ENCRYPTED=1 python scripts/check-backup-freshness.py` as the service account.
- Verify an archive with `ECC_BACKUP_AGE_IDENTITY=/secure/path/identity.txt bash scripts/verify-backup-postgres.sh /path/to/archive.dump.age`.

The timer uses a persistent daily schedule at 02:15 host-local time with up to 15 minutes of randomized delay. A missed run is triggered after the host returns. `run-backup.sh` uses `flock` to reject overlapping runs.

## Security and limitations

- Keep the age private identity separate from the backup host where possible. The environment template contains only the public recipient, never the private identity.
- Do not put AWS secret keys or age private identities in the repository or in the environment file. Use the host's approved credential provider (for example, an instance role or protected AWS credentials file).
- Review the systemd sandbox against the host's Docker socket and credential-provider paths. Docker socket access is effectively privileged; systemd hardening does not make an untrusted Docker operator safe.
- Alerting is not configured by these units. Connect service failures and freshness-check failures to your monitoring system.
- This is an installation template, not proof of a production backup schedule. Perform a real encrypted upload, download, and isolated restore drill, then measure RPO/RTO.
