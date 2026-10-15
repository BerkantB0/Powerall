# Validation

This file describes the checks used for Powerall and their limits. Tests use temporary databases, synthetic accounts and fake provider responses. Real household data and live Bright credentials are not required for automated checks.

## Coverage

| Area | What is checked |
| --- | --- |
| Consumption and costs | Decimal calculations, VAT, standing charges, manual overrides, missing readings and unknown prices |
| Dates and tariffs | UK clock changes, repeated BST/GMT hours, overnight windows, weekday/free-hour rates and effective dates |
| Dashboard and exports | Custom ranges, comparisons, CSV gaps/zero values, formula-safe text, chart breakdowns and touch gesture logic |
| Local API | Origin/Host checks, request bounds, mutation tokens, imports and corrections |
| Hosted accounts | Invitations, separate registration/sign-in budgets, bounded password work, password rules, encrypted credentials, session revocation, household isolation and deletion |
| PostgreSQL integration | Forced row-level security, worker concurrency/ownership, retry recovery, migrations, shared throttling and password-work limits across replicas |
| Recovery | Encrypted backup integrity, restoration, deletion-ledger application and invalidation of restored access |
| Upgrade utility | Command order, replica preservation, failed-step handling, locking and consistent release versions |

Commands for running the checks are in [CONTRIBUTING.md](CONTRIBUTING.md). The normal backend suite skips PostgreSQL-specific tests unless the disposable integration stack is used. The Docker integration script creates its own containers and volumes and removes them afterward.

## Limits

- Passing tests do not establish a production capacity limit or replace an independent security review.
- Provider availability, consent and actual meter data vary by account. A successful API response can contain no readings.
- Gesture tests simulate event handling. Physical iPhone and other browser/device combinations need broader coverage.
- Deployment-specific proxy routing, firewall rules, certificates and recovery procedures must be checked by the operator.
- Dependency scans are point-in-time checks, not a guarantee that dependencies or base images are free of vulnerabilities.
- Per-account sign-in throttling intentionally remains to resist distributed password guessing. Repeated failed logins can temporarily block new sign-ins for that username; existing sessions remain usable. Public-proxy abuse controls and MFA require separate consideration.

## Release candidate 0.2.1 — 15 October 2026

- Backend suite: 83 passed; 14 PostgreSQL-specific tests skipped by the normal local runner and checked separately in the disposable integration stack.
- The final PostgreSQL integration suite passed all 14 tests. Full Docker HTTPS checks passed both directly and through the NPM-style nginx route, using two API and two worker replicas, synthetic accounts and temporary volumes. These include secure sessions, household isolation, proxy-header handling, request bounds and encrypted backup/restore with deletion protection.
- Frontend suite: 13 passed. TypeScript, lint and the standalone production build passed.
- API and frontend Docker builds passed. Isolated, read-only containers verified the release version and packaged project/dependency license notices; no household database or deployment secrets were mounted.
- Dependency audits reported no known advisories for the npm lockfile or the eight resolved Python packages checked. Operating-system packages in base images were not audited by these commands.
- Local documentation links and ignore rules were checked. No known local credential values or common credential patterns were found in the source/history scan; its scope does not cover every possible secret format or previously rotated credential.
- The GitHub Actions workflow is configured for backend/frontend checks, with Docker integration available by manual dispatch. It has not yet run on GitHub.
- Authentication regressions check that invalid registrations cannot consume sign-in allowances, invalid/expired/used invitations and account capacity are checked before hashing, concurrent invitation reuse is rejected, and existing sessions and deployed login-attempt keys remain valid. Shared password-work slots are checked across PostgreSQL connections, including commit/rollback release. No database schema or secret changes are required by this release.
- Production firewall rules, NPM configuration and installed images were not audited; this work is not an independent penetration test.
