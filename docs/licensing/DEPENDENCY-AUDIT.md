# Third-party dependency and asset audit

**Status: incomplete.** This document is an audit register and release checklist, not a third-party licence notice, legal opinion or redistribution clearance.

## Current evidence

Direct Python dependency inputs are maintained in:
- `backend/requirements-runtime.in`
- `backend/requirements-dev.in`

The corresponding committed, hash-pinned lock files are:
- `backend/requirements-runtime.lock` for the API runtime image
- `backend/requirements-dev.lock` for development and CI tests

The frontend declares direct dependencies in `frontend/package.json` and commits `frontend/package-lock.json`. The frontend Docker build and frontend CI install with `npm ci`; the security workflow also installs the locked graph before running `npm audit`. The Python security workflow audits both committed Python lock files with `pip-audit`. Lockfiles improve repeatability and make the resolved package graph reviewable, but they do not establish licence compatibility or provenance.

The GitHub Security Scan builds both container images, scans for unfixed critical vulnerabilities with Trivy, and generates CycloneDX JSON SBOM artifacts with Syft. The workflow retains these CI artifacts for 30 days. These are CI image inventories, not yet a release-governed SBOM and third-party notices package for the exact artefacts distributed to customers.

Automated vulnerability scans do not establish copyright provenance, licence compatibility, attribution compliance, source-distribution obligations or permission to redistribute every dependency and bundled asset.

## Inventory status

| Area | Source of truth | Current status |
|---|---|---|
| Direct Python packages | `backend/requirements-runtime.in`, `backend/requirements-dev.in` | Declared; exact resolved versions are captured in committed hash-pinned locks |
| Transitive Python packages | `backend/requirements-runtime.lock`, `backend/requirements-dev.lock` | Locked with hashes; component licence review and notices remain incomplete |
| Direct frontend packages | `frontend/package.json` | Declared with semver ranges |
| Transitive frontend packages | `frontend/package-lock.json` | Committed; CI and Docker use `npm ci`; component licence review remains incomplete |
| Container base images and OS packages | Dockerfiles and CI-generated image SBOMs | CI SBOM artifacts are generated; release-specific review and archival remain pending |
| Fonts, icons, images and other bundled assets | Frontend source and build output | Source and permission/provenance review required |
| Generated or copied code | Repository history and source files | Provenance review required |

No package licence is inferred from its name. Verify the actual package metadata, licence expression, included licence files, notices and exceptions against the exact version shipped.

## Required release procedure

Before publishing a Community or Enterprise release:

1. Regenerate Python locks from the committed `.in` inputs using the pinned pip-tools version, review the resulting changes and retain hash verification.
2. Regenerate the frontend lockfile with the repository's documented Node.js/npm toolchain and validate it with `npm ci`.
3. Generate and archive an SPDX or CycloneDX SBOM for the exact release source and container images. CI currently creates provisional CycloneDX image SBOM artifacts; retain release-specific artefacts beyond the short CI retention window.
4. Record each direct and transitive component, exact version, supplier, package URL, declared licence and evidence location.
5. Review licence texts and compatibility for the intended distribution model, including bundled binaries and container OS packages.
6. Record required copyright notices, attribution, source-offer or corresponding-source obligations, and modification notices.
7. Trace fonts, icons, images, templates, copied snippets and generated assets to their origin and permitted use.
8. Resolve unknown, custom, missing, dual or conflicting licence expressions before distribution.
9. Publish a release-specific third-party notices file derived from the verified SBOM and legal review.
10. Archive the SBOM, notices, lockfiles and review record with the release.

## Release gate

Do not describe the dependency audit as complete until the exact release artefacts have been scanned and every shipped component has a reviewed licence/provenance record. Vulnerability scan success and committed lockfiles are not substitutes for that review.
