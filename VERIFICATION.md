# Verification — 6 October 2026

This report records checks as the implementation evolved; earlier sections describe the version tested at that stage. The later sections describe the current hosted architecture and recent changes. Automated tests use fake providers and temporary databases; the live Bright checks use the user's existing server-side `.env` without printing credentials or JWTs.

## Initial SQLite implementation checks

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

## Initial automated results

- `python3 -m unittest discover -s tests -v`: **32 passed**.
- `npm run typecheck` (inside `dashboard`): **passed**.
- `npm run build` (inside `dashboard`): **passed**.
- Python source compilation: **passed**.
- Final production-browser error logs: **no errors**.

The initial, now-replaced Vinext build printed a static-analysis notice that the React page route cannot be classified. The route was verified by serving the production build, loading it in the browser and exercising the local API. That build also emitted a dependency deprecation notice for `module.register()`; it did not prevent checks, the build or runtime from completing.

Private preview captures are saved in ignored `data/preview-desktop.jpg` and `data/preview-mobile.jpg`.

## Product limits kept explicit

SQLite mode is local and single-household. Hosted mode supports separate household accounts; no public deployment has been performed. The local preview remains bound to loopback. Bright timestamps are treated as UTC, while imported tariff windows assume UK local time and rates as reported; the response does not explicitly establish its VAT/clock conventions. Both uncertainties appear in the UI. Enter confirmed historical rates to price earlier periods, and use confirmed manual rates for bill-aligned estimates. Supplier billing rounding and intelligent tariff adjustments are outside this first version.

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

## Persistent local hosted preview and interface updates

- Added `compose.local.yaml`, `deploy/Caddyfile.local` and `scripts/run-hosted-local.py` for a persistent, loopback-only PostgreSQL preview at https://localhost:8443. Separate secrets, TLS files and invitation links remain in ignored `data/hosted-local/`; existing SQLite data and `.env` are not automatically imported.
- Verified the hosted login returned HTTP 200 over HTTPS using its local certificate. Anonymous account endpoints remain protected; local HSTS is omitted so other localhost HTTP apps remain usable. Production security headers remain configured separately.
- Hosted browser inspection was blocked by the in-app browser's self-signed certificate warning. The user must accept the local exception and complete registration/Bright connection themselves; no completed live hosted household import is claimed here.
- Management commands now close the PostgreSQL pool before exiting. The status command completed without the prior pool shutdown delay.
- Reduced the minimum sign-in password length to 12 in both backend validation and registration/password-change forms. **16 hosted tests passed**, including rejection of 11-character passwords, acceptance of 12-character passwords and session revocation after password change.
- Removed the sidebar privacy footer and its unused styles. The running SQLite dashboard was visually inspected and the sidebar captured in ignored `data/sidebar-without-label.png`.
- Frontend lint, TypeScript checks, final hosted production Docker build and whitespace checks passed. The rebuilt local hosted frontend was started and its login endpoint returned HTTP 200.
- Before commit `f8d4576`, staged files were checked against private configuration and generated runtime secrets: none were included. The local preview and UI changes were committed with a clean working tree.

## NPM through a private host IP

- Added `compose.npm-ip.yaml` and `scripts/compose-npm-ip.sh` to publish only gateway port 8080 on an explicitly configured private host IP, without requiring an external/shared Docker network.
- Validated the merged Compose configuration: exactly one gateway port, correct bind/trusted proxy IPs, the NPM Caddy configuration, no published frontend/API/database ports and no external network declarations. Shell syntax and whitespace checks passed.
- This reuses the previously tested NPM gateway policy. The new host-IP routing has not been deployed or tested end-to-end on the user's remote host; source-IP routing and actual NPM forwarding must be verified there.

## Recurring weekday and free-hour tariffs — 7 October 2026

- Manual time-of-use windows now support weekday selection, including Sunday-only zero rates. Existing windows without weekdays retain their daily behaviour; window JSON persists without a database schema migration.
- Weekday-specific windows override daily windows; equally specific overlapping windows are rejected. Overnight windows belong to their start weekday, evaluated in the tariff's chosen UK/UTC clock. Free electricity retains the daily standing charge.
- Five new backend cases cover Sunday boundaries, normal rates on other days, daily-window precedence, zero VAT-adjusted costs, overnight rollover, UK clock changes, UTC weekdays, validation and persistence. The 40-test SQLite/backend suite passed. The hosted suite passed all 17 tests, including saving/editing/exporting weekday windows. The 11 separately configured PostgreSQL integration tests were skipped in the general suite, not rerun for this change.
- All five presentation tests passed, including a new free-rate CSV/stacked-chart case. TypeScript, lint and the hosted frontend/API Docker builds passed. Local hosted services were updated after a transient Docker secret-mount I/O failure resolved.
- The in-app browser's CDP commands timed out on both existing and fresh local tabs, so the changed form has not been visually verified in this session. No guessed Sunday rates or hours were saved to the user's household.

## Gunicorn read-only startup correction

- Added `--no-control-socket` to the API image's Gunicorn command. The optional `gunicornc` socket is unused by Powerall and otherwise attempts to write under `/home/powerall` in a read-only container.
- Rebuilt the API image and tested its actual command with a disposable fixture WSGI app: HTTP 200, clean shutdown and no control-socket/read-only errors. The fixture retained UID 10001, a read-only root, dropped capabilities, no-new-privileges, a temporary `/tmp` and no external network. It used no household data or credentials and its container was removed.

## Custom dashboard periods — 7 October 2026

- Added inclusive custom start/end dates (1–366 days, ending no later than today), daily charts/drill-down and existing half-hour CSV exports. Custom comparisons use the immediately preceding equal-length calendar range, matching elapsed UK time for a range ending today. Existing day/week/month requests remain compatible.
- General backend suite: 63 passed, 11 separately configured PostgreSQL tests skipped. Six new cases cover inclusive bounds/standing charges, preceding-period totals, current partial comparisons, clock changes/missing prices, invalid/oversized/future requests and hosted household isolation. All five presentation tests passed.
- Frontend TypeScript, lint and production Docker builds passed. Removed a duplicate generated `.next/types/routes.d 2.ts` file that had caused a local-only TypeScript conflict; clean Docker type checking also passed.
- Restarted the local SQLite preview with the new API, rebuilt the local hosted stack using its persistent database/keys, and verified the three-day local API response and HTTPS login both returned HTTP 200.
- Browser verification succeeded using the actual IPv4 loopback listener at http://127.0.0.1:5173 after localhost navigation failed. Verified the Custom selector, native date entry and Apply range, exactly three daily bars for 1–3 October, daily drill-down and return to the saved custom selection. Downloaded CSV contained exactly 144 half-hours across those three dates.
- Inspected desktop and phone-sized (390×844) layouts, confirmed no horizontal document overflow, and corrected clipped custom-chart date labels. Private screenshots remain in ignored data/custom-period-desktop.png and data/custom-period-mobile.png; viewport override was reset.
- Documented upgrade compatibility, backup/configuration preservation, API-before-frontend ordering, brief recreation downtime and rollback in HOSTING.md. No schema migration, new secrets or environment settings are needed. The remote deployment was not changed.

## Chart time-label alignment correction

- Replaced evenly spread axis labels with grid cells corresponding to their actual bar indices, sharing gap and padding variables with both half-hour and daily bars. Day labels use UK clock times at four-hour boundaries; every chart includes its final interval/date (23:30 for day charts).
- Live browser geometry checks on the day chart and a 390×844 mobile viewport showed label centres within 0.05 CSS pixels of the corresponding bar centres. Verified 46- and 50-half-hour clock-change days; the 50-slot day retained 00:00/04:00/08:00/12:00/16:00/20:00/23:30 ticks at their actual indices. Weekly labels aligned within 0.01 pixels.
- Fixed an existing mobile toolbar overflow exposed by the four period buttons by allowing the date controls to wrap. The mobile document no longer overflowed the viewport; the temporary viewport override was reset. Proof screenshot is stored privately in ignored data/chart-axis-fixed.png.
- TypeScript, lint and all five presentation tests passed; the final production Docker frontend build passed. Rebuilt/recreated only the local hosted frontend. No backend, schema, secrets or household records were changed by this correction.
- HOSTING.md includes frontend-only rebuild/recreation and rollback instructions using the deployed NPM host-IP wrapper. The remote deployment was not modified.

## iPhone Home Screen icon

- Added an opaque 180×180 PNG derived from the existing SVG lightning logo, with the same colours and shape. The PNG uses a square background so iOS can apply its own corner mask. Root metadata advertises the Apple touch icon across dashboard, login and privacy pages; the existing browser favicon remains available.
- Visually inspected the PNG and verified its dimensions, PNG format and lack of transparency. Local dashboard and login HTML both contain the expected Apple touch-icon link; the icon endpoint returned HTTP 200 with image/png and the correct dimensions.
- TypeScript, lint and production Docker frontend build passed. A disposable read-only, network-disabled container verified the packaged PNG and prerendered dashboard/login metadata in the final image; it was removed afterward.
- HOSTING.md documents frontend-only deployment and removal/re-addition of an existing iPhone Home Screen shortcut. No API, database, configuration or secret changes are required. The remote deployment was not modified; installation on a physical iPhone has not been tested here.

## Daily chart number previews — 8 October 2026

- Week, month and custom detail rows now show daily kWh and estimated total cost together, independent of the selected chart metric. Missing readings/costs and future days remain explicitly labelled. The hint explains hover versus click/tap. Existing mouse-enter and keyboard-focus handlers only select the preview; click retains daily drill-down.
- Live browser keyboard-focus checks in all three views displayed daily numbers without switching periods. Verified both metrics in Custom and confirmed clicking a weekly day still opens its half-hours. Inspected the rendered custom cost preview; private proof is saved in ignored data/daily-preview.png. Direct mouse hovering was not exercised by the available browser interaction API; its existing selection-only handler was inspected.
- TypeScript, lint and the standalone production frontend build passed. The Docker build could not run because Docker Desktop was stopped; no container or remote deployment was changed. HOSTING.md records frontend-only rollout/rollback with the NPM host-IP wrapper. No API, schema, secrets or household data changes are required.

## Touch hold-and-slide previews — 8 October 2026

- Added native touch listeners to daily charts: a 450 ms hold activates preview, sliding follows the nearest daily bar, and release retains the numbers without opening a day. Quick taps preserve drill-down. Movement before activation cancels the hold and allows normal scrolling. Multitouch/cancellation and view changes clear pending gestures; listeners and timers are removed on cleanup. Touch move/end prevention is scoped to active previews, using explicit non-passive listeners. Keyboard activation remains available.
- Five gesture regression tests passed, covering quick tap, stationary hold, sliding/gaps, release click suppression, subsequent taps, vertical/horizontal swipes, cancellation, multitouch, cleanup and keyboard clicks. All five existing presentation tests also passed. TypeScript, lint and the standalone production build passed.
- Browser inspection confirmed the updated hint, daily numbers and desktop click-to-open. Private proof is in ignored data/touch-preview.png. The available desktop browser API cannot generate real touch gestures; physical iPhone Safari/Home Screen behaviour, scrolling and hold/slide remain to be verified on device. No Docker rebuild or remote deployment is claimed for this change.
- README.md describes the gesture; HOSTING.md extends the frontend-only rollout with phone checks and rollback. No API, schema, secrets or household data changes are required.

## Wider mobile charts — 8 October 2026

- On screens up to 600 px, chart cards extend 12 px into each existing page margin, use 10 px horizontal padding, a 28 px left-axis gutter and a 4 px plot gap. Half-hour gaps reduce from 2 px to 1 px; daily gaps and shared axis/bar alignment are retained. Desktop spacing is unchanged.
- Live phone-size inspection at a 390×844 viewport measured plot width increasing from 270 to 307 CSS px and half-hour bar width from 3.66 to 5.41 px (about 48% wider). Day-label centres remained within 0.05 px of their bar centres. Monthly cost labels fitted within the smaller gutter. No horizontal page overflow occurred at 390 or 320 px viewport widths; the temporary viewport override was reset.
- Visually inspected the day chart; private proof is saved in ignored data/mobile-chart-width.png. Lint and the production build (including TypeScript validation) passed. The existing frontend-only rollout/rollback section covers this CSS change; no remote deployment or household data was modified.

## Balanced mobile chart layout — 8 October 2026

- Supersedes the preceding wider-card layout: phone chart cards now follow the same page margins as surrounding cards. The scale labels overlay the plot above grid lines, with a white backing and no pointer interception. The redundant zero label is hidden on phones. Long custom charts retain a sticky overlay scale; an extra 9 px of top space prevents their top label from clipping without reducing bar height. Half-hour gaps remain 1 px; desktop layout is unchanged.
- At 390×844, browser geometry confirmed equal 14 px plot insets, chart/insight-card left edges both at 19 px, a 309 px plot and 5.45 px half-hour bars. Visually inspected usage and checked cost/custom layouts. A 36-day custom range scrolled within the card with its top scale label visible; no page overflow at 390 or 320 px. The viewport override was reset.
- Private proof is in ignored data/mobile-chart-balanced.png. Lint and the final production build, including TypeScript validation, passed. HOSTING.md describes the final layout under the same frontend-only rollout/rollback; no migration, configuration changes or remote deployment are required or performed.

## Separate mobile scale gutter — 8 October 2026

- Corrects the preceding inset-label layout, which obscured bars. Removed the scale overlay and restored normal-flow labels, including zero. Mobile chart cards retain the surrounding card margins with 10 px inner padding; the 28 px left axis plus 4 px gap is balanced by 32 px of space on the right. Half-hour gaps remain 1 px. Custom charts keep an opaque sticky scale gutter during horizontal scrolling.
- Visually inspected the corrected day chart. Browser geometry measured equal 43 px plot insets, a minimum 4 px gap between every scale label and the plot, and 4.25 px half-hour bars at 390×844 (versus 3.66 px before these mobile changes). Monthly cost charts retained the same label separation/equal insets with no page overflow at 320, 390 and 430 px viewport widths. Inspected a 36-day custom chart after scrolling to its end; its opaque scale gutter kept numbers readable. Temporary viewport override was reset.
- Lint and the production build including TypeScript validation passed. Private proof is in ignored data/mobile-chart-clear-axis.png. HOSTING.md now describes the corrected final layout; deployment remains frontend-only with no migration or secret changes. The remote deployment was not modified.

## Remove excess right gutter — 8 October 2026

- Removed the extra 32 px mobile right margin while retaining the separate 28 px left scale gutter and 4 px gap. Chart cards remain aligned with surrounding cards; the right edge now uses only normal card padding.
- Visually inspected the day chart at 390×844. Plot width increased from 251 to 283 CSS px; labels retained 4 px clearance from bars, right inset was 11 px including the border, and the page did not overflow. Private proof is in ignored data/mobile-chart-final-spacing.png; viewport override reset. Lint and whitespace checks passed. This CSS-only adjustment uses the existing frontend-only rollout and requires no migration or configuration changes.

## Recoverable hosted refresh failures — 8 October 2026

- Manual refresh can atomically bring an existing failed queue job forward, resetting its retry count and retaining the historical range when no replacement range is supplied. Fresh queued and running imports remain protected against duplicate/replaced ownership. Failed automatic retries use 1, 2, 4, 8 and then at most 15 minutes; success resets consecutive attempts. The periodic worker scheduler bounds existing deferred failure jobs to 15 minutes without altering active ownership or successful refresh schedules.
- Status adds optional retry metadata and distinguishes legacy accumulated attempt counts from actual failures. The UI treats a scheduled retry as waiting rather than active work, enables manual refresh, shows relative retry time and provides Retry now. The existing one-minute API cooldown is shown as a short countdown. Shared Bright throttling and provider cooldowns remain enforced. Older API statuses are supported; deployment order is API/worker before frontend.
- General backend suite: 67 passed, 11 real-PostgreSQL tests skipped there. The initial sandbox run could not bind temporary API test sockets; rerunning with loopback permission passed. Four new queue tests cover acceleration/range preservation, duplicate/running rejection, explicit ranges and bounded backoff. The separate disposable PostgreSQL suite passed all 11 tests, including expanded worker checks for automatic delay, legacy deferred-job repair, manual acceleration, preserved historical days, reset-on-success, legacy status compatibility and running ownership protection. No real Bright calls, production accounts or deployed volumes were used; test stack containers/volumes were removed.
- All 13 frontend tests passed, including three retry-state/cooldown/legacy-API cases. TypeScript, lint, standalone production build and API Docker image build passed. A separate loopback synthetic-status preview showed enabled refresh/Retry now while a retry was deferred; clicking Retry now switched to queued status and disabled duplicate refresh. Private screenshot is in ignored data/refresh-retry-controls.png. The synthetic preview was stopped afterward.
- The original remote next-day timestamp was not reproduced or independently inspected; the prior queue backoff in source was capped at an hour. The tested repair also handles existing day-long deferred fixtures. HOSTING.md records compatibility, preserved configuration/data, API/worker-first rollout and rollback. No schema migration or new secrets/settings are required; the remote deployment was not changed.
