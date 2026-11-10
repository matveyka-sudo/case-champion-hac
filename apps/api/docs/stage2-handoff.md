> Исторический выпуск 0.2.0. Для текущего контракта используйте [README](../README.md) и [handoff](handoff-ml1-ml2.md); прежние профили и HTTP не являются production v2.

# BACKEND 0.2.0 — PostgreSQL jobs and worker

First region: **Leningrad Oblast**, then other regions through configuration. No production cities, institutions, services or scenarios were selected by backend. Domain data/calculation still comes from №5/№4, as described in [the handoff contract](handoff-ml1-ml2.md).

## What works

Alembic revision `0001_jobs_runs` creates schema-local jobs/runs with FK, unique `(kind,idempotency_key)`, cache/queue indexes and state checks. Runtime uses parameterized asyncpg SQL; SQLAlchemy is only used by migrations.

The queue supports atomic registration/claim/publication, immutable run IDs, polling, failed-job recovery and version-scoped cache reuse. Same request/key returns the same job, including after completion or deployment; changed payload with that key gives 409. To rerun a failed job, explicitly submit a new key. Cache fingerprints additionally include the entire immutable execution context; idempotency deliberately depends only on the original normalized request.

One worker holds a schema-specific PostgreSQL advisory lock. Calculations run in spawned child processes outside HTTP/event loop, with a default hard timeout of 600 seconds. Timeout/cancellation/SIGTERM stops and reaps the child. A child monitors abrupt parent loss; loss of the advisory-lock session cancels active work. Claim tokens prevent stale publication. Recovery marks interrupted work failed instead of silently repeating it. Worker code version must match the saved job.

GET `/api/v1/runs/{id}/export?format=json|csv` exports a saved result together with its original request/context, versions, hashes, units and evidence. Unknown numbers stay null/empty; CSV textual formula prefixes are escaped. Numeric negative deltas stay numbers. Scenario publication checks exact operations, versions, baseline IDs/origins and before intervals against saved immutable runs.

## Entry points and local dependencies

Run from repository root. The root manifests/lock and Compose were not edited. The current frozen macOS environment lacks greenlet needed by SQLAlchemy asyncio. Temporary local setup:

```sh
uvx --from uv==0.12.17 uv sync --frozen --all-packages --group dev
uvx --from uv==0.12.17 uv pip install --python .venv/bin/python greenlet==3.5.6
export PYTHONPATH=packages/contracts/src:apps/api/src
```

**DEVOPS:** in `apps/api/pyproject.toml`, replace `sqlalchemy>=2,<3` with `sqlalchemy[asyncio]>=2,<3`, then update root `uv.lock`, verify frozen install and `uv pip check`. Until integrated, a frozen sync removes the temporary local greenlet install. Packaging/workspace sources also remain DEVOPS-owned.

Configure a private database URL (do not commit real credentials):

```sh
export A2_DATABASE_URL='postgresql://USER:PASSWORD@HOST:PORT/DATABASE'
export A2_DATABASE_SCHEMA='a2_backend'
.venv/bin/alembic -c apps/api/alembic.ini upgrade head
.venv/bin/alembic -c apps/api/alembic.ini current
.venv/bin/uvicorn a2_api.api.bootstrap:build_app --factory --host 127.0.0.1 --port 8033
```

Schema names are validated before connecting. API lifespan opens/closes a bounded pool and checks migrations; it never migrates production automatically. `A2_CATALOG_PATH` optionally selects metadata configuration. Database-only mode can read existing jobs/runs; new analyses remain 503 until trusted adapters are configured. No real-data adapter or paid model call is claimed in this release.

## Next integration: two BACKEND adapter functions

```python
from a2_api.core.jobs import ClaimedJob, JobContext, JobKind, JobRequest
from a2_contracts import ProfileResult, Comparison

async def prepare(kind: JobKind, request: JobRequest) -> JobContext:
    # Resolve accepted №5 bundle and registered №2/№4 profile/task configuration.
    # Validate IDs, periods, scenario commands and input limits; no CPU routing.
    ...

def calculate(claim: ClaimedJob) -> ProfileResult | Comparison:
    # Load exactly the saved immutable inputs, verify hashes, invoke №4 functions.
    ...
```

`prepare` is fast async validation/I/O. `calculate` is synchronous and runs in the separate calculation process. Colleagues implement domain functions in their packages; BACKEND supplies these thin adapters and maps typed errors. HTTP/model input cannot choose adapters, SQL, code or its own acceptance context.

| JobContext field | Required meaning |
| --- | --- |
| bundle_id / bundle_hash | №5 accepted immutable data, including destinations/services, transfers, fares and evidence, not timetable alone |
| code_version | Actual deployed calculation identifier supplied by DEVOPS |
| profile_hashes / profile_versions | Actual normalized configuration hash and positive integer revision per profile |
| task_hashes | Fixed TaskSet per profile, including unknown tasks in the denominator |
| origin_ids | Exact evaluated IDs per profile; for scenarios taken from saved baselines |
| baseline_run_ids | Empty for analysis, exact requested saved bases for scenario |
| task_count | Positive server-computed case count; >3500 rejected with 422 |

Backend also checks saved scenario baselines and their original bundle/profile hashes; mismatch gives 409, unknown ID 404. Transport semantics, timetable validity and real services remain №4/№5 responsibilities.

After actual adapters exist, configure their real import paths:

```sh
export A2_CODE_VERSION='ACTUAL_CALCULATION_COMMIT'
export A2_PREFLIGHT_HOOK='TRUSTED_BACKEND_ADAPTER_MODULE:prepare'
export A2_CALCULATION_HANDLER='TRUSTED_BACKEND_ADAPTER_MODULE:calculate'
export A2_JOB_TIMEOUT_SECONDS=600
.venv/bin/python -m a2_api.workers.cli worker
```

Uppercase names above are placeholders, not implemented modules. Hook/handler are trusted operator `module:function` configuration, never HTTP/tool parameters. They are unset by default. API requires hook+handler+code together; worker requires database+handler+code. `--once` processes at most one job. Poll interval is `A2_WORKER_POLL_SECONDS`, default 0.5 seconds. API and worker need identical accepted storage and versions.

## Reproduce isolated checks

Verified Compose project: `a2-backend-stage2`, PostgreSQL 17, port 55433. Credentials below are disposable local-test values:

```sh
POSTGRES_DB=a2_backend_stage2 POSTGRES_USER=a2_backend POSTGRES_PASSWORD=a2_backend_local_test POSTGRES_PORT=55433 docker compose --project-name a2-backend-stage2 up -d postgres
export A2_TEST_DATABASE_URL='postgresql://a2_backend:a2_backend_local_test@127.0.0.1:55433/a2_backend_stage2'
PYTHONPATH=packages/contracts/src:apps/api/src .venv/bin/python -m pytest -q -o asyncio_default_fixture_loop_scope=function
```

Tests create/remove only unique `a2_test_<uuid>` schemas. Without the test URL, integration tests explicitly skip; that run is not proof of DB readiness. Synthetic handlers exist only under `apps/api/tests/` and are not production fallback.

The suite checks real upgrade twice/downgrade/re-upgrade while preserving a foreign table; 20 concurrent duplicate submissions; conflicts/cache/rollback/immutable runs; worker timeout, shutdown and SIGKILL; terminated PostgreSQL lock session; code mismatch; scenario consistency; and API→worker→run→export in **separate OS processes**. Python process/signal checks were executed on macOS; PostgreSQL ran in Docker.

Stop only the isolated test container, keeping its volume:

```sh
docker stop a2-backend-stage2-postgres-1
```

## FRONTEND / next work

POST returns 202 `{job_id,status}`; GET jobs every 2 seconds. After 90 seconds offer to return later, without declaring queued/running failed. Succeeded has run_id; failed has safe error/trace. A temporary database failure returns retryable 503. Export is a saved result, not a new calculation.

DEVOPS receives exact migration/API/worker commands, dependency and packaging changes. FRONTEND receives updated OpenAPI and export route. No messages, PR or push were automatically sent. Next backend stage is connecting accepted №5 inputs and №4 functions, then six shared-validator tools and bounded agent; paid-model testing requires verified API access and a spending limit.
