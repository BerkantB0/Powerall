# Powerall interface

This is the React/Vinext interface for Powerall. Start both the interface and the local Bright data service using `python3 scripts/run.py` from the parent project folder. See the parent `README.md` for configuration, historical tariffs, cost assumptions and tests.

Frontend commands (inside this folder):

```sh
npm ci
npm run typecheck
npm run build
```

`npm run dev -- --host 127.0.0.1` runs the interface alone and expects the data service at `127.0.0.1:8765`. `npm run start` serves the built version. All Bright credentials belong in the parent `.env`, never in this folder or in client environment variables.
