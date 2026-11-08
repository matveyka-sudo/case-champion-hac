# A2 backend — contract 2.0.0

Current baseline: [PRODUCT_SPEC](../../docs/PRODUCT_SPEC.md), [FORMULAS](../../docs/FORMULAS.md), [IMPLEMENTATION](../../docs/IMPLEMENTATION.md), [ASSISTANT](../../docs/ASSISTANT.md) and `config/product.json`. Canonical DTOs live in `a2_contracts.v2`. Historical profile contracts remain isolated for compatibility; they do not define current product behavior.

The backend accepts four analysis kinds, persists jobs and immutable results, runs calculations in a separate worker process and exposes saved scenario proposals. Formulas and routing belong to ML1; accepted snapshots and evidence belong to ML2. No real catalog, fixture or calculation adapter is loaded automatically. Missing integrations return explicit 503; unknown data never becomes a zero score.

## Run

From the repository root:

```sh
uvx --from uv==0.12.17 uv sync --frozen --all-packages --group dev
export PYTHONPATH=packages/contracts/src:apps/api/src:packages/assistant/src
.venv/bin/uvicorn a2_api.app:create_configured_app --factory --host 127.0.0.1 --port 8033
```

Without a database, `/health` and `/api/v1/capabilities` work. Capabilities report actual integration readiness; published methodology does not imply accepted data. The territory catalog, snapshots and cargo profiles are supplied by a trusted synchronous `A2_REGISTRY_HOOK=module:function` returning `ProductRegistry`. The published urban scope comes from `config/product.json`; IDs and geometry come from accepted catalogs. Intercity territories are configured independently. Adding data for further regions does not require new HTTP handlers.

Set `A2_DATABASE_URL`, a role-specific `A2_DATABASE_SCHEMA`, `A2_CODE_VERSION`, and trusted `A2_CALCULATION_HANDLER=module:function` together to enable the configured worker adapter. `ProductRegistry.ready_kinds` must explicitly declare integrated kinds. Optional async `A2_PREFLIGHT_HOOK(request, snapshot)` performs bounded readiness checks, never routing. Apply migrations before startup:

```sh
.venv/bin/alembic -c apps/api/alembic.ini upgrade head
.venv/bin/python -m a2_api.runtime worker
```

Worker and API must use the same database schema and code revision. Runtime SQL uses parameterized asyncpg; SQLAlchemy is used only for migrations. A single schema-specific advisory lock owns the worker. Claims are fenced, heartbeated and cancellable; calculations run in killable spawned processes. Interrupted work requires explicit retry rather than unbounded automatic reruns. Results and candidate proposals publish atomically.

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

`a2_assistant.FlashAssistant` supports the fixed read-only tool allowlist through `a2_api.tool_services.SavedTools`. The default explanation is a deterministic template. Optional trusted `A2_ASSISTANT_HOOK(service)` returns a configured `FlashAssistant`. Live calls require keys/folder, explicit model capability verification and an external durable deployment spending guard; no process-local budget is sufficient. The current integration does not implement a billing ledger or invoke paid APIs automatically. Tools expose saved, scoped structured data; the departure tool reads saved journeys only and reports partial coverage.

The separately injectable `a2_api.providers.rasp.RaspProvider` has bounded paging, retry/quota/rate limits, cache and explicit direct-only/partial coverage. It never supplies a complete multimodal index. Connecting it to ML2 ingestion or an engine adapter is an explicit integration step, not an HTTP side effect.

## Verify and export

```sh
export PYTHONPATH=packages/contracts/src:apps/api/src:packages/assistant/src
.venv/bin/python -m pytest -q -o asyncio_default_fixture_loop_scope=function
.venv/bin/ruff check packages/contracts/src packages/contracts/tests apps/api/src apps/api/tests packages/assistant/src packages/assistant/tests
.venv/bin/ruff format --check packages/contracts/src packages/contracts/tests apps/api/src apps/api/tests packages/assistant/src packages/assistant/tests
MYPYPATH=$PYTHONPATH .venv/bin/python -m mypy --explicit-package-bases packages/contracts/src/a2_contracts apps/api/src/a2_api packages/assistant/src/a2_assistant
.venv/bin/python -m a2_api.export --output contracts/generated
.venv/bin/python -m a2_api.export --output contracts/generated --check
```

Set `A2_TEST_DATABASE_URL` to a dedicated writable PostgreSQL database to run database/process checks; each test creates and drops its own schema. Without it these checks are skipped. All current test geography, times and scores are explicitly synthetic. Tests do not establish readiness of actual city data or paid model capabilities.

`openapi.json` is the canonical production API. `v2.*.schema.json` are current DTOs. `legacy-openapi.json` and schemas without the `v2.` prefix describe historical contracts only. Legacy deployment requires explicit `A2_CONTRACT_VERSION=legacy` and must not share a public surface with the canonical deployment.

## Integration dependencies

See [the existing handoff](docs/handoff-ml1-ml2.md) and [backend task status](../../docs/tasks/backend.md). DEVOPS owns manifests and lock: declare internal workspace/source dependencies, add `pydantic` to assistant, expose `packages/assistant/src` until packaging is enabled, and use `sqlalchemy[asyncio]>=2,<3` with a refreshed lock. The local environment currently has `greenlet==3.5.6`; a frozen sync removes that temporary extra until the manifest is corrected. PostgreSQL/PostGIS provisioning, CI and production billing are separate dependencies.
