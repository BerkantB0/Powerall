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
