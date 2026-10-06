# Powerall interface

This is the React/Next.js interface for Powerall. Start both the interface and the local Bright data service using `python3 scripts/run.py` from the parent project folder. See the parent `README.md` for configuration, historical tariffs, cost assumptions and tests.

Frontend commands (inside this folder):

```sh
npm ci
npm run typecheck
npm run build
```

`npm run dev` runs the interface alone and expects the data service at `127.0.0.1:8765`. `npm run start` serves the built version. In SQLite mode, Bright credentials belong in the parent `.env`. In hosted mode, each user connects Bright through the account interface; credentials are encrypted server-side. Never put credentials in this folder or client environment variables.

The Docker deployment uses a standalone production build. To test registration, sign-in and separate households locally, run `.venv/bin/python scripts/run-hosted-local.py start` from the parent project folder and open its printed invitation. The preview uses HTTPS at localhost:8443 and a separate PostgreSQL database. Account passwords require at least 12 characters. See [HOSTING.md](../HOSTING.md) for local preview and remote deployment instructions.
