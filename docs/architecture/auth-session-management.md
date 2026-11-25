# Authentication session management

## Scope

Each successful password login now creates a persisted `auth_sessions` record and embeds its opaque UUID in the access token's `sid` claim. The database stores the session identifier and lifecycle metadata, not the bearer token or password.

The access token remains short-lived. A request using a session-bound token is accepted only when the session belongs to the authenticated user and organization, has not been revoked, has not expired, and carries the same account token version.

## API

- `GET /api/v1/auth/sessions`: list the current account's session records, newest first.
- `DELETE /api/v1/auth/sessions/{session_id}`: revoke one session belonging to the current account. A foreign or unknown session returns 404.
- `POST /api/v1/auth/logout`: revoke only the current persisted session.
- `POST /api/v1/auth/logout-all`: increment the account token version and revoke all currently active persisted sessions.

Session list responses expose identifiers and timestamps only. They do not expose bearer tokens, IP addresses, or user-agent strings.

## Compatibility and rollout

Tokens issued before this migration do not contain `sid`. They remain accepted under the existing account-level token-version checks until they expire or the account token version changes. Calling logout with such a legacy token retains the previous account-wide revocation behavior. New logins always create session-bound tokens.

The migration must be applied before deploying the new API code. The migration creates the session table and indexes; it does not backfill old tokens into synthetic sessions.

## Operational notes

- Session expiry follows `ACCESS_TOKEN_MINUTES` (5–60 minutes, default 20).
- Last-seen timestamps are updated no more than once every five minutes per active session.
- Password changes and account deactivation continue to invalidate all tokens through `token_version`; stale session rows are consequently unusable even if their `revoked_at` is null.
- Expired and revoked rows are retained for auditability. A retention and cleanup policy should be defined before production deployment.
- This change does not yet provide a device-name UI, MFA, refresh tokens, or independent security testing.
