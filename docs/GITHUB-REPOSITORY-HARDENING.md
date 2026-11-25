# GitHub repository hardening

## Current limitation

Repository configuration is partly outside the source tree. A workflow file, CODEOWNERS file or pull-request template cannot by itself prevent direct pushes to `main`. GitHub branch rules must be enabled in repository settings by an administrator.

The repository was inspected on 2026-11-25. At that time, the `main` branch reported `protected: false` and no required status checks. This is a material governance risk: successful CI runs are informative but are not a merge gate.

## Required administrator configuration

In **Settings → Rules → Rulesets**, create an active branch ruleset targeting the default branch (`main`):

1. Require a pull request before merging.
2. Require at least one approval from a person other than the PR author. Do not count the author's own approval.
3. Dismiss stale approvals when new commits are pushed.
4. Require approval of the most recent reviewable push where available.
5. Require all applicable CI and security checks to pass before merging. Select the actual check names shown by GitHub after a fresh PR run; at minimum include the backend test job, frontend build, Docker build, Python dependency audit, frontend dependency audit and container image vulnerability scan.
6. Require conversation resolution.
7. Require branches to be up to date before merging, if the repository's workflow duration and team size make this practical.
8. Block force pushes and branch deletion.
9. Do not grant routine bypass to administrators or automation. Keep any emergency bypass narrow, documented and audited.
10. Enable signed-commit enforcement only after all contributors can reliably sign commits.

If using classic branch protection instead, configure equivalent settings under **Settings → Branches**.

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
- a direct push to `main` is rejected;
- a PR cannot merge while a required check is failing or pending;
- an author cannot satisfy the approval requirement with their own review;
- force-push and branch deletion are rejected;
- required checks correspond to current workflow job names and are not accidentally optional.

Record the ruleset URL, date, administrator and a screenshot/export of the effective rules. Recheck after workflow job names change.

## Scope

This document is a configuration runbook, not proof that protections are active. Until the administrator settings above are applied and verified, treat `main` as unprotected.
