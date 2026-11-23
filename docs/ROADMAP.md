# Product roadmap

## Phase 0 - foundation
- [x] Public repository and initial project structure
- [x] FastAPI service and health endpoint
- [x] Basic asset CRUD API
- [x] Local PostgreSQL Docker Compose stack
- [x] Automated tests and CI workflow
- [x] Versioned database migrations

## Phase 1 - usable Community Edition
- [x] Authentication and secure password handling
- [x] Organization and user foundation
- [x] Departments and employee directory foundation
- [x] Tenant isolation and role-based access control
- [x] Tenant-scoped asset CSV import/export with validation and spreadsheet-formula protection
- [x] Tenant-scoped software licence register
- [x] Maintenance request workflow
- [x] Basic audit events
- [x] Asset inventory web interface with sign-in and role-aware controls
- [x] Administrator UI for user accounts and audit history
- [x] Authenticated password change
- [x] Per-user access-token invalidation after password or account-active-state changes

## Phase 2 - operational readiness
- [ ] Backup and restore procedures (local PostgreSQL scripts added; recovery validation and off-host protection pending)
- [ ] Structured logs and monitoring
- [x] Baseline frontend security headers
- [x] Login rate limiting with shared Redis counters
- [ ] Additional API security headers and deployment hardening
- [x] Python and npm dependency vulnerability scanning in GitHub Actions (`pip-audit`, `npm audit`)
- [x] Container image vulnerability scanning for unfixed critical vulnerabilities with Trivy
- [ ] Automated integration tests against PostgreSQL
- [ ] Installation and upgrade documentation
- [ ] Independent security review

## Phase 3 - commercial edition
- [ ] Define and document Community/Enterprise boundaries
- [ ] Separate private commercial modules
- [ ] Licence service and offline activation design
- [ ] Enterprise integrations and reporting
- [ ] Support and subscription operations

Commercial functionality must not be represented as implemented until it exists, is tested and is documented.
