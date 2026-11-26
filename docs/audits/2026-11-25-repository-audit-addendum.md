# Repository-wide audit addendum

- **Date:** 2026-11-25
- **Repository:** `maximnaumov20-alt/enterprise-control-core`
- **Reviewed main commit:** `16bf045de4142df0c9c65d086c07798edb4a3874`
- **Related tracking issue:** [#142](https://github.com/maximnaumov20-alt/enterprise-control-core/issues/142)
- **Method:** GitHub repository metadata, active ruleset, pull-request metadata and patches, workflow definitions, dependency inputs/locks, README, roadmap, production checklist, selected backend models and Alembic environment.
- **Limitations:** No full local execution, dynamic API fuzzing, penetration test, load test, live cloud backup/restore, production infrastructure validation or legal review was performed. This is not a security certification.

## Executive conclusion

ECC has a substantial development foundation: tenant-scoped business APIs, role-based authorization, PostgreSQL/Alembic, React/TypeScript, Docker Compose, CI/security workflows, dependency lockfiles, session management and backup tooling. It is **not production-ready**. A controlled pilot with synthetic or non-sensitive data is the appropriate next validation stage, subject to deployment controls.

## 1. Main branch and governance

Reviewed main head: `16bf045de4142df0c9c65d086c07798edb4a3874`.

The active repository ruleset **Protect main** (ID `28054326`) is enabled. Its latest readable configuration includes deletion and non-fast-forward protections, pull-request review-thread resolution, **one required approval**, and **seven required status checks**: `test`, `frontend-build`, `docker-build`, `shell-validation`, `Python dependency audit`, `Frontend dependency audit`, and `Container image vulnerability scan`. It also requires branches to be up to date before merging. There are no bypass actors.

**Scope verified:** the current ruleset API response explicitly contains `ref_name.include: ["refs/heads/main"]` and an empty exclude list. The ruleset is therefore scoped to `main`; the earlier scope caveat is resolved. Do not require `archive-recovery-evidence` for PRs because it is skipped on pull-request events.

The connected GitHub integration can read the ruleset but does not expose a ruleset-write operation. No ruleset changes were made by this audit.

## 2. Pull request integration and MFA

At review time, PRs #134, #135, #136, #137, #138, #140 and #141 were open drafts.

- **#135** adds `cryptography` and updates both hash-pinned Python lockfiles. Its CI and Security Scan completed successfully. Until it is merged, the main branch's runtime lock does not include this dependency.
- **#140** adds lockfile verification. Its first verification attempt failed because `pip-tools==7.5.1` accessed the removed `InstallRequirement.use_pep517` attribute. Pinning pip 25.3 did not resolve this. A follow-up commit pins pip 24.3.1; the regenerated runtime lock step has passed on that commit, while the development lock, diff check and hash-locked installation were still running at the time of this addendum refresh. Do not call #140 green until the complete workflow and all seven required checks finish successfully. Main's lock generator and verifier both use pip-tools 7.5.1.
- **#134** and **#141** both add `backend/app/mfa.py`. This is an integration collision: both branches introduce the same module, while #141 is based on #135 rather than main. Consolidate to one canonical TOTP implementation and rebase the encrypted-storage work on the selected dependency/primitive branch before merging.
- **#141** adds AEAD-encrypted TOTP secret storage. Its head is based on #135 rather than main and has no associated workflow runs visible through the current commit check query. Its test claims must not be treated as full-CI-verified until workflows run on the exact current head.
- **#136–#138** remain separate draft changes. Review and merge individually only after checking their latest diffs and CI; do not batch unrelated security changes blindly.

No pull request was merged as part of this audit.

## 3. Authentication and authorization

The current main source contains:
- password hashing through the configured password-hashing library;
- JWT authentication and user token-version checks;
- organization-scoped users and business records;
- admin/editor/viewer roles;
- persisted `AuthSession` records with per-login session IDs, expiry and revocation fields.

The roadmap records individual and account-wide session revocation as implemented. The README's prior wording that selective revocation remains outstanding is therefore stale; refresh-token support and MFA remain separate outstanding items.

A source review is not proof of complete tenant isolation. Continue with endpoint-by-endpoint authorization matrices and negative cross-tenant tests for every object relationship, import/export path and administrative operation.

## 4. Database and migrations

The project uses SQLAlchemy models and Alembic migrations. The Alembic environment imports metadata from `app.main`, which currently concentrates many models and routes in a large module. This raises maintainability and migration-review risk as the application grows.

Recommended follow-up:
- split models, schemas, routers and services by domain without changing public API contracts;
- test upgrade paths from every supported release baseline;
- test PostgreSQL behavior for constraints, transaction races and concurrent revocation;
- require backup verification before production schema upgrades;
- add migration review for both upgrade and downgrade behavior.

## 5. CI, dependencies and software supply chain

Main has:
- an ECC CI workflow covering backend tests, migration checks, frontend build, Docker builds and PostgreSQL Compose smoke tests;
- a security workflow for Python/npm dependency audits, critical container vulnerabilities and CycloneDX image SBOM artifacts;
- a Python lock-generation workflow pinned to pip-tools 7.5.1;
- separate runtime and development Python lockfiles.

The latest verified PR #143 head (`1da7982bf0521684c2e1f559366dfa6209882f93`) passed all seven required checks. ECC CI jobs `test`, `frontend-build`, `docker-build` and `shell-validation` succeeded; `archive-recovery-evidence` was skipped as expected. Security jobs `Python dependency audit`, `Frontend dependency audit` and `Container image vulnerability scan` succeeded. These results apply to the PR head, not to a new commit or to main.

PR #140 adds a lockfile verification job. The first attempt failed with the pip 25.3 / pip-tools 7.5.1 compatibility error. The PR branch now pins pip 24.3.1; runtime lock regeneration has passed, but the remaining steps and full required checks must complete before the control is accepted.

Vulnerability scans and SBOM generation do not replace license/provenance review. The project still needs verified third-party notices, source inventory, release SBOM archival and legal review of the ECC license and contributor rights.

## 6. Frontend quality

CI confirms frontend compilation/build, not browser behavior. The audit did not establish a comprehensive browser E2E suite. Add tests for:
- sign-in, logout, session expiry and revocation;
- admin/editor/viewer permissions;
- tenant-scoped workflows;
- assets, licenses, employees, departments and maintenance requests;
- CSV import/export validation and error states;
- API/network failures and recovery.

## 7. Backup, operations and deployment

The production deployment checklist correctly states that ECC is not production-ready. Source and CI evidence for backup scripts and recovery tests does not prove operational recovery in a real deployment.

Still to be demonstrated:
- scheduled backup execution on the target host;
- external object-storage upload and download with least-privilege IAM;
- isolated restore drill including encryption-key recovery;
- immutable retention and deletion protection;
- measured RPO/RTO;
- alerting for missed/failed backups and unhealthy dependencies;
- TLS termination, firewall/network segmentation and secret-manager integration;
- log retention, incident response and operational ownership.

Do not load sensitive or business-critical customer data until these controls and independent security review are complete.

## 8. Prioritized action plan

1. Keep the active ruleset scoped to `refs/heads/main`; its current API configuration confirms this. The seven required checks, strict up-to-date policy and one approval are present.
2. Resolve #134/#141 duplicate TOTP module and #141's dependency-base relationship.
3. Review and merge #135 only after its latest CI/security results are green; verify main lockfiles afterwards.
4. Align #140's pinned pip-tools version with the main generator and retain reproducibility checks.
5. Complete end-to-end MFA: credential migration, enrollment confirmation, challenge-only login, atomic replay prevention, one-time recovery codes, throttling, session invalidation and PostgreSQL concurrency tests.
6. Add frontend browser-level regression tests.
7. Run a real backup/restore and key-recovery drill; measure RPO/RTO.
8. Complete licensing/provenance review and independent security assessment.

## Final status

**Development foundation:** substantial.
**CI/security automation:** present; seven required checks and one approval are configured in the active ruleset. Ruleset branch-filter scope still needs UI confirmation.
**MFA:** incomplete and not enabled as an end-to-end control.
**Production readiness:** not demonstrated.
**Independent penetration test:** not performed.
