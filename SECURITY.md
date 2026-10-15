# Security

Powerall handles household energy data and, in hosted mode, account and Bright credentials. Treat a deployment and its backups as private systems.

## Reporting a vulnerability

Please do not post credentials, exploit details or household data in a public issue.

Use GitHub's **Report a vulnerability** option under the repository's Security tab if it is available. If private reporting is not available, open an issue asking the maintainer for a private reporting channel, without describing the vulnerability publicly.

A useful report describes the affected version, deployment mode, steps to reproduce using synthetic data, and the expected impact. Do not test against someone else's installation without permission.

## Support expectations

This is an early community project. Security fixes are intended for the latest release; there is no guaranteed response time or long-term support policy for older releases. Automated tests are not an independent security audit.

## Deployment boundaries

- The local SQLite launcher has no account authentication and binds to loopback. Do not expose it publicly.
- Public hosting requires the authenticated Docker deployment, HTTPS and the configured gateway. Do not publish the API or database ports.
- Behind Nginx Proxy Manager, trust only the actual proxy peer address. Host-IP forwarding may translate connections through a Docker bridge; use firewall rules so other clients cannot impersonate the trusted source.
- Protect the credential encryption key and backup key separately from the database. Docker volumes are not encrypted automatically. A privileged operator can decrypt stored Bright credentials.
- Avoid sharing logs or screenshots with meter identifiers, invitation URLs, account information or consumption history.
- Open-source availability does not grant permission to use a third-party API. Users and operators are responsible for their provider access and consent.

See [HOSTING.md](HOSTING.md) for configuration, backups and account administration.
