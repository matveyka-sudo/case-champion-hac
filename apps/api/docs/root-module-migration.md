# Canonical backend module paths

The package root now contains only `__init__.py`, `py.typed`, and the `api`,
`assistant`, `core`, `db`, `exports`, `providers`, `services`, and `workers`
packages. The retired root files below are removed. Existing behavior is tested
through the canonical implementation; no replacement facade is installed.

| Retired root file | Canonical implementation | Migrated callers and behavior |
| --- | --- | --- |
| `alice.py` | `assistant/alice.py` | `ASSISTANT_HOOK`, offline fake `SDKTransport` seam, one-call gate, safe fallback |
| `analysis_service.py` | `services/analyses.py`; `assistant/explanations.py` | API and tool tests use the same `AnalysisService` and scoped explanation path |
| `api_v2.py` | `api/application.py`; `api/v2/` | HTTP tests target the existing v2 factory and routers |
| `app.py` | `api/bootstrap.py`; `api/application.py`; `api/legacy.py` | Uvicorn, installed-package smoke, legacy HTTP tests and CI use the appropriate factory |
| `budget_cli.py` | `assistant/budget_cli.py` | Operator CLI and tests use the same read-only `status` and explicit `set-limit` commands |
| `budget_hook.py` | `assistant/budget.py` | `ASSISTANT_BUDGET_HOOK` and tests use the PostgreSQL-backed hook |
| `configuration.py` | `core/catalog.py`; `services/catalog.py` | Catalog models/loader and legacy catalog service retain their separate responsibilities |
| `deployment_budget.py` | `db/repositories/budget.py` | Reserve, record usage, tariff and limit tests target the existing ledger implementation |
| `env_settings.py` | `core/env_settings.py` | Alias precedence and conflict tests target the same settings base |
| `evidence_search.py` | `db/repositories/evidence.py` | Evidence and read-only tool tests target scoped repository queries |
| `export.py` | `exports/schemas.py` | CLI, CI and docs use the deterministic schema exporter |
| `job_services.py` | `services/jobs.py` | Queue/legacy service tests retain their behavior checks |
| `jobs.py` | `core/jobs.py` | Worker, repository and scenario tests use the original job DTO/hash functions |
| `product_registry.py` | `core/registry.py` | Registry and integration tests use the original product configuration |
| `run_exports.py` | `exports/results.py` | JSON/CSV export tests target the same implementation |
| `runtime.py` | `core/config.py`; `db/connection.py`; `api/bootstrap.py`; `workers/cli.py` | Settings, pool, ASGI lifespan and worker command tests use their owning modules |
| `storage.py` | `db/repositories/`; `db/rows.py`; `db/validation.py` | PostgreSQL tests retain repository, row, transaction and publication checks |
| `tool_services.py` | `assistant/tools.py` | Read-only scoped tool tests use the same adapter |
| `worker.py` | `workers/runner.py`; `workers/calculation.py` | Worker tests patch `workers.runner.calculate`; lease, cancellation and publication tests remain |

Operational entrypoints:

```text
API             a2_api.api.bootstrap:build_app --factory
Worker          python -m a2_api.workers.cli worker
Budget          python -m a2_api.assistant.budget_cli status
Schema export   python -m a2_api.exports.schemas --output contracts/generated --check
Alice hook      a2_api.assistant.alice:build_assistant
Budget hook     a2_api.assistant.budget:build_budget
```

The current OpenAPI and all 293 generated artifacts remain unchanged. The
PostgreSQL schema and existing budget ledger records are not migrated or reset.
`api/legacy.py` and `services/legacy.py` remain functional modules. The
historical audit report records commands executed before this relocation; it
is retained as evidence, with current commands documented here and in the API
README.
