# Deployed application

Powerall is deployed and holds real household accounts, credentials, readings and tariffs.
Consider upgrades in every change: preserve existing data and configuration, keep old API
requests working where possible, and flag breaking changes explicitly. Maintain semantic
release versioning in VERSION and keep application package versions consistent. Do not add
upgrade-process documentation unless the user asks for it.
Never replace deployment secrets, copy local test data into production, or remove persistent
volumes as part of an upgrade. Include the appropriate Compose wrapper in deployment commands.
Record validation and material limitations in VERIFICATION.md. Frontend work must also follow
dashboard/AGENTS.md.
