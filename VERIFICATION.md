# Verification — 6 October 2026

All checks used the current local source and build. Automated tests use fake providers and temporary databases; the live Bright checks use the user's existing server-side `.env` without printing credentials or JWTs.

## Completed checks

| Requirement | Evidence |
|---|---|
| Server-side Bright authentication | Live authentication and resource discovery succeeded. Credentials load only in the Python service; JWTs are memory-only. The launcher removes inherited Bright credentials from the frontend environment. |
| Meter discovery | One installation and one active electricity stream were discovered and persisted with an opaque local meter ID. |
| Historical half-hourly import | Initial 90-day lookback stored 4,350 distinct half-hours, beginning 8 July 2026. The live worker finished successfully. |
| Refresh and corrections | The UI initiated a successful refresh of 702 recent records. Stored count stayed at 4,350. Automated tests separately replaced fixture values while preserving unique interval count. |
| Current Bright tariffs | Current tariff and cost-resource history were imported. The newer time-of-use structure preserved the off-peak window. The available history contains one entry from 5 October. |
| Historical manual tariffs | API integration tests saved a tariff into an isolated database, rejected overlap, recalculated a day's estimated cost and deleted the manual entry. The phone-sized browser form and off-peak window controls were inspected. No invented historical rates were added to the real account. |
| Estimated costs | Tests cover decimal unit costs, VAT, one daily standing charge, exclusive tariff ends, provider changes and manual override priority. Unknown earlier prices remain unavailable. |
| UK clock changes | Tests prove 46/48/50-period days, distinct repeated BST/GMT hours, UK versus UTC windows, and midnight-crossing windows. |
| Daily charts and interactions | Browser checks verified 48 day bars, usage/cost toggles, tariff-unavailable tooltips, keyboard date changes, and weekly daily drill-down. |
| Week/month comparisons | Browser checks showed both views; tests prove matching elapsed current-week comparison, missing-comparison suppression and leap-year month boundaries. |
| Freshness and missing indicators | Latest reading and successful sync were visible. An unimported day showed 48 missing intervals, unknown usage/cost rather than zero, and 0% coverage. Future intervals were labelled not yet elapsed. |
| Mobile layout | Desktop and 390×844 / 320×740 browser layouts were checked. No horizontal document overflow was reported. Mobile navigation retains accessible names. |
| Local API security | Tests reject foreign origins, DNS-rebinding Host headers and write requests without the per-launch session token. The production proxy is allow-listed. |
| Secret exposure checks | Final built client assets were scanned against the actual configured credentials: zero matches across 18 files. Production `/` and `/api/status` contained no matching credentials. Requests to `/.env`, an `/@fs/…/.env` path and an unapproved API route returned 404. |
| Private persistence | `.env` and SQLite file permissions are 0600; the data directory is 0700. Runtime data and credentials are excluded by `.gitignore`. |
| Locally runnable delivery | `python3 scripts/run.py --production` started both services, and the production UI and proxy returned HTTP 200. A launcher shutdown stopped its child services, and restart succeeded. |
| Documentation | Root README includes prerequisites, development/production commands, tariff semantics, provider extension points, security boundaries, backups, tests and troubleshooting. |

## Automated results

- `python3 -m unittest discover -s tests -v`: **32 passed**.
- `npm run typecheck` (inside `dashboard`): **passed**.
- `npm run build` (inside `dashboard`): **passed**.
- Python source compilation: **passed**.
- Final production-browser error logs: **no errors**.

The Vinext build prints a static-analysis notice that the React page route cannot be classified. The route was verified by serving the production build, loading it in the browser and exercising the local API. Node also emits a dependency deprecation notice for `module.register()`; it did not prevent checks, the build or runtime from completing.

Private preview captures are saved in ignored `data/preview-desktop.jpg` and `data/preview-mobile.jpg`.

## Product limits kept explicit

This is a local, single-household app. It is not published or exposed to a physical phone/network by default. Bright timestamps are treated as UTC, while imported tariff windows assume UK local time and rates as reported; the response does not explicitly establish its VAT/clock conventions. Both uncertainties appear in the UI. Enter confirmed historical rates to price earlier periods, and use confirmed manual rates for bill-aligned estimates. Supplier billing rounding and intelligent tariff adjustments are outside this first version.

## Tariff editing, chart colours and CSV export

- Backend suite: **34 passed**. New integration cases edit a manual tariff without changing its ID, recalculate costs, reject overlapping dates, and refuse edits to Bright records or another meter's tariff.
- `cd dashboard && node --test lib/energy.test.mjs`: **4 passed**. Cases cover standard/off-peak/unknown chart breakdowns, standing charges, CSV missing and zero readings, repeated BST/GMT clock times, quoting/formula-safe labels, and UK date conversion for editing.
- TypeScript checks and the production build passed after these changes.
- Browser checks showed separate off-peak day bars and stacked week bars, the CSV export control and resulting notification, and correctly prefilled manual tariff dates/rates/windows. The edit form was inspected at 390×844. Existing household tariffs were not changed during verification.
- The running development frontend picked up source changes. The existing Python service requires a restart before the new edit handler is active.

## Initial multi-household Docker hosting (superseded below)

- Added invite-only accounts, password hashing, secure seven-day sessions, Origin/CSRF checks, internal service authentication, encrypted per-account Bright credentials and separate household databases.
- Regression suite: **50 tests passed**, including cross-tenant meter/tariff/export access, invitation expiry/reuse, session revocation, encrypted credential binding, operator restore safeguards, backup tampering, retention cleanup and provider redirect refusal. Presentation tests: **4 passed**.
- Standard Next.js replaced the preview/Cloudflare toolchain. Lint, TypeScript, local production build and standalone Docker build passed. Both official-base Docker images built successfully. Caddy configuration validated.
- The full disposable Docker integration test passed over verified HTTPS: registration, secure cookies, session-protected endpoints, cross-account access rejection, Origin/CSRF guards, security headers, unpublished application ports, non-root/read-only services and encrypted backup round-trip. Test volumes/containers were removed afterward. No real Bright calls were made.
- npm audit (all retained dependencies): **0 vulnerabilities**. pip-audit for every pinned Python production package: **no known vulnerabilities**. These are point-in-time advisory checks, not an independent penetration test or an audit of every operating-system package in the images.
- Source scan found no actual local Bright username/password in tracked or candidate source files. Docker build contexts exclude `.env`, real readings, screenshots, deployment secrets and the virtual environment.
- New registration/sign-in controls were inspected at mobile width. Existing real household credentials/readings were not migrated into hosted storage. The real deployment requires domain/operator configuration. Bright is enabled for the stated personal/friends use.
- The initial SQLite deployment had a small account quota and one API process. The PostgreSQL architecture below replaces those limits.

## Scalable PostgreSQL hosting

- Removed the default account quota and the 25-account ceiling. Registration remains invite-only; an optional positive capacity setting is available.
- Added shared PostgreSQL accounts/sessions, tenant-prefixed keys, forced row-level security and bounded connection pools. API replicas are stateless; hosted SQLite data is imported only by an explicit transactional migration.
- Added separate worker replicas, durable per-household jobs, advisory locks, write ownership fencing, deduplication, crash replay, retries that preserve historical import ranges, shared per-HTTP-request throttling/429 cooldowns, leader-claimed housekeeping and worker heartbeats.
- **50 regression tests passed**. **11 additional real PostgreSQL tests passed** inside the disposable Docker stack: household isolation/context reset, shared session revocation, registration beyond 25 users, concurrent workers, deduplication/crash replay/retries, stale-worker fencing, encrypted credentials/invites/throttles, deletion/erasure cascade, SQLite migration, global HTTP spacing/cooldown, retention and concurrent password-reset/login revocation.
- Docker HTTPS integration passed with **two API replicas and two worker replicas** (two Gunicorn processes per API). Verified queued status, private ports, non-root/read-only application containers, Origin/CSRF controls, own dashboard access, foreign household rejection and worker monitoring. Providers were mocked/paused; no real Bright calls or user data were used.
- Online encrypted PostgreSQL backup, separate encrypted erasure-ledger backup, authenticated decryption, actual pg_restore, applying post-backup deletions and revoking restored access all passed. All disposable containers and volumes were removed.
- Frontend production Docker build, TypeScript, lint and the four presentation tests passed. The UI reports queued imports/retry times and polls every 30 seconds when idle (five seconds during imports).
- Python production dependency audit found no known advisories. Actual local Bright credentials remain excluded from source and Docker contexts. Existing local SQLite/credentials were not migrated or modified.
- HOSTING.md documents deployment, five private secrets, independent API/worker scaling, database connection budgets, remote verified TLS settings, aggregate monitoring, migration, online backups and restore safeguards. Domain/operator settings are still needed for real hosting.
- Replica/concurrency tests verify correctness; no production account-capacity benchmark or independent security assessment is claimed. Physical resource limits and provider throughput still determine capacity.

## Nginx Proxy Manager deployment option

- Added an explicit Compose override that removes gateway host ports, disables Caddy HTTPS/ACME, and attaches only the gateway to a dedicated external NPM network. Standalone HTTPS remains available.
- Caddy accepts only the configured NPM peer IPs, strictly parses the forwarded client-address chain, and replaces client-IP/host/protocol headers before passing requests to Next.js. Both modes import one shared security policy.
- Compose merged configuration and both Caddy configurations validated. The frontend production Docker build passed.
- The NPM-compatible nginx TLS fixture passed trusted client-IP hashing, browser header-spoof rejection, rejection of a different Docker peer, Origin guards, no-store/security headers and request-body bounds. The test uses NPM's forwarding behaviour; it does not reconfigure or certify the user's actual NPM installation.
- HOSTING.md includes the external network with a separate dynamic IP range, fixed NPM address, Proxy Host settings, log controls and the compose-npm.sh wrapper for all deployment/maintenance commands.
- Full NPM-mode Docker integration completed successfully with two API and two worker replicas, including secure registration/cookies, household isolation, queued imports, dashboard access, encrypted online backup/restore and erasure safeguards. Its 11 PostgreSQL tests passed; disposable containers/networks/volumes were removed afterward.
