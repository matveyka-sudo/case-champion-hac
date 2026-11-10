# A2 backend — contract 2.0.0

Current baseline: [PRODUCT_SPEC](../../docs/PRODUCT_SPEC.md), [FORMULAS](../../docs/FORMULAS.md), [IMPLEMENTATION](../../docs/IMPLEMENTATION.md), [ASSISTANT](../../docs/ASSISTANT.md) and `config/product.json`. Canonical DTOs live in `a2_contracts.v2`. Historical profile contracts remain isolated for compatibility; they do not define current product behavior.

The backend accepts four analysis kinds, persists jobs and immutable results, runs calculations in a separate worker process and exposes saved scenario proposals. Formulas and routing belong to ML1; accepted snapshots and evidence belong to ML2. No real catalog, fixture or calculation adapter is loaded automatically. Missing integrations return explicit 503; unknown data never becomes a zero score.

## Run

From the repository root:

```sh
uvx --from uv==0.12.17 uv sync --frozen --all-packages --group dev
export PYTHONPATH=packages/contracts/src:apps/api/src:packages/assistant/src
```

Without a database, `/health` and `/api/v1/capabilities` work. Capabilities report actual integration readiness; published methodology does not imply accepted data. The territory catalog, snapshots and cargo profiles are supplied by a trusted synchronous `A2_REGISTRY_HOOK=module:function` returning `ProductRegistry`. The published urban scope comes from `config/product.json`; IDs and geometry come from accepted catalogs. Intercity territories are configured independently. Adding data for further regions does not require new HTTP handlers.

Copy `.env.example` to a private `.env` in the repository root, fill its PostgreSQL credentials and set `DATABASE_URL` to the exposed Compose port. `DATABASE_SCHEMA` is optional and defaults to `a2_backend`. API, worker, Alembic and budget CLI load this file from the process working directory; process variables override it. Start PostgreSQL and apply migrations before API startup:

```sh
docker compose up -d postgres
.venv/bin/alembic -c apps/api/alembic.ini upgrade head
.venv/bin/python -m a2_api.assistant.budget_cli status
.venv/bin/python -m a2_api.assistant.budget_cli set-limit
.venv/bin/python -m a2_api.assistant.budget_cli status
```

Start the API on loopback after migrations:

```sh
.venv/bin/uvicorn a2_api.api.bootstrap:build_app --factory --host 127.0.0.1 --port 8033
```

The budget limit is installed only by `set-limit`, never by startup or migrations. The template keeps both live gates off. `ASSISTANT_HOOK` and `ASSISTANT_BUDGET_HOOK` configure the bundled Alice factory and durable ledger; an empty Yandex key/folder leaves the assistant in safe fallback mode. Set `A2_CODE_VERSION` and trusted `A2_CALCULATION_HANDLER=module:function` together only when a calculation adapter is ready, then start the worker separately:

```sh
.venv/bin/python -m a2_api.workers.cli worker
```

`ProductRegistry.ready_kinds` must explicitly declare integrated kinds. Optional async `A2_PREFLIGHT_HOOK(request, snapshot)` performs bounded readiness checks, never routing.

Worker and API must use the same database schema and code revision. Runtime SQL uses parameterized asyncpg; SQLAlchemy is used only for migrations. A single schema-specific advisory lock owns the worker. Claims are fenced, heartbeated and cancellable; calculations run in killable spawned processes. Interrupted work requires explicit retry rather than unbounded automatic reruns. Results and candidate proposals publish atomically.

Ordinary final result messages retain a 16 MiB wire limit and a default 16 MiB decoded JSON limit. For a reviewed large deployment, the operator can explicitly set `A2_WORKER_RESULT_DECODED_MIB=32` (validated range 16–32); the worker freezes this startup choice into its calculation child. JSON above the wire limit uses a versioned lossless zlib frame. Both sides enforce the independent byte budgets, exact declared decoded size, and a maximum expansion ratio of 128; malformed, truncated, concatenated or trailing streams fail. Decoding and canonical validation run outside the event loop under the job deadline. Canonical result data and hashes are unchanged. The separately admitted historical DEMO transport gate retains its existing 32 MiB allowance. No deployment enables the larger decoded budget automatically.

For HTTPS deployment set `A2_SECURE_COOKIES=true`. Anonymous access is scoped by a random HttpOnly/SameSite cookie; only its hash is stored. The frontend must retain cookies, use the same origin (a reverse proxy for development) and fetch capabilities before parallel mutations. This is session isolation, not an account/login system. Losing the cookie loses access to that session's saved results. Cross-origin mutations are rejected; no wildcard credentialed CORS is enabled.

## HTTP

| Method | Path under `/api/v1` | Behavior |
| --- | --- | --- |
| GET | `/capabilities`, `/territories?level=&query=` | Published configuration and accepted inventory |
| POST | `/analyses` | Four discriminated forms; `Idempotency-Key`; 202 job |
| GET | `/jobs/{job_id}` | Status and saved result ID when complete |
| POST | `/jobs/{job_id}/cancel` | Idempotent cancellation of queued/running work |
| GET | `/analyses/{analysis_id}` | Immutable `AnalysisResult` |
| POST | `/analyses/{analysis_id}/explanation` | Grounded explanation or explicit template fallback |
| GET | `/analyses/{analysis_id}/scenarios` | Saved proposals; no recalculation |
| POST | `/scenarios/{scenario_id}/runs` | Saved server proposal only; body `{"schema_version":"2"}` and idempotency key |
| GET | `/scenario-runs/{run_id}` | Immutable scenario comparison |

All saved resources and assistant tools recheck the access scope. Unknown and unauthorized IDs both return 404. Errors use `error.code/message/field_errors/request_id/retryable`; `X-Request-ID` correlates the request. Raw user inputs, keys and provider response bodies are omitted from errors/logs. Provider failures do not imply absence of transport.

The original user request is used for idempotency; normalized snapshot, methodology, config, source hash, code and access scope define the calculation cache. Live traffic cache entries expire with their sources. Static/typical/live/historical never silently substitute for each other. Accepted snapshots do not prove full edge/category/horizon coverage; the engine must return explicit partial/unknown states.

## Assistant and providers

`a2_assistant.FlashAssistant` supports the fixed read-only tool allowlist through `a2_api.assistant.tools.SavedTools`. The default explanation is a deterministic template. Optional trusted `ASSISTANT_HOOK(service)` returns a configured `FlashAssistant`. The backend includes a PostgreSQL deployment-wide money and token ledger at migration head `0004_alice_budget`; it never installs a budget or invokes a paid API automatically. Tools expose scoped structured data and accepted document search. `EvidenceRepository` provides Russian PostgreSQL full-text retrieval with source/territory/period filters. The departure tool retains saved journeys and can include a separately labelled, partial provider observation through an explicitly injected `RaspDepartureGateway`; observations never become accepted snapshots automatically.

The backend-owned Alice hook is `ASSISTANT_HOOK=a2_api.assistant.alice:build_assistant`; set `ASSISTANT_BUDGET_HOOK=a2_api.assistant.budget:build_budget` to use the bundled ledger. It reads existing Yandex settings, uses `SDKTransport` and closes it with the API lifespan. Missing key, `LLM_ENABLED=false`, unset currency authorization, absent hook or unconfigured budget keeps the endpoint on the saved-result template fallback. An operator must explicitly install the monetary and total-token ceilings; RUB prices are versioned public reference prices and do not confirm the billing account currency. The model selects metric IDs and semantic codes; the backend assembles all values, units, evidence and narrative from the saved result. See [Alice integration](docs/alice-integration.md) for CLI commands, offline checks and live rollout gates.

The separately injectable `a2_api.providers.rasp.RaspProvider` has bounded paging, retries, rate limits, cache and explicit direct-only/partial coverage. Every key-bearing Search/Copyright attempt requires the [shared account/day quota](docs/rasp-quota.md): at most 500 including retries, with an explicit known usage baseline in a separate central ledger. It never supplies a complete multimodal index. Connecting it to ML2 ingestion or an engine adapter is an explicit integration step.

## Verify and export

```sh
export PYTHONPATH=packages/contracts/src:apps/api/src:packages/assistant/src
.venv/bin/python -m pytest -q -o asyncio_default_fixture_loop_scope=function
.venv/bin/ruff check packages/contracts/src packages/contracts/tests apps/api/src apps/api/tests packages/assistant/src packages/assistant/tests
.venv/bin/ruff format --check packages/contracts/src packages/contracts/tests apps/api/src apps/api/tests packages/assistant/src packages/assistant/tests
MYPYPATH=$PYTHONPATH .venv/bin/python -m mypy --explicit-package-bases packages/contracts/src/a2_contracts apps/api/src/a2_api packages/assistant/src/a2_assistant
.venv/bin/python -m a2_api.exports.schemas --output contracts/generated
.venv/bin/python -m a2_api.exports.schemas --output contracts/generated --check
```

Set `A2_TEST_DATABASE_URL` to a dedicated writable PostgreSQL database to run database/process checks; each test creates and drops its own schema. Without it these checks are skipped. All current test geography, times and scores are explicitly synthetic. Tests do not establish readiness of actual city data or paid model capabilities.

`openapi.json` is the canonical production API. `v2.*.schema.json` are current DTOs. `legacy-openapi.json` and schemas without the `v2.` prefix describe historical contracts only. Legacy deployment requires explicit `A2_CONTRACT_VERSION=legacy` and must not share a public surface with the canonical deployment.

## Integration dependencies

See [the existing handoff](docs/handoff-ml1-ml2.md) and [backend task status](../../docs/tasks/backend.md). DEVOPS owns manifests and lock: declare internal workspace/source dependencies, add `pydantic` to assistant, expose `packages/assistant/src` until packaging is enabled, SQLAlchemy asyncio support and greenlet are now declared in the API manifest/root lock under the user-authorized exception and verified in a clean frozen environment. PostgreSQL/PostGIS provisioning, CI and production billing are separate dependencies.

The audit adds migration `0003_evidence_search`, followed by `0004_alice_budget`; apply the full migration head before starting this version. Import/publication contracts, tool response changes and the independently reproduced frozen-environment dependency blocker are documented in [the existing handoff](docs/handoff-ml1-ml2.md). Generated explanations use server-approved qualitative sentences and saved numeric claims; arbitrary model prose is intentionally rejected.

Waydex integration: [frontend handoff](docs/frontend-integration-handoff.md) defines exact UI→API mapping, optional saved map metadata, scoped question/selection explanation body, cookies/polling and scenario flow. Latest clean frozen regression: 560 tests passed with PostgreSQL; actual frontend browser integration and production data remain separate team acceptance.

Rasp response validation: echoed search codes and origin-local query date must match; station requests additionally require exact segment endpoints (city codes are checked through the echoed city scope). Conflicting duplicate service/departure identities reject the observation; identical rows deduplicate. Responses request `Accept-Encoding: identity` and reject any actual non-identity `Content-Encoding` before reading, including gzip/deflate/br and coding lists, even if upstream ignores the request header. Compression is deliberately unsupported so HTTPX cannot allocate an expanded gzip body before the cap. Uncompressed transport chunks are checked before copying or JSON parsing, without a fixed-size rechunk buffer. The default cap is 2 MiB per HTTP response, configurable by `max_response_bytes` up to 16 MiB, and applies to successful and error responses without trusting Content-Length. Timeout/cancellation closes the stream; errors contain no provider body. Observations remain partial and never become accepted snapshots.

## Module boundaries after the fix-fullstack refactor

The runtime stack remains FastAPI/Pydantic v2, asyncpg and parameterized raw SQL.
SQLAlchemy is used only by Alembic. No new ORM, task queue, DTO copy or dependency
was introduced. Existing URLs, operation IDs, cookies, security policy and status
transitions are preserved.

```text
a2_api/
  app.py                    stable ASGI app + configured/legacy factory imports
  api/
    application.py          canonical FastAPI factory, middleware, errors, health
    dependencies.py         request-local service lookup and header type
    bootstrap.py            configured API lifespan and trusted hooks
    legacy.py               isolated historical HTTP surface
    v2/
      catalog.py            capabilities and territories
      analyses.py           analysis submit/read
      jobs.py               polling and cancellation
      scenarios.py          proposals, run submission and scenario result
      explanations.py       scoped saved-result explanation
  services/
    analyses.py             normalization, readiness, enqueue and saved-result access
    scenarios.py            frozen proposal and baseline orchestration
    jobs.py                 historical queued service/preflight adapter
    catalog.py, legacy.py   historical metadata and unavailable service boundary
  db/
    connection.py           asyncpg pool, schema readiness and failure cleanup
    rows.py                 SQL row → canonical DTO, hash and access-scope checks
    validation.py           pure publication integrity guards, no SQL
    repositories/
      jobs.py               queue/cache/idempotency/claim/lease/publication/cancel
      runs.py               immutable result reads and baseline checks
      scenarios.py          saved proposal SQL and scenario publication checks
      evidence.py           accepted evidence publication and Russian FTS
      budget.py             deployment ledger SQL and reservation accounting
  workers/
    calculation.py          spawned CPU process, IPC size/time bounds and termination
    runner.py               single-worker claim/heartbeat/cancel/publication lifecycle
    cli.py                  configured worker entrypoint and signals
  core/
    config.py               RuntimeSettings
    catalog.py, registry.py internal configuration and published product catalogs
    jobs.py                 persisted job contexts and worker payload types
    errors.py, scope.py     safe domain errors and pure territory scope extraction
  assistant/
    alice.py                opt-in SDK factory and deployment budget validation
    explanations.py         saved-result/selection scope and template/model orchestration
    tools.py                eight read-only scoped tool adapters
    budget.py, budget_cli.py operator settings/hook and admin CLI
  exports/
    schemas.py              deterministic OpenAPI and JSON Schema export
    results.py              saved legacy JSON/CSV export
  providers/                existing Rasp HTTP boundary and departure gateway
```

### Request and transaction flow

`a2_api.api.application:create_app --factory` builds an unconfigured canonical v2 ASGI app.
`a2_api.api.bootstrap:build_app --factory` resolves runtime settings/hooks and
opens the pool during lifespan. HTTP middleware retains session/CSRF/error policy;
thematic routers obtain `request.app.state.services`, call application services
and return canonical DTOs. Router instances hold no shared service instance.
Multiple FastAPI applications therefore retain separate services and sessions.

Application services normalize/authorize requests and call repositories; they
contain no SQL or FastAPI Response construction. SQL row checks and publication
integrity remain on the persistence boundary. The configured worker uses the same
repositories and dispatches trusted calculation handlers in spawned processes.

`JobRepository` is the compatible public repository surface. Its concrete
`ScenariosRepository → RunsRepository` bases group existing operations around the
same pool and preserve `self` dispatch, including subclass publication hooks.
There is no nested connection acquisition introduced by this inheritance.

| SQL area | Where to look | Transaction owner |
| --- | --- | --- |
| Replay/cache/queue | `db/repositories/jobs.py:replay/enqueue` | enqueue opens its original transaction |
| Claim and lease | `jobs.py:claim/heartbeat/cancel/recover_orphans` | original claim/cancel transactions and conditional updates |
| Result publication | `jobs.py:succeed` | one transaction inserts run/proposals and finishes job |
| Saved results | `runs.py:get_run/_read_run/get_run_parameters` | original repeatable-read read transaction and optional FOR SHARE |
| Scenario commands | `scenarios.py:get_proposal/_save_proposals/_check_scenario_inputs` | explicit save transaction or caller's publication connection |
| Evidence | `evidence.py:publish/search` | original immutable ingestion transaction; scoped FTS reads |
| Alice budget | `budget.py:configure_budget/reserve/record_usage/read_budget_status` | original singleton row locks and accounting transactions |
| Pool/schema checks | `db/connection.py:open_pool` | lifespan/CLI closes pool; no application transaction hidden here |

The cross-entity run INSERT deliberately stays inside `jobs.succeed`: it is part
of atomic job publication. `_save_proposals` receives that SAME connection, not a
new pool checkout. `_publication_connection` still uses the session owning the
worker advisory lock; session loss aborts publication. `FOR UPDATE SKIP LOCKED`,
lock ordering, placeholders, lease fencing, cancellation and immutable hashes are
unchanged. There is no sessions table or independent cache table: session hashes
and versioned cache state remain in the existing jobs/runs/proposals columns.

### DTO ownership and canonical imports

Public Pydantic request/result/error schemas remain exclusively in
`packages/contracts/src/a2_contracts/v2/`. Backend configuration models and internal
persisted job/worker types are in `core/` and the assistant component; they are not
copies of canonical AnalysisRequest/AnalysisResult/ScenarioResult/AcceptedData.
`db/rows.py` maps asyncpg rows into those existing models and checks stored hashes.
No ORM table models were created.

| Retired root file | Canonical location |
| --- | --- |
| `app.py`, `api_v2.py` | `api/bootstrap.py`, `api/application.py`, `api/legacy.py`, `api/v2/` |
| `analysis_service.py`, `job_services.py` | `services/analyses.py`, `services/scenarios.py`, `services/jobs.py`, `assistant/explanations.py` |
| `configuration.py`, `product_registry.py`, `jobs.py`, `env_settings.py` | `core/catalog.py`, `services/catalog.py`, `core/registry.py`, `core/jobs.py`, `core/env_settings.py` |
| `storage.py`, `evidence_search.py`, `deployment_budget.py` | `db/repositories/`, `db/rows.py`, `db/validation.py` |
| `runtime.py`, `worker.py` | `core/config.py`, `db/connection.py`, `api/bootstrap.py`, `workers/cli.py`, `workers/runner.py`, `workers/calculation.py` |
| `alice.py`, `tool_services.py`, `budget_hook.py`, `budget_cli.py` | `assistant/alice.py`, `assistant/tools.py`, `assistant/budget.py`, `assistant/budget_cli.py` |
| `export.py`, `run_exports.py` | `exports/schemas.py`, `exports/results.py` |

Tests patch `a2_api.workers.runner.calculate` and
`a2_api.assistant.alice.SDKTransport` at their canonical injection points.
Configuration hooks use `a2_api.assistant.alice:build_assistant` and
`a2_api.assistant.budget:build_budget`. The existing HTTP contracts, repository
operations, scope checks, worker publication behavior and exported schemas remain
unchanged. The [complete path migration map](docs/root-module-migration.md)
lists every removed root file and its callers.

### Refactor verification

Source baseline: `5311617a7634e04eec6936cc103986e3f39ec3b8` in the main PyCharm
checkout, branch fix-fullstack. Before: Ruff/format/mypy clean; without database
200 passed/148 skipped; on a dedicated PostgreSQL17.11 cluster,348 passed.
The initial database attempt exposed a locally missing declared greenlet extra.
`uv sync --frozen --all-packages --group dev` installed locked greenlet3.5.6;
no manifest/lock update was required. Original sources were preserved before edits,
verified against HEAD, and rerun read-only after environment repair; implementation
work stayed in the main checkout.

After: **826 passed,0 skipped** across API/contracts/assistant, including28 new
relocation guards. The API portion is376 tests (348 original +28 guards).
All293 schema artifacts are byte-identical, including production/legacy OpenAPI;
canonical generated files were not rewritten. Fixtures captured185 signatures,
47 SQL literals and57 SQL binding/transaction AST fingerprints from the original
source, not from the refactored implementation. Signature comparison normalizes
only relocated module prefixes, unordered frozenset repr and function addresses.
Fresh import order tests run from another cwd; ORM/reverse dependency guards pass.

Real TCP Uvicorn configured startup, health/capabilities/OpenAPI, PostgreSQL pool,
worker `--once` on an empty disposable queue, and budget `status` smoke passed. Existing database tests include
migration upgrade/downgrade, concurrent idempotency, cancellation, worker parent/
lease loss, stale publication, proposal atomicity, ownership, FTS and budget.
Frontend code stayed unchanged:36 unit/API tests, build, API freshness and21 E2E
passed in apps/web. Verified old ignored frontend caches were removed with explicit
allowlisted paths; no tracked frontend root remains.

Run frozen setup before invoking the source checkout directly:

```sh
uv sync --frozen --all-packages --group dev
export PYTHONPATH=.:apps/api/src:packages/contracts/src:packages/assistant/src
# A2_TEST_DATABASE_URL must point to a dedicated disposable database.
.venv/bin/python -m pytest apps/api/tests packages/contracts/tests packages/assistant/tests -q -o asyncio_default_fixture_loop_scope=function
.venv/bin/ruff check apps/api/src apps/api/tests
.venv/bin/ruff format --check apps/api/src apps/api/tests
MYPYPATH=packages/contracts/src:apps/api/src:packages/assistant/src .venv/bin/python -m mypy --explicit-package-bases packages/contracts/src/a2_contracts apps/api/src/a2_api packages/assistant/src/a2_assistant
.venv/bin/python -m a2_api.exports.schemas --output contracts/generated --check
```

Source/PYTHONPATH deployment semantics remain unchanged because manifests still
use package=false. Real engine/ML2 hooks, accepted data, production provider/Alice
credentials and production E2E remain integration dependencies. This refactor does
not enable those integrations or claim a paid live call. No new independent
functional defect was fixed as part of the structural change.
