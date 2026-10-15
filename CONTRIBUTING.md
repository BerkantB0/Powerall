# Contributing to Powerall

Thanks for helping improve Powerall. For a substantial feature, open an issue first so its scope and data requirements can be discussed. Small fixes and documentation improvements can go straight to a pull request.

## Development setup

Use Python 3.10+ and Node.js 22.13+; Docker builds use Python 3.13 and Node.js 24.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-hosted.txt
cd dashboard
npm ci
cd ..
```

`python3 scripts/run.py` starts the local dashboard and API. `scripts/run-hosted-local.py` provides a separate account-enabled Docker preview. See the README for both options.

## Tests

Run these from the project directory:

```sh
.venv/bin/python -m unittest discover -s tests -v
cd dashboard
node --test lib/*.test.mjs
npm run typecheck
npm run lint
npm run build:hosted
```

The normal backend suite uses temporary databases and fake provider responses. PostgreSQL-specific tests are skipped unless run in the disposable Docker integration stack. With Docker available, build its images and run:

```sh
docker compose --env-file deploy/hosting.env.example build api frontend database
.venv/bin/python scripts/test-hosted-stack.py
.venv/bin/python scripts/test-hosted-stack.py --npm
```

The integration script creates and removes its own test stack. It must never target a deployment containing real accounts. It does not need a Bright account or live credentials.

For chart changes, also inspect narrow and wide screens, keyboard access, missing values and UK clock-change dates. Automated touch-event tests do not replace testing on a real phone.

## Keep changes safe for existing installations

Powerall can hold real household accounts, readings and credentials. Preserve existing data and configuration. Keep older API requests working where practical, and call out breaking changes in the pull request. Never replace deployment secrets or remove persistent volumes as part of a routine change.

Use semantic versioning in `VERSION`, with matching versions in `powerall/__init__.py`, `dashboard/package.json` and the root package entries in `dashboard/package-lock.json`. Check that a dependency update's release notes and supported runtimes match the project.

Keep the code and documentation readable. Explain decisions that affect users, rather than recording a development transcript. Record test coverage and material limitations in `VERIFICATION.md`.

## Protect private data

Do not commit `.env`, deployment secrets, databases, backups, invitation links, provider responses or screenshots from a real household. Use synthetic fixtures in tests and examples. Keep Bright credentials and tokens out of browser code and logs. Missing readings and prices must not silently become zero.

Security reports should follow [SECURITY.md](SECURITY.md), rather than a public issue containing vulnerability details.

## License

By contributing, you agree that your contribution can be distributed under the project's MIT License. Preserve relevant third-party copyright and license notices.
