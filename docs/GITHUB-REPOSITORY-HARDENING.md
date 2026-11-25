# GitHub repository hardening

## Current configuration (verified 2026-11-25)

Repository configuration is partly outside the source tree. A workflow file, CODEOWNERS file or pull-request template cannot by itself prevent direct pushes to `main`; GitHub administrator settings enforce these rules.

The repository has an active branch ruleset named **Protect main** (ruleset ID `28054326`). Its target is explicitly `refs/heads/main`. The ruleset currently enforces:

- pull requests before merging;
- branch deletion blocked;
- non-fast-forward updates (force pushes) blocked;
- review-thread resolution;
- no required approving review count (currently 0);
- no required status checks yet.

The ruleset's `required_status_checks` list is empty. Therefore CI and security workflows can pass without acting as a merge gate. Do not describe the branch as fully protected until required checks are configured and verified.

The classic branch-protection endpoint returned HTTP 403 to the connected integration during the audit. This is not evidence that the ruleset is inactive; the active ruleset was read successfully from the repository rulesets API.

## Required administrator follow-up

In **Settings → Rules → Rulesets → Protect main**:

1. Keep enforcement set to **Active** and target exactly `refs/heads/main`.
2. Keep **Require a pull request before merging**, **Restrict deletions**, **Block force pushes**, and **Require conversation resolution** enabled.
3. Keep required approvals at 0 while there is no independent reviewer. Once a trusted second reviewer is available, consider requiring one approval and dismissing stale approvals after new commits. Do not create a self-approval requirement that cannot provide independent review.
4. Configure required status checks using the exact check names shown by GitHub after a fresh PR run. The current workflow job names are:
   - ECC CI: `test`, `frontend-build`, `docker-build`, `shell-validation`;
   - ECC Security Scan: `Python dependency audit`, `Frontend dependency audit`, `Container image vulnerability scan`.
5. Do not select `archive-recovery-evidence` as a required PR check: it runs only for successful pushes to `main`, not for pull requests.
6. Consider requiring branches to be up to date only after confirming the resulting merge workflow remains practical.
7. Do not grant routine bypass to administrators or automation. Keep any emergency bypass narrow, documented and audited.

If using classic branch protection instead, configure equivalent settings under **Settings → Branches**. Avoid maintaining conflicting rulesets unless their combined effect is understood.

## Additional repository settings

- Enable private vulnerability reporting in the Security tab if available.
- Enable Dependabot alerts and security updates where supported.
- Enable secret scanning and push protection where supported by the account/repository plan.
- Restrict Actions permissions to read-only by default. Grant write permissions only to workflows that need them.
- Require approval for workflows from first-time contributors.
- Keep the default branch as `main`; remove stale branches after their PRs are merged.
- Review outside collaborators and deploy keys periodically.
- Store deployment credentials only in narrowly scoped GitHub environments or an external secret manager. Never put secrets in repository variables, source files, issue comments or workflow logs.

## Verification after configuration

An administrator should verify that:

- the ruleset remains Active and explicitly targets `refs/heads/main`;
- a direct push to `main` is rejected;
- a PR cannot merge while a required check is failing or pending;
- force-push and branch deletion are rejected;
- required checks correspond to current workflow job names and are not accidentally optional;
- the selected checks actually run for pull requests and do not refer to push-only jobs.

Record the ruleset URL, date, administrator and a screenshot/export of the effective rules. Recheck after workflow job names change.

## Scope

This document is a configuration runbook and a record of the ruleset state observed on 2026-11-25. It is not proof that every intended merge gate is active. The active ruleset currently protects the target branch against deletion, force-push and direct non-PR changes, but required CI/security checks still need to be added.
