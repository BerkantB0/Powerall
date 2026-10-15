# Roadmap

Powerall is an early self-hosted project. This roadmap describes direction, not promised delivery dates. Suggestions and contributions are welcome.

## Planned work

- **Additional providers:** add Octopus Energy through the existing provider interface, keeping dashboard and tariff calculations independent of the data source.
- **Better import diagnostics:** explain gaps, provider failures and import progress more clearly, and make refreshing data easier to understand.
- **Mobile refinement:** test hold-and-slide charts on physical phones, improve accessibility and make long date ranges easier to explore.
- **More tariff coverage:** improve support for complex and time-varying tariffs while keeping missing or uncertain prices visible.

## Under consideration

- Gas consumption and electricity export.
- Cumulative meter readings or live power where a provider and the user's hardware make them available.
- Stronger account recovery and optional MFA or SSO.
- More detailed usage reports, tariff comparisons and performance benchmarks.

A feature belongs in the app only when the source data and calculation rules are reliable enough to explain. Until then, Powerall should show a gap or limitation rather than invent a value.
