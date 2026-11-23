# Third-party dependency and asset audit

**Status: incomplete. This is an audit register and release checklist, not a third-party licence notice or legal clearance.**

## Current evidence

The repository currently declares direct dependencies in:
- backend/requirements.txt
- frontend/package.json

At the time of this review, the repository tree contains no committed Python lock file, npm lock file, or generated software bill of materials (SBOM). Python requirements use version ranges, and frontend dependencies use semver ranges. The exact transitive dependency graph can therefore change between builds.

The GitHub Security Scan checks known vulnerabilities with pip-audit, npm audit and Trivy. These checks do not establish copyright provenance, licence compatibility, attribution compliance, or permission to redistribute every dependency.

## Inventory status

| Area | Source of truth | Current status |
|---|---|---|
| Direct Python packages | backend/requirements.txt | Declared; exact resolved versions and licence texts not recorded here |
| Transitive Python packages | Resolved installation graph | Not pinned or fully inventoried |
| Direct frontend packages | frontend/package.json | Declared; exact resolved versions and licence texts not recorded here |
| Transitive frontend packages | npm dependency graph | No committed lockfile; not reproducible from a committed resolution |
| Container base images and OS packages | Dockerfiles | Must be inventoried per image digest and release |
| Fonts, icons, images and other bundled assets | Frontend source and build output | Requires source/provenance review before release |
| Generated or copied code | Repository history and source files | Requires provenance review |

No package licence is inferred from its name. Verify the actual package metadata, licence expression, included licence files, notices and any exceptions against the exact version shipped.

## Required release procedure

Before publishing a Community or Enterprise release:
1. Generate and commit the appropriate dependency lockfiles; use deterministic installation (pip constraints/lock tooling and npm ci).
2. Generate an SPDX or CycloneDX SBOM for the exact release and container images.
3. Record each direct and transitive component, exact version, supplier, package URL, declared licence and evidence location.
4. Review licence texts and compatibility for the intended distribution model, including bundled binaries and container OS packages.
5. Record required copyright notices, attribution, source-offer or corresponding-source obligations, and modification notices.
6. Trace fonts, icons, images, templates, copied snippets and generated assets to their origin and permitted use.
7. Resolve unknown, custom, missing, dual or conflicting licence expressions before distribution.
8. Publish a release-specific third-party notices file derived from the verified SBOM and legal review.
9. Archive the SBOM, notices, lockfiles and review record with the release.

## Release gate

Do not describe the dependency audit as complete until the exact release artefacts have been scanned and every shipped component has a reviewed licence/provenance record. Vulnerability scan success is not a substitute for this review.
