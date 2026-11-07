# BACKEND implementation plan

> For agentic workers: execute using superpowers:executing-plans. Independent contract review may use up to two read-only subagents; no overlapping edits.

**Goal:** implement the BACKEND role in reviewable stages, starting with canonical contracts and a real API/schema export.

**Architecture:** Pydantic DTOs independent of HTTP, engine, storage and LLM. FastAPI injects domain services; absent accepted data or engine produces typed errors, never synthetic successful calculations. PostgreSQL jobs and one worker follow after the contract gate.

**Tech stack:** Python 3.12, existing locked Pydantic/FastAPI, asyncpg/Alembic for later persistence.

**Spec:** `packages/contracts/specs/*.md`, `docs/ARCHITECTURE.md`, `docs/tasks/backend.md`, A2 specification 1.1 (pages 8–13, 16–17). User requested execution in order on 2026-11-07.

## Global constraints

- Work in the separate clone on `work/backend`; edit BACKEND paths only.
- Do not edit any `pyproject.toml` or `uv.lock`; packaging changes go to DEVOPS.
- `/api/v1`; regular, medical, intercity are separate profiles, no aggregate score.
- All public datetimes require an offset; service seconds may exceed 86400.
- Unknown values are null/unknown, never fabricated zero, path or confirmed timetable.
- D1–D3 remain pending; fixtures are marked synthetic and are never loaded as real data.
- API model access and spending are D4; no paid calls before a budget is supplied.

## Review focus

- Unrecognised fields and non-finite numbers must be rejected.
- Conditional activity/return requirements must hold for regular and medical.
- Partial intervals must not accidentally become exact scalar scores.
- Scenario operations must preserve identity, complete input and version links.
- Missing services must return an error envelope, not a successful empty calculation.

## Stage 1: canonical contracts and HTTP boundary

Files: `packages/contracts/src/a2_contracts/{common,dataset,profiles,journeys,results,scenarios,api,assistant}.py`, public `__init__.py`; tests and synthetic fixtures in `packages/contracts/tests/` and `packages/contracts/fixtures/`.

- [x] Write failing tests for offset-aware time, finite values, extra fields, versions, accepted metadata, positive weights, medical/regular requirements and score bounds.
- [x] Run tests and observe missing implementation; implement DTOs with shared validation, explicit nullability, documented engineering decisions and public imports.
- [x] Validate success and partial fixtures; reject invalid fixture. Tests enforce mandatory provenance/hash/version fields missing from shortened PDF examples.
- [x] Keep semantic arithmetic in ML1; validate shape and consistency of supplied results only.

Files: `apps/api/src/a2_api/{app,services,export}.py`, `apps/api/tests/`, `contracts/generated/`, module READMEs.

- [x] Write failing API tests for valid requests, malformed JSON/payload, 404/409/503 envelopes, trace ID, and no fixture fallback.
- [x] Implement `create_app(services=None)` and documented `/api/v1` routes backed by injectable service interfaces. Default unavailable engine/data returns 503, not jobs that cannot execute.
- [x] Implement deterministic `python -m a2_api.export --output PATH`; publish actual FastAPI OpenAPI plus standalone JSON Schemas for canonical models.
- [x] Export twice to temporary directories; compare bytes, check all documented operations and valid references.
- [x] Run complete pytest suite, ruff and mypy on implemented modules; record exact results, package import workaround and DEVOPS handoff.

## Stage 2: PostgreSQL jobs and one worker

Files: API persistence, migrations, worker and their tests in `apps/api/`.

- [x] Add migrations for versioned runs, jobs and idempotency; test on a distinct PostgreSQL database.
- [x] Atomically register Idempotency-Key + canonical payload hash; repeat returns same job, conflicting payload returns 409 `idempotency_conflict`.
- [x] Claim jobs transactionally with one worker, execute engine outside event loop, publish immutable results, retain typed errors and enforce 600-second hard timeout.
- [x] Add GET polling and export JSON/CSV, cache keys include all domain versions/parameters/code.
- [x] Request DEVOPS packaging/entrypoint changes by exact paths and commands; do not edit manifests yourself.

## Stage 3: tools, agent and trace

Files: `packages/assistant/`, API integration and tests.

- [ ] Share contract and service validators across REST and six allowlisted tools.
- [ ] Implement bounded tool orchestration and grounded structured responses; no arbitrary SQL, shell or URL loading.
- [ ] Enforce 6 calls, 2 scenario launches, 90-second interactive budget, 4000 output tokens, 1 retry; paid-model execution requires D4 access/budget.
- [ ] Log trace/call/run and versions without secrets, cost/tokens when available, retention 7 days.
- [ ] Test unavailable LLM, injection in evidence, unknown calendar, invalid scenario and numeric grounding; forms remain usable independently.

## Handoff and limits

Stage 1 is a contract release, not a complete transport service. ML1 supplies `find_activity_journeys`, `evaluate_cases`, `compare_scenario`; ML2 supplies `load_bundle` and accepted evidence. Their absence is exposed as 503. D1–D3 block real profile runs, not DTOs or the HTTP boundary. Later stages require a runnable PostgreSQL instance and accepted engine/data integrations. Publishing schemas does not assert team approval of field semantics; list engineering choices explicitly for colleagues.

## Execution record — 2026-11-07

- Stage 1 complete locally: DTOs, 3 marked fixtures, actual API routes, configurable catalog, OpenAPI and validation/serialization JSON Schemas.
- User steering: first region is Leningrad Oblast. Full city scope and demo subset are distinct. No real cities, institutions or services were chosen by BACKEND. Configuration uses `StudyArea` and version-linked `ProfileConfig`.
- Ruling: keep temporary explicit PYTHONPATH commands while workspace members use package=false; manifests/lock remain untouched because DEVOPS owns packaging.
- Ruling: profile versions are positive integers; medical selects service_type_id; add_trip requires new_trip_id. Machine metric names are activity_feasibility/time_decay. These are documented contract proposals requiring consumer handoff, not claims of team consensus.
- Ruling: no accepted job/result is produced by the unconfigured service. It returns 503 until actual adapters and persistent worker are connected.
- Independent review found contradictory feasibility, impossible comparison deltas, missing profile provenance and unrepresentable medical hours. Regression tests reproduced each failure before fixes. UTC elapsed comparisons also cover a DST fold for future regions.
- Final suite: 132 passed. Ruff check/format and strict mypy (14 source files) passed. Two independent exports match; generated exports pass --check.
- Real-process smoke: Uvicorn factory + explicit synthetic catalog; health/areas/profiles return 200 with trace headers; unavailable coverage returns 503 engine_unavailable. Process shut down after smoke.
- Stages 2 and 3 remain pending; this record does not claim persistent jobs, migrations, real routing, accepted regional data or a working LLM agent.

## Stage 2 execution record — 2026-11-07

Stage 2 now complete locally: PostgreSQL migrations/jobs/runs, idempotency/cache, one killable version-checked worker, API pool lifecycle, polling and JSON/CSV exports. See `stage2-jobs-plan.md` and `stage2-handoff.md` for decisions, exact commands, regression checks and remaining dependencies. Final result: 194 tests passed on isolated PostgreSQL 17; strict mypy, ruff, pip compatibility and schema-export checks passed. User `.idea` changes and DEVOPS manifests/lock were preserved. Real №4/№5 adapters and Stage 3 agent are still pending; synthetic queue checks do not claim real regional results.
