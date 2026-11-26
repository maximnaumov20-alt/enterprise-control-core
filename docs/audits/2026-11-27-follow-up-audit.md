# ECC: follow-up technical audit

- **Date:** 2026-11-27
- **Repository:** `maximnaumov20-alt/enterprise-control-core`
- **Baseline:** PR #147 head `32719a3a9f3622ca82cb6e71ff1ab4b621546d1d`
- **Scope:** current open PR stack, latest CI/security/E2E results, workflow trigger coverage, MFA implementation, backup/restore controls, README and production-readiness claims.
- **Limitations:** GitHub source and workflow metadata review only. No live deployment, production data, cloud credentials, S3 account, independent penetration test, or destructive restore was used.

## Executive summary

The MFA implementation and its automated checks are present in the open stacked PR series, but they are **not merged into `main`**. The latest PR #147 head has successful ECC CI, browser E2E and Security Scan runs. This is a useful validation milestone, not a production approval.

The repository remains **not production-ready**. The most important outstanding evidence is a real isolated restore drill from an off-host encrypted backup, independent security review, verified branch protection, release/licensing governance, and a complete review of all PR-stack merge dependencies.

## 1. Repository and PR state

The current development stack is:

| PR | Purpose | Base | State at audit |
|---|---|---|---|
| [#140](https://github.com/maximnaumov20-alt/enterprise-control-core/pull/140) | Reproducible hashed Python lockfiles | `main` | Open, not merged |
| [#141](https://github.com/maximnaumov20-alt/enterprise-control-core/pull/141) | Encrypted TOTP secret storage | #140 branch | Open, not merged; GitHub reported mergeable=false |
| [#144](https://github.com/maximnaumov20-alt/enterprise-control-core/pull/144) | MFA lifecycle/API/recovery | #141 branch | Open, not merged |
| [#145](https://github.com/maximnaumov20-alt/enterprise-control-core/pull/145) | Frontend MFA flow | #144 branch | Open, not merged |
| [#146](https://github.com/maximnaumov20-alt/enterprise-control-core/pull/146) | PostgreSQL concurrency, browser E2E, cleanup | #145 branch | Open, not merged; Draft |
| [#147](https://github.com/maximnaumov20-alt/enterprise-control-core/pull/147) | Roadmap/README and CI trigger synchronization | #146 branch | Open, not merged; Draft |

**Merge order matters.** These PRs are intentionally stacked. Do not merge a downstream PR before its base dependency. PR #141's `mergeable=false` needs investigation after #140 is resolved; it must not be treated as an approval or silently bypassed. No PR was merged and `main` was not modified by this follow-up.

The README on `main` still describes MFA as outstanding. PR #147 corrects that statement on the development stack and explicitly says MFA has not yet been merged into `main`.

## 2. Latest CI and security evidence

For PR #147 head `32719a3a9f3622ca82cb6e71ff1ab4b621546d1d`, these runs completed successfully:

- [ECC CI #591](https://github.com/maximnaumov20-alt/enterprise-control-core/actions/runs/45796635520): test, frontend build, Docker build and shell validation passed. The `archive-recovery-evidence` job was **skipped**, not passed.
- [Frontend browser E2E #21](https://github.com/maximnaumov20-alt/enterprise-control-core/actions/runs/45796635339): browser E2E passed.
- [ECC Security Scan #415](https://github.com/maximnaumov20-alt/enterprise-control-core/actions/runs/45796635683): Python dependency audit, frontend dependency audit and container scan jobs passed.

The workflow branch filters were expanded so the stacked PR base receives CI/E2E/security checks. The latest head now has successful runs, resolving the earlier absence of visible checks.

**Coverage limitation:** the Trivy image workflow scans vulnerabilities with `--ignore-unfixed --severity CRITICAL`. A successful run therefore does not mean that no HIGH vulnerabilities, fixed-but-still-installed vulnerable packages, secrets, misconfiguration, or application-level security defects exist. Dependency audit success is limited to the lockfiles and package audit tools used in that run.

## 3. MFA security review

The current PR stack contains:
- AES-256-GCM encrypted TOTP secrets, organization/user-bound associated data and versioned envelopes;
- strict startup validation of active and previous encryption keys;
- enrollment and login challenge lifecycle, TOTP replay prevention and one-time recovery codes;
- rate limiting designed to fail closed unless local fallback is explicitly enabled for isolated development/tests;
- frontend QR generation locally in the browser, enrollment/challenge screens and recovery-code handling;
- PostgreSQL concurrency and browser E2E coverage.

These controls are positive implementation evidence. Remaining review items:
1. Validate key backup, rotation and recovery procedures operationally; losing the encryption key can make stored MFA factors unusable.
2. Independently review session revocation, recovery-code consumption, concurrency behavior, tenant boundaries and rate-limit proxy configuration.
3. Confirm deployment always uses a shared rate-limit backend and never enables the local fallback in multi-worker or production environments.
4. Review enrollment secret exposure through browser memory, logs, telemetry, browser extensions and support workflows.

No independent penetration test was performed.

## 4. Backup and restore

Source review confirms:
- PostgreSQL custom-format backups are checked with `pg_restore --list`;
- optional age encryption is available, with plaintext backup warning when encryption is not configured;
- the S3 uploader refuses plaintext archives, records SHA-256 metadata, checks object size and downloads the object back to compare actual bytes;
- the S3 downloader verifies checksum/size and uses an atomic no-clobber publication step;
- the restore script requires explicit `ECC_RESTORE_CONFIRM=YES`, requires the expected database name `ecc`, validates the archive, and uses a single transaction with `--exit-on-error`.

**Confirmed operational gap:** these source-level controls and mocked CI tests do not prove that a backup can be decrypted and restored on a separate host. There is no verified live off-host restore drill in this audit. S3 bucket versioning, Object Lock, lifecycle policies, access policies and age-key recovery remain external infrastructure responsibilities.

Do not run the restore script against a real or shared database as part of routine CI. The next safe engineering increment should be an isolated disposable-PostgreSQL restore drill with generated test data and no cloud credentials, followed by a separately authorized off-host exercise.

## 5. Confirmed gaps and risks

| Finding | Classification | Impact / next action |
|---|---|---|
| MFA changes are still in stacked open PRs, not `main` | Confirmed release-state gap | Preserve merge order; resolve #140 first and investigate #141 mergeability before downstream merges |
| #146 and #147 are Draft | Confirmed workflow state | Do not treat them as review-ready |
| Archive recovery evidence job skipped on latest ECC CI | Confirmed validation gap | Determine skip condition and run only when the required isolated test inputs are available |
| Trivy checks only unfixed CRITICAL vulnerabilities | Confirmed scan-scope limitation | Consider HIGH+CRITICAL policy and separate secrets/misconfiguration scans |
| No live off-host decrypt-and-restore drill verified | Confirmed operational gap | Perform in disposable environment, then controlled separate-host drill |
| Branch protection/ruleset enforcement not verified in this audit | Unverified control | Inspect repository rulesets and required status checks; do not claim `main` is protected |
| Independent security review and penetration test absent | Confirmed outstanding work | Obtain independent review before sensitive pilot |
| Licensing, third-party notices and release SBOM archival incomplete | Confirmed governance gap from roadmap | Complete provenance/legal review and archive release artifacts |

## 6. Prioritized next work

1. **PR stack integrity:** resolve #140 checks/review; inspect why #141 is reported non-mergeable; update downstream bases only in the intended merge sequence.
2. **Safe restore drill:** build a disposable PostgreSQL test harness that restores only generated CI data, validates schema and records, and always tears down its isolated container/volume.
3. **Security scan policy:** evaluate raising Trivy from unfixed CRITICAL to HIGH+CRITICAL and add separate secret/misconfiguration checks without weakening existing checks.
4. **Repository governance:** verify `main` ruleset/branch protection and required checks through repository settings; record evidence, not assumptions.
5. **Release readiness:** independent security review, operational backup/key recovery, licensing and release SBOM/provenance.

## 7. Readiness estimate

The previous audit's 40–45% commercial-MVP estimate should not be mechanically carried forward. The MFA feature set has advanced in an open PR stack, but the latest validation does not close deployment, independent review, restore, governance or licensing gaps. A defensible updated percentage requires a weighted acceptance checklist and evidence for each criterion; this audit therefore does **not** invent a new readiness percentage.

**Conclusion:** continue development on feature branches and stacked PRs. Keep `main` unchanged until the PR chain is reviewed, merge dependencies are resolved, and required checks and governance controls are verified.


## Addendum: latest PR #146 head observed after initial audit

The PR #146 head subsequently advanced to `8b6350626cb68fb26185e3164af65bfda661e8c0`. The following newer workflow evidence supersedes the older #146 references above:

- [ECC CI #593](https://github.com/maximnaumov20-alt/enterprise-control-core/actions/runs/45852452138): **success**. API tests, migrations, frontend build, Docker build/Compose smoke test and shell validation passed. The Docker job's **Upload recovery drill evidence** step passed, and the test job's **Run backup and restore regression tests** step passed. The separate `archive-recovery-evidence` job was **skipped**; do not describe the whole recovery validation as skipped, but also do not treat that separate job as passed.
- [Frontend browser E2E #22](https://github.com/maximnaumov20-alt/enterprise-control-core/actions/runs/45852462930): **success**.
- [MFA PostgreSQL concurrency #23](https://github.com/maximnaumov20-alt/enterprise-control-core/actions/runs/45852463421): **success**.
- No Security Scan run for this newer head was returned by the commit-associated workflow query. The successful Security Scan listed in section 2 remains evidence for the older PR #147 head only; it must not be attributed to #146's newer SHA.

This newer CI adds meaningful automated backup/restore regression coverage, but it still does not establish a real off-host restore drill with separately stored encryption keys and an independent recovery environment. That operational gap remains open.

The earlier audit baseline and PR-state table are a dated snapshot, not a live PR status. Recheck PR heads and mergeability immediately before any merge decision. In particular, PR #146's current head and the earlier #141 mergeability observation must not be conflated.


## Addendum: verification on 2026-11-27

This addendum supersedes earlier dated PR-state observations where they differ. It records GitHub state observed on 2026-11-27 and does not authorize merging.

### Current PR stack and dependencies

All PRs below were open and unmerged when checked. GitHub reported each as mergeable at the time of this check:

| PR | Base branch | Head branch | Head SHA | State |
|---|---|---|---|---|
| [#140](https://github.com/maximnaumov20-alt/enterprise-control-core/pull/140) | `main` | `chore/verify-python-lockfiles` | `5dd7da106800fe72062c60100aa74845d9d25aea` | Open, ready for review |
| [#141](https://github.com/maximnaumov20-alt/enterprise-control-core/pull/141) | `chore/verify-python-lockfiles` | `feat/mfa-encrypted-secret-storage` | `55678e36da5adf19d28153a69feec1c0d6691d30` | Open, ready for review |
| [#144](https://github.com/maximnaumov20-alt/enterprise-control-core/pull/144) | `feat/mfa-encrypted-secret-storage` | `feat/mfa-enrollment-login-recovery` | `3f5752ad03dfe92e33d96b3766560f01ded99e04` | Open, ready for review |
| [#145](https://github.com/maximnaumov20-alt/enterprise-control-core/pull/145) | `feat/mfa-enrollment-login-recovery` | `feat/mfa-frontend-flow` | `e46a095d6f76a947b365b2d564e6b99faa7d06ed` | Open, ready for review |
| [#146](https://github.com/maximnaumov20-alt/enterprise-control-core/pull/146) | `feat/mfa-frontend-flow` | `test/mfa-e2e-cleanup-hardening` | `8b6350626cb68fb26185e3164af65bfda661e8c0` | Open, Draft |
| [#147](https://github.com/maximnaumov20-alt/enterprise-control-core/pull/147) | `test/mfa-e2e-cleanup-hardening` | `docs/refresh-mfa-roadmap-status` | `32719a3a9f3622ca82cb6e71ff1ab4b621546d1d` | Open, Draft |
| [#148](https://github.com/maximnaumov20-alt/enterprise-control-core/pull/148) | `docs/refresh-mfa-roadmap-status` | `audit/2026-11-27-followup` | `87bb18dc608a7f577c731fe0cd14f9eb9ad821bf` | Open, Draft |
| [#149](https://github.com/maximnaumov20-alt/enterprise-control-core/pull/149) | `test/mfa-e2e-cleanup-hardening` | `security/high-critical-container-scan` | `96b2c95236105250361c609e698bd3168ca1547c` | Open, Draft |
| [#150](https://github.com/maximnaumov20-alt/enterprise-control-core/pull/150) | `test/mfa-e2e-cleanup-hardening` | `test/mfa-challenge-lockout-regression` | `a7a15bab1b7d0abdc58ee67a8cc735d50700eaf4` | Open, Draft |

The earlier statement that PR #141 was non-mergeable is stale: the current GitHub metadata now reports it as mergeable. This is not a review approval and does not remove the stacked dependency order. PRs #149 and #150 are parallel additions based on #146, not steps in the #140 → #147 linear chain. Do not merge any PR without its required base changes and review.

### Latest CI and security results

- [PR #149 ECC CI run 45910760485](https://github.com/maximnaumov20-alt/enterprise-control-core/actions/runs/45910760485): all four substantive jobs succeeded — API tests/migrations/backup regression, frontend build, Docker build/Compose smoke checks and shell validation. The separate `archive-recovery-evidence` job was skipped.
- [PR #149 Security Scan run 45910760626](https://github.com/maximnaumov20-alt/enterprise-control-core/actions/runs/45910760626): Python dependency audit, frontend dependency audit and container image vulnerability scan all succeeded. PR #149 expands Trivy coverage to unfixed HIGH and CRITICAL findings for both API and frontend images.
- [PR #150 ECC CI run 45911111854](https://github.com/maximnaumov20-alt/enterprise-control-core/actions/runs/45911111854): frontend build, Docker build/Compose smoke checks, shell validation, backup/restore regression tests, API tests and migration verification all succeeded. The separate `archive-recovery-evidence` job was skipped.
- PR #150 adds regression tests for the five-attempt MFA challenge limit and expired challenge rejection. No Security Scan run was verified for PR #150; its change is test-only, and the successful PR #149 Security Scan must not be attributed to PR #150.

These runs are evidence for their exact PR heads only. They do not prove that the same workflow policy is already active on `main`, nor do they constitute an off-host disaster-recovery exercise.

### Main branch governance verified

The repository has an active GitHub ruleset named **Protect main**, targeting `refs/heads/main`. The ruleset currently enforces:
- no branch deletion and no non-fast-forward updates;
- pull requests with at least one approving review;
- dismissal of stale approvals on new pushes;
- resolution of review conversations;
- strict required status checks;
- no bypass actors.

The configured required status contexts are:
`test`, `frontend-build`, `docker-build`, `shell-validation`, `Python dependency audit`, `Frontend dependency audit`, and `Container image vulnerability scan`.

This confirms that a ruleset is active. It does not by itself prove every required context is emitted by every workflow event or that every PR in the stack has satisfied the full merge policy. Check the exact head's required checks before any merge.

### Confirmed remaining release blockers

1. MFA and supporting hardening remain in unmerged PRs; `main` does not yet contain those changes.
2. The real off-host encrypted backup recovery drill remains unverified. CI regression tests and upload evidence are not a substitute for restoring on an independent host with separately recoverable keys.
3. Independent security review and a complete route-by-route tenant-isolation review remain outstanding.
4. The dependency/provenance audit is incomplete; the commercial licence remains a draft and no root `LICENSE` file was present on `main` at this check.
5. The production deployment checklist still describes the product as not production-ready. No real customer pilot, measured RPO/RTO, load test or operational support process has been evidenced.

### Updated readiness assessment

The current development stack is estimated at approximately **55–60% toward a controlled commercial MVP**, while `main` remains approximately **40–45%** because the feature stack is not merged. These are engineering judgement ranges, not measured metrics or security certification. Commercial readiness remains lower than feature completeness until the release blockers above are addressed.

No PR was merged and no change was made to `main` during this addendum.


## Addendum: PR #151 tenant-isolation regression validation (2026-11-27)

PR [#151](https://github.com/maximnaumov20-alt/enterprise-control-core/pull/151) is open, Draft, unmerged, and based on PR #146's branch. Its head is `c8da26d8aa692fe024eea9826cc26fea3835d287`. GitHub reported it mergeable when checked; this is not approval to merge.

- [ECC CI run 45912323540](https://github.com/maximnaumov20-alt/enterprise-control-core/actions/runs/45912323540): **success**.
- `shell-validation`: passed, including maintenance script lint and mocked backup/S3 orchestration checks.
- `frontend-build`: passed.
- `docker-build`: passed, including API/frontend image builds, trusted proxy checks, PostgreSQL Compose smoke test, and upload of recovery drill evidence.
- `test`: passed, including backup/restore regression tests, API tests, and migration verification.
- Separate `archive-recovery-evidence` job: **skipped**. This does not negate the successful Compose smoke test or the recovery-evidence upload step inside `docker-build`.

PR #151 adds a two-organization API regression suite covering tenant-scoped listing and access restrictions for assets, departments, employees, software licences, maintenance requests and users, foreign-tenant resource references, and audit-event visibility. The tests use synthetic records and an isolated in-memory SQLite database. This is useful regression coverage, but it is **not** PostgreSQL-specific tenant-isolation evidence and does not replace a complete route-by-route authorization review. The PostgreSQL Compose smoke test in the same CI run is a separate workflow check; it should not be described as running this new test suite against PostgreSQL.

### Updated status and remaining work

- The PR #151 test suite and all substantive jobs in its associated ECC CI run passed on the exact head above.
- The tenant-isolation review remains partial: expand coverage to remaining tenant-owned endpoints and verify critical cross-tenant cases against PostgreSQL before calling the review complete.
- PR #151 remains Draft and unmerged. It is a parallel branch based on #146, not a new step in the #140 → #147 linear stack.
- No PR was merged and `main` was not changed by this addendum.


## Addendum: PostgreSQL tenant-isolation validation started (2026-11-27)

PR [#151](https://github.com/maximnaumov20-alt/enterprise-control-core/pull/151) was extended so the same two-organization API isolation test runs against both SQLite and a dedicated PostgreSQL test database. The PostgreSQL fixture validates the configured database name (`ecc_test` or `test`), creates a UUID-scoped schema, and drops only that schema during teardown. Synthetic test records only; no production database or credentials are used.

- Updated PR #151 head: `c0c027030837f264e8ef247baba0116ffe903dc2`.
- [PostgreSQL security regression run 45912829641](https://github.com/maximnaumov20-alt/enterprise-control-core/actions/runs/45912829641): PostgreSQL service initialized; dependencies installed; **Run PostgreSQL MFA and tenant-isolation regressions passed**. The subsequent expired-MFA-cleanup test step was still running when this addendum was written, so the overall workflow result is not yet final.
- The workflow now triggers for PRs targeting `test/mfa-e2e-cleanup-hardening` and pushes to `test/tenant-isolation-regression`.
- The PR description was corrected to distinguish the earlier completed SQLite/standard CI run from the new PostgreSQL validation, which is still in progress.

This provides direct PostgreSQL-backed regression evidence for the tested endpoints, but not a complete route-by-route tenant-isolation review. Other tenant-owned routes and permission combinations remain to be inventoried and tested. PR #151 remains Draft and unmerged. No change was made to `main`.


### Final result for PostgreSQL security regression workflow

The two runs triggered for PR #151's updated head both completed successfully:

- [Pull-request run 45912829641](https://github.com/maximnaumov20-alt/enterprise-control-core/actions/runs/45912829641): **success**. PostgreSQL MFA and tenant-isolation regressions passed; expired MFA artifact cleanup tests passed.
- [Push run 45912825035](https://github.com/maximnaumov20-alt/enterprise-control-core/actions/runs/45912825035): **success**, with the same test steps passed.

This supersedes the earlier in-progress note above. The dedicated PostgreSQL tenant-isolation regression now has successful CI evidence on PR #151 head `c0c027030837f264e8ef247baba0116ffe903dc2`. This remains automated coverage of the specific tested API surface, not proof that every tenant-aware route or authorization combination has been reviewed.


### Additional tenant-isolation cases and CI correction

The endpoint inventory identified asset bulk update, CSV export and CSV import as tenant-sensitive routes not covered by the first PR #151 test version. Regression cases were added for:
- rejecting a bulk update that includes another organization's asset ID;
- ensuring CSV export does not disclose another organization's asset data;
- ensuring CSV import creates records only in the authenticated organization's scope;
- verifying the imported record is not visible to the other organization;
- confirming audit events remain organization-scoped after import.

The first run of these additional cases failed because the test supplied an unsupported asset status (`maintenance`); the API correctly rejected the invalid enum with HTTP 422 before tenant authorization was exercised. The test fixture was corrected to the supported `in_repair` value. This was a test-data defect, not evidence of an application authorization defect.

Updated PR #151 head: `282c41041a43ef5e0e4277ef241d71d353303986`. Fresh ECC CI and PostgreSQL workflow runs were triggered:
- [ECC CI run 45913092033](https://github.com/maximnaumov20-alt/enterprise-control-core/actions/runs/45913092033): frontend build and shell validation passed; API tests and Docker build/Compose checks were still running when this note was written.
- [PostgreSQL run 45913092041](https://github.com/maximnaumov20-alt/enterprise-control-core/actions/runs/45913092041): queued/running; final result pending.

The earlier PostgreSQL success on head `c0c0270` does not validate these newly added bulk/CSV cases. Do not mark the expanded tenant-isolation suite complete until the fresh runs for `282c410` finish successfully.


### Expanded tenant-isolation suite: PostgreSQL result

The corrected expanded suite on PR #151 head `282c41041a43ef5e0e4277ef241d71d353303986` passed against PostgreSQL:

- [PostgreSQL security regression run 45913092041](https://github.com/maximnaumov20-alt/enterprise-control-core/actions/runs/45913092041): the combined MFA concurrency + tenant-isolation test step passed, and expired MFA cleanup tests passed.
- The passing tenant suite now includes cross-tenant bulk asset update rejection, CSV export non-disclosure, tenant-scoped CSV import, and visibility checks in addition to the earlier resource-level cases.
- [ECC CI run 45913092033](https://github.com/maximnaumov20-alt/enterprise-control-core/actions/runs/45913092033) was still running its API tests and PostgreSQL Compose smoke test when this addendum was written. Frontend build and shell validation had passed.

The first expanded run's failure was confined to an invalid test enum value; the corrected run passed. This is not a confirmed application defect. The overall ECC CI result for the current head remains pending, and a complete route-by-route authorization review remains outstanding.


### Final ECC CI result for expanded tenant-isolation cases

The full ECC CI run for PR #151 head `282c41041a43ef5e0e4277ef241d71d353303986` completed successfully:

- [ECC CI run 45913092033](https://github.com/maximnaumov20-alt/enterprise-control-core/actions/runs/45913092033): `test`, `frontend-build`, `docker-build` and `shell-validation` all **success**. The API tests, migrations, frontend build, Docker image builds, trusted-proxy checks, PostgreSQL Compose smoke test, backup/restore regression checks and shell validations passed.
- The separate `archive-recovery-evidence` job was **skipped**. The Docker job's recovery-drill evidence upload step passed; these are distinct workflow steps.
- [PostgreSQL security regression run 45913092041](https://github.com/maximnaumov20-alt/enterprise-control-core/actions/runs/45913092041) also completed successfully, including the expanded tenant-isolation suite and expired MFA cleanup tests.

These results apply to the exact PR #151 head above. The PR remains Draft and unmerged; the broader route-by-route authorization review and independent security assessment remain open.
