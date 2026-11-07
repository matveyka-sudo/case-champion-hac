# Canonical contracts 0.1.0

Import models from `a2_contracts` or their domain submodule. Contracts depend only on Pydantic and the Python standard library, never on FastAPI, the database, engine or model SDK.

- `dataset`: sources, evidence, service/calendar/transfer tables, accepted bundle and key/reference checks.
- `regions`: full versioned study area and separate demonstration selection, with no fixed city count.
- `profiles`: profile configuration, task cases and fixed TaskSet.
- `journeys`: shared request validation, legs, constraints and journey results.
- `results` / `scenarios`: profile scores, comparison metadata and scenario operations.
- `api`: analysis input, error envelope, jobs and catalog place records.

All versions are positive JSON integers; string semantic versions from historical P1 are not accepted. Unknown fields and non-finite numbers are rejected. Public datetimes require offsets; durations are seconds in legs and named minutes in aggregates; GTFS service seconds may exceed 86400. Actual elapsed duration checks compare UTC instants, including DST folds.

`fixtures/success.json`, `partial.json`, `invalid.json` are wrapper documents with `synthetic=true`, model name and `payload`. Validate the payload, not the wrapper. They are never production calculations and are never automatically loaded by the server. Invalid has the expected field failure recorded.

For integration, commands and engineering decisions read [the handoff](../../apps/api/docs/handoff-ml1-ml2.md). Actual exported schemas live in `contracts/generated/`; use `python -m a2_api.export` to regenerate both validation and serialization forms. Runtime Python validators also enforce cross-field invariants not fully expressible in the structural JSON Schemas.
