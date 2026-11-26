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
