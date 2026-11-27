# MFA lifecycle API and operational notes

This document describes the first server-side MFA lifecycle implementation.

## Enrollment

1. An authenticated user calls `POST /api/v1/auth/mfa/enrollment`.
2. The API creates or renews a pending factor with a ten-minute expiry and returns the TOTP secret and provisioning URI once to the authenticated caller.
3. The user confirms with `POST /api/v1/auth/mfa/enrollment/confirm`.
4. Only a valid six-digit TOTP code activates the factor. The accepted counter is stored immediately so that the enrollment code cannot be reused for login.
5. Ten recovery codes are returned once. Only password hashes are stored.

## Login

1. Password login remains rate-limited.
2. If no enabled factor exists, the existing bearer-token flow is preserved.
3. If MFA is enabled, password login creates a five-minute, one-use challenge and does not issue an access token.
4. `POST /api/v1/auth/mfa/challenge/verify` accepts either a fresh TOTP code or an unused recovery code.
5. A successful challenge creates the authenticated session and access token. Challenge attempts are capped and rate-limited.

## Replay and tenant isolation

- TOTP acceptance uses a conditional database update requiring the accepted counter to be greater than the last stored counter. This is the replay and concurrent-use guard.
- Factor, challenge and recovery-code rows carry both organization and user identifiers and use composite foreign keys to the same-organization user key.
- Challenge tokens are stored only as SHA-256 digests.
- Recovery codes are stored only as password hashes and consumed with a conditional update.

## Disabling MFA

An authenticated user must re-enter the current password and provide a fresh TOTP or unused recovery code. Disabling MFA increments the user's token version and revokes active sessions.

## Operational requirements and limitations

- Configure `ECC_MFA_ENCRYPTION_KEY` and store it in the deployment secret manager. Keep previous keys until ciphertext and retained backups no longer depend on them.
- Configure `REDIS_URL` in multi-worker/production deployments so the MFA rate limiter is shared. Redis errors fail closed. Without Redis, the current limiter falls back to process-local memory and is not a multi-worker production control.
- Run Alembic migrations before deploying the API.
- The current PR does not yet include frontend enrollment/login screens, PostgreSQL concurrency tests, challenge/recovery cleanup jobs, a managed key-rotation job, end-to-end browser tests, or an independent security review.
- Do not enable this feature in production until those items and backup/restore procedures have been validated.
