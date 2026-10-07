# Powerall

A mobile-friendly electricity dashboard powered by Bright / Hildebrand Glowmarkt, with a single-household SQLite mode and an invite-only, multi-household Docker deployment.

It discovers electricity meters, imports half-hourly readings, keeps a local history, overlays tariff windows, and estimates costs. Day, week and month views show reading coverage, unknown prices and comparisons. Historical tariffs can be entered from a bill, including off-peak windows, effective dates, VAT and UK/UTC clock conventions.

## Test the full account flow locally

For registration, sign-in, separate household accounts and background imports, use the Docker preview. Requirements: Docker with Compose v2 supporting `!override`, and Python with the packages in `requirements-hosted.txt`:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-hosted.txt
.venv/bin/python scripts/run-hosted-local.py start
```

Open the invitation printed by the launcher and create an account with a password of at least 12 characters. Connect your Bright account under **Data & connection**, then wait for the initial import. The preview runs at **https://localhost:8443**, uses a self-signed certificate and may require you to accept a localhost certificate exception in your browser. It uses separate PostgreSQL data and keys; your original SQLite history and `.env` are not imported.

Use `scripts/run-hosted-local.py invite`, `status` or `stop` with the same Python interpreter to create another invitation, inspect services or stop the preview. Stopping preserves data. See [HOSTING.md](HOSTING.md#full-hosted-flow-on-your-computer) for details and the separate remote deployment instructions.

## Run the single-household SQLite mode

Requirements: Python 3.10+ with UK timezone data (normal macOS/Linux installations), Node 22.13+ and npm. Python uses the standard library; no Python packages are needed.

From the Powerall project folder:

```sh
cd dashboard
npm ci
cd ..
python3 scripts/run.py
```

Open **http://localhost:5173**. Press Ctrl+C in that terminal to stop both services.

The existing root `.env` is used only by the Python data service:

```dotenv
BRIGHT_USERNAME="your-bright-login"
BRIGHT_PASSWORD="your-bright-password"
POWERALL_PORT=8765
POWERALL_IMPORT_DAYS=90
POWERALL_REFRESH_HOURS=6
```

The optional settings default to the values above. Keep `.env` private; it is ignored by Git. Never use public/frontend environment variables for Bright credentials. Confirm that readings already appear in the Bright app and that meter consent remains active.

The first launch imports a 90-day lookback, subject to Bright availability. Each API request covers at most ten days. Import progress is visible while the UI remains available. The automatic worker refreshes every six hours while the app runs and re-fetches the last fourteen days for late or corrected readings. The Refresh button runs this same refresh immediately. Data & connection provides 30-day through 2-year historical imports. Importing a period again updates existing intervals without duplicates.

### Run a built version

```sh
cd dashboard
npm run build
cd ..
python3 scripts/run.py --production
```

The built UI uses the same URL and local data service. Build before using production mode after frontend changes.

For development with separate terminals, run `python3 -m powerall.server` at the project root and `npm run dev` inside `dashboard`. The API defaults to port 8765. The launcher propagates a configured API port to the UI without propagating credentials. If you start the UI separately with a custom API port, set only `POWERALL_PORT` for that process.

## Tariffs and estimated costs

Bright tariff history is retrieved from the electricity cost resource; its current tariff comes from the energy resource. The adapter uses the newer `structure` when available because the legacy `plan` can omit off-peak windows. Historical Bright tariffs remain effective until a later Bright entry supersedes them.

The verified account currently returned one tariff entry, dated 5 October 2026. Earlier consumption is still displayed, but its cost is unknown until historical rates are supplied. The provider's timestamp is interpreted as UTC, consistent with its time-series convention; the UI explicitly flags its clock and VAT assumptions. On the verified entry, UTC midnight appears as 01:00 BST, leaving the first two half-hours of that local date unpriced. Do not assume that this entry establishes the supplier's actual contract start date.

Add manual rates from **Tariffs → Add historical tariff**. Enter:

- A descriptive name and effective dates. Dates start at UK local midnight; the end date is **exclusive**. For example, an entry ending on 5 October covers the days through 4 October.
- Standard rate in pence/kWh and standing charge in pence/day.
- Optional off-peak windows, including windows crossing midnight. They must use half-hour boundaries. Choose Every day or a weekday such as Sunday; enter 0p/kWh for free hours. Weekday-specific windows override daily windows. Windows of the same priority cannot overlap on the same weekdays. An overnight window follows the weekday it starts on, using the selected tariff clock.
- Whether windows follow UK local time (BST/GMT) or UTC/GMT all year.
- Whether your entered prices include VAT. Excluded rates receive 5% domestic VAT once; included rates are used unchanged.

Free-hour windows reduce usage cost to zero; the daily standing charge remains. Free consumption is included in the off-peak chart colour and share, with a Free electricity tooltip and `free` in the CSV rate-period column.

Manual tariffs override Bright during their effective period. Overlapping manual entries are rejected. Use the pencil button to edit a manual entry, including dates, rates, VAT and off-peak windows. Saving recalculates estimates and preserves the entry. Deletion requires a confirmation in the interface. Bright records cannot be edited or deleted from the interface.

For each observed half-hour, usage cost = kWh × applicable pence/kWh ÷ 100. One standing charge is added per UK calendar day using the tariff effective at that day's midnight. Estimates use decimal arithmetic; rounding is applied for display. They are not a supplier billing reconciliation: supplier-specific rounding, discounts, intelligent charging adjustments and corrections are outside this first version.

Bright does not explicitly identify VAT or tariff window clock conventions in the verified response. Imported estimates use prices as reported and assume UK local windows, with a visible warning. A confirmed manual tariff is the way to remove that uncertainty for a period. Dynamic, block, multi-schedule and incomplete tariffs are not flattened into a misleading single rate; enter supported rates manually or add a suitable pricing provider.

## Time, missing data and comparisons

Select **Custom** to choose an inclusive start/end date range of up to 366 days, ending no later than today, then press **Apply range**. Custom charts show daily totals, can scroll horizontally for longer ranges, and let you tap a day to inspect its half-hours. CSV exports cover the selected dates. Comparisons use the immediately preceding range of the same number of calendar days; a range ending today compares matching elapsed days and UK wall-clock time. Reading and tariff gaps remain explicit.

Readings are stored as UTC interval-start timestamps, with a half-open `[start, end)` convention. Days and calendar periods are grouped in `Europe/London`. Spring clock-change days have 46 half-hours and autumn days have 50. Repeated autumn times are labelled separately with BST/GMT.

Day charts colour off-peak half-hours separately. Week and month bars stack standard and off-peak usage, with unknown tariff usage shown separately. Cost bars separate usage costs from standing charges.

Use **Export day/week/month as CSV** above the chart to download the selected meter and period. Exports contain elapsed half-hours, UTC timestamps, UK dates and BST/GMT labels, kWh, rates, rate periods, estimated usage costs, tariff names and estimate warnings. Standing charges appear once per day; blank readings or costs stay blank, and future half-hours are omitted.

Missing elapsed readings remain null. Actual zero readings remain zero. Current unfinished half-hours and future dates are not counted as missing. Reading coverage is received / expected elapsed half-hours. Freshness separately shows the latest reading and last successful sync; the dashboard flags readings more than 48 hours behind. Bright may return delayed, missing or zero values; the app cannot independently verify whether a provider-reported zero represents actual usage.

Usage totals include observed readings only. Fully priced estimates require all elapsed readings, tariff coverage and the day's standing charge. Otherwise the app shows the available subtotal, or an unavailable cost, and explains missing readings or tariffs. Average unit price is weighted by priced consumption and excludes standing charges. Off-peak share uses known off-peak consumption divided by observed consumption; unpriced portions are explicitly identified as unknown.

Completed days/weeks/months compare with the prior corresponding calendar period. Current periods compare with the matching elapsed local days and time in the previous period. Percentage changes are withheld when either compared period has missing readings; cost changes also require complete tariff coverage. Previous months can have different lengths, so read the actual calendar dates alongside monthly totals.

## Architecture and security

The following components describe the single-household SQLite mode. Hosted mode adds PostgreSQL storage and isolation (`postgres.py`, `schema.sql`), authentication (`auth.py`, `hosted.py`), durable workers (`jobs.py`) and operator commands (`manage.py`). Its security and operational setup are documented in [HOSTING.md](HOSTING.md).

- `powerall/bright.py`: read-only provider adapter; authentication, resource discovery, UTC readings and tariff normalization. JWTs live only in server memory and are reauthenticated after expiry or a 401.
- `powerall/providers.py`: provider protocol and normalized meter contract. Future Octopus adapters implement discovery, readings and tariffs; analytics and charts do not depend on Bright response shapes.
- `powerall/storage.py`: SQLite readings, meters, tariffs and sync state. Corrections upsert by meter/UTC interval. The database contains no passwords or JWTs.
- `powerall/sync.py`: serial background imports, ten-day chunks, retries through the adapter, correction refreshes and restart/failure recovery. Cached data survives outages. A failed job backs off for one hour; manual retries remain available.
- `powerall/tariffs.py` / `analytics.py` / `timeutils.py`: provider-independent date, tariff and cost rules.
- `powerall/server.py`: standard-library HTTP API bound only to loopback, with Host/Origin validation, request size limits and a per-launch CSRF token for writes. No CORS grant is returned.
- `dashboard/app`: React/Next.js interface and an allow-listed local API proxy. Credentials are never sent to the browser or placed in the frontend environment by the launcher.

The SQLite launcher serves one household on one computer without account authentication. Both services bind to loopback; keep this mode private to the computer. For multiple households or public hosting, use the authenticated Docker deployment in [HOSTING.md](HOSTING.md). A phone-sized browser preview is supported; access from a physical phone is not enabled by the default loopback binding.

The `data/` directory and `.env` are excluded from version control. The data directory uses owner-only permissions and the database file is mode 0600. Back up `data/powerall.sqlite3` while the app is stopped to preserve imported history and manual tariffs. No telemetry or third-party fonts are added by the app.

## Validation

```sh
python3 -m unittest discover -s tests -p test_powerall.py -v
cd dashboard
node --test lib/energy.test.mjs
npm run typecheck
npm run build
```

The test suite uses isolated temporary databases and fake provider responses. It covers UK clock changes, repeated hours, tariff effective boundaries, overnight windows, UK versus UTC pricing, VAT, daily standing charges, unknown readings/prices, previous-period comparisons, provider parsing, token reconnection, chunked imports, corrections without duplicates, failure retention, concurrent-import exclusion, origin/CSRF/Host checks, and tariff save/edit/recalculation/deletion. Presentation tests cover stacked consumption/cost breakdowns, CSV missing/zero readings and repeated clock times, spreadsheet-safe text cells, and UK date conversion for edits. Real credentials are not used in automated tests.

See `VERIFICATION.md` for the completed live checks and browser validation.

## Troubleshooting

- **Cannot reach Bright:** cached history still works. Check network access, then Refresh data. Provider response bodies and secrets are not logged.
- **Authentication or consent refused:** update the local credentials or renew meter consent in Bright, then restart the app.
- **Data service unavailable:** use the launcher, or start both development processes. Check whether ports 5173/8765 are already in use.
- **Missing earlier tariff:** add the rates that actually applied to that historical period. The latest tariff is deliberately not backdated.
- **Interrupted import:** the next launch marks the interrupted run as failed; refresh to continue. Completed chunks remain stored.
- **No readings before a date:** a longer import cannot recover data Bright does not supply. The interface reports the resulting gaps.

## Public hosting

See [HOSTING.md](HOSTING.md) for the invite-only Docker deployment with separate household accounts, authentication, encrypted Bright credentials, PostgreSQL household isolation, shared sessions, a durable import queue, separate workers, online encrypted backups and privacy controls. The local launcher remains unauthenticated and binds to loopback; it must not be exposed publicly. Hosted data lives in the PostgreSQL Docker volume; API and workers can scale independently. Account capacity is optional, with no default ceiling.

For Nginx Proxy Manager, use the explicit `compose.npm.yaml` option and `scripts/compose-npm.sh` wrapper documented in [HOSTING.md](HOSTING.md#nginx-proxy-manager). NPM handles external HTTPS; the private Caddy gateway preserves the security policy and trusted client-IP handling.

To forward from NPM to the Docker host's internal IP without a shared network, use `compose.npm-ip.yaml` and `scripts/compose-npm-ip.sh`. See [the internal-IP steps](HOSTING.md#npm-through-the-hosts-internal-ip-no-shared-network).
