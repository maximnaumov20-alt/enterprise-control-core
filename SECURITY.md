# Security policy

## Supported versions

Only the latest code on the default branch is considered for security fixes while ECC is in active development. There are no production support or response-time guarantees.

ECC is an early-development project and must not be deployed with sensitive or production data until the production-readiness gates in the documentation have been completed.

## Reporting a vulnerability

Please do not publish exploit details, credentials, personal data or proof-of-concept code in a public issue.

Use GitHub's **Report a vulnerability** action in the repository Security tab when private vulnerability reporting is available. If that option is unavailable, contact the repository maintainer through GitHub and request a private channel before sharing technical details.

A report should include:
- affected commit, release or deployment version;
- affected component and configuration;
- impact and realistic attack prerequisites;
- a minimal reproduction that does not access another person's data;
- any suggested mitigation.

Do not include live secrets or customer data. Rotate any credential that may have been exposed.

## Response expectations

This is a volunteer development project. Receipt, triage, remediation and disclosure timelines are not guaranteed. Please allow maintainers time to validate a report and coordinate a safe fix.

## Disclosure

Please give maintainers a reasonable opportunity to investigate and prepare a fix before public disclosure. Do not perform testing against systems or data you do not own or have explicit permission to assess.
