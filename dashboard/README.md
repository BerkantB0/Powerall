# Powerall dashboard

This folder contains the React/Next.js interface and its server-side API proxy. It is part of Powerall, not a separate service that talks directly to Bright.

Start both the local interface and API with `python3 scripts/run.py` from the project root. For account-enabled Docker mode, use the local preview or hosting instructions in the [main README](../README.md).

From this folder:

```sh
npm ci
npm run dev
```

Development mode listens on `127.0.0.1:5173` and expects the API on `127.0.0.1:8765`. Set `POWERALL_PORT` only if the local API uses a different port. `npm run build` and `npm run start` provide a local production build; Docker uses `npm run build:hosted` for standalone output.

Bright credentials belong in the root `.env` for local mode, or the account connection form for hosted mode. Never put credentials in this folder, client-side code or public environment variables.

Tests and contribution guidance are in [CONTRIBUTING.md](../CONTRIBUTING.md). The UI must keep reading gaps, missing prices and UK clock-change intervals distinct.
