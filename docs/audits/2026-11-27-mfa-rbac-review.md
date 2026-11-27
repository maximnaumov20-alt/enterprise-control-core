# MFA, authentication and RBAC review — 2026-11-27

## Scope and evidence

Reviewed the consolidated implementation on branch `audit/2026-11-27-followup`, commit
`7dafa3f11a58ba4a5477a6b27981b019e7ffa97b`.

Files inspected:
- `backend/app/main.py`: authentication/session dependency, role dependency, and all 39 route decorators in this module.
- `backend/app/mfa_routes.py`: MFA status, enrollment, confirmation, challenge verification, disable and recovery-code regeneration.
- `backend/app/mfa_models.py`: organization/user foreign keys, uniqueness and check constraints.
- `backend/tests/test_mfa_lifecycle.py` and `backend/tests/test_rbac.py`: lifecycle and role-matrix coverage.

This was a source review only. No tests were run in this review, and this document does not claim that every route or runtime configuration has been independently verified.

## Positive controls observed

- Normal password login creates an `AuthSession` and issues an access token containing its session ID.
- Session-bound tokens are checked against the stored session, user ID, organization ID, token version, revocation timestamp and expiry.
- MFA challenges store a digest of the random challenge token, have a five-attempt ceiling, and expire.
- TOTP replay protection uses a conditional database update on the last accepted counter.
- Recovery codes are stored as password hashes and consumed with a conditional update, preventing a second successful consumption.
- MFA factor, challenge and recovery-code records carry organization/user ownership constraints.
- User, asset, department, employee, maintenance, software-license and audit-event routes inspected apply organization filters or validate same-organization references.
- RBAC tests exercise representative editor and viewer permissions. Tenant-isolation tests exist separately in PR #151.

## Finding: session-less JWTs remain accepted

**Classification:** hardening risk / compatibility behavior; not demonstrated as an externally exploitable path in the current login flow.

In `backend/app/main.py`, `create_access_token(user, session_id=None)` permits creating a token without a session ID. In `get_current_user`, session validation runs only when the decoded token contains `sid`. A correctly signed, unexpired token without `sid` therefore passes user-active and token-version checks without consulting `AuthSession`.

The current password-login path and MFA challenge-verification path both create an `AuthSession` and pass its ID to `create_access_token`. The reviewed production issuance paths therefore use session-bound tokens. However, the optional parameter and conditional validation preserve a session-less-token compatibility path; existing legacy tokens minted before session enforcement could remain valid until expiry and would not be individually revocable through the session table.

**Recommended remediation:**
1. Decide whether legacy session-less tokens must remain supported.
2. If not, require a non-empty `sid` in `get_current_user` and remove the default from `create_access_token`.
3. Update test fixtures that currently mint session-less tokens so they create a matching synthetic `AuthSession`.
4. Add a regression test proving a signed token without `sid` is rejected, plus tests for wrong-user and wrong-organization session IDs.
5. If compatibility is required, define a bounded migration window and a hard cutoff; do not silently retain indefinite session-less acceptance.

No code change was made for this finding because removing the compatibility path affects authentication fixtures and may affect deployments with previously issued tokens. It should be addressed as a deliberate auth migration, not an incidental edit.

## Remaining verification

- Route-by-route review of any routers outside `main.py` is still required; the MFA router was inspected, but other routers may be added later.
- Automated cross-tenant coverage does not prove isolation for every future endpoint or query.
- Concurrent MFA behavior should continue to be exercised against PostgreSQL, not SQLite alone.
- Reverse-proxy trust and rate-limit identity depend on deployment configuration. The application reads `X-Real-IP`; the reviewed deployment configuration must continue to ensure the trusted gateway overwrites client-supplied values and the API is not exposed around that gateway.
- External penetration testing, independent-host backup restoration, production rollout and licensing/IP review remain outstanding.

## CI state at review time

The consolidated PR head had no associated workflow runs or commit statuses when checked. Earlier successful CI runs on PR #151's separate head do not validate this consolidated commit.
