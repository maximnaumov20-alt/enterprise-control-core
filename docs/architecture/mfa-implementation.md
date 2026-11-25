# Multi-factor authentication implementation design

## Status

Design baseline only. MFA is **not implemented**. Do not describe the product as MFA-protected until the API, UI, migrations and end-to-end tests are merged and verified.

## Initial scope

Implement optional per-user TOTP authentication (RFC 6238) for the Community Edition. Administrators may later enforce MFA for an organization, but organization-wide enforcement is outside the first increment.

## Enrollment lifecycle

1. An authenticated user starts enrollment after re-entering their password.
2. The API creates a short-lived pending enrollment secret. Pending secrets do not enable MFA.
3. The UI displays a provisioning URI and manual setup key once; a QR code may be added as a presentation convenience.
4. The user confirms enrollment with a valid TOTP code.
5. Only after successful confirmation is MFA marked enabled. The API invalidates existing sessions and records an audit event.
6. A user can disable MFA only after password plus a valid TOTP or unused recovery code. Disabling also invalidates all sessions and is audited.

Never return an already-enabled TOTP secret from a read endpoint.

## Login lifecycle

1. Password verification remains the first factor and continues to use the existing login throttling.
2. If MFA is enabled, do not create an AuthSession and do not issue a normal access token after password verification alone.
3. Return a short-lived, purpose-limited MFA challenge that cannot access ordinary API routes.
4. Verify the TOTP code against a small clock-drift window. Consume the accepted time-step so the same code cannot be replayed.
5. Only after successful second-factor verification create the persisted AuthSession and issue the normal access token.
6. Apply shared Redis rate limits to MFA challenge verification, scoped by account and source address. Fail closed if the shared limiter is unavailable.

The challenge must be signed with a distinct token purpose/type and have a very short expiry. The normal authenticated-user dependency must reject MFA challenge tokens.

## Secret storage and key management

- Generate TOTP secrets with a cryptographically secure random generator.
- Encrypt secrets at rest with authenticated encryption from a maintained cryptographic library (AES-GCM or an equivalent reviewed AEAD implementation).
- Use a dedicated ECC_MFA_ENCRYPTION_KEY, separate from ECC_SECRET_KEY. Never reuse the JWT signing key as an encryption key.
- Refuse MFA enrollment or verification when the encryption key is missing, malformed or unavailable. Do not silently store plaintext.
- Document key backup, rotation and recovery before production rollout. Losing the encryption key makes enrolled authenticators unusable.
- Never log TOTP values, provisioning URIs, raw secrets, recovery codes or challenge tokens.

## Recovery codes

- Generate a small set of high-entropy, single-use recovery codes at enrollment confirmation.
- Display them once and require the user to save them.
- Store only password-hashed recovery codes; consume each code atomically on use.
- Regeneration invalidates all previous recovery codes and requires a fresh second-factor proof.
- Recovery-code use must be audited without recording the code itself.

## Data model and audit

A migration will add MFA state to the user or a dedicated one-to-one credential table. Prefer a separate credential table for encrypted secret, enrollment timestamps and replay-prevention counter. Recovery codes should be separate hashed rows with a consumed timestamp.

Audit at least: enrollment started, enrollment completed, MFA login success/failure, recovery code used, MFA disabled and recovery-code regeneration. Audit metadata must never contain credentials or secret material.

## API and UI acceptance criteria

- Enrollment requires the current password and a successful TOTP confirmation.
- A pending enrollment can expire and be safely restarted.
- Password-only login cannot issue a normal access token for an MFA-enabled account.
- MFA challenge tokens cannot call normal business endpoints.
- TOTP replay is rejected, including concurrent duplicate submissions.
- Recovery codes are one-time and cannot be reused concurrently.
- Tenant boundaries and user ownership are enforced for every MFA operation.
- MFA disable and recovery actions invalidate existing sessions.
- The UI supports setup, confirmation, status, disable and recovery-code regeneration.
- Accessible manual-key entry is available even if QR support is added.
- Tests cover RFC 6238 known vectors, drift boundaries, replay, clock skew, key failures, tenant isolation, throttling, migration and full login flows.

## Rollout gates

1. Add and validate the migration and encryption-key configuration.
2. Implement isolated TOTP and AEAD helpers with known-answer tests.
3. Implement enrollment and confirmation endpoints.
4. Implement MFA challenge login and replay protection.
5. Implement hashed one-time recovery codes.
6. Add UI and account security guidance.
7. Run full CI, PostgreSQL migration tests and an independent security review.
8. Enable gradually; do not force organization-wide enforcement until recovery and support procedures are proven.

## Explicit non-goals for the first increment

- SMS or email one-time codes.
- Push approvals.
- Passkeys/WebAuthn.
- Organization-wide mandatory enforcement.
- Administrator bypass of a user's second factor without a documented, audited recovery procedure.
