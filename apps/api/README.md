# A2 backend: contract release 0.1.0

This release implements canonical DTOs, configurable area/profile catalogs, the HTTP validation boundary, synthetic fixtures and reproducible schema export. Calculation adapters, persistent jobs, PostgreSQL migrations and the model agent are subsequent integration stages. An unconfigured service returns a typed 503, never a fake route or job.

## Local environment

Run from repository root. The installed global uv may differ from the version required by this repository; use the pinned invocation. This does not change manifests or the lock.

```sh
uvx --from uv==0.12.17 uv sync --frozen --all-packages --group dev
export PYTHONPATH=packages/contracts/src:apps/api/src
.venv/bin/uvicorn a2_api.app:create_configured_app --factory --host 127.0.0.1 --port 8033
```

`GET /health` is process liveness only. `/docs` and `/openapi.json` describe the real routes. Default `/api/v1` data/calculation endpoints return 503 until their service is configured. `/profiles` and `/study-areas` work with an explicit catalog:

```sh
export A2_CATALOG_PATH=apps/api/examples/catalog.synthetic.json
.venv/bin/uvicorn a2_api.app:create_configured_app --factory --host 127.0.0.1 --port 8033
```

The example is **synthetic only**. `demo-*` IDs, destinations, service type and four city placeholders are not a list of real cities, institutions, accepted data or completed calculations. The number of placeholders intentionally differs from the three-city minimum demonstration. The example is never loaded automatically. No paid API is called.

Production configuration uses the same `CatalogConfig` schema. `load_catalog(path)` validates the entire document before serving it. Profile references must match an existing study-area revision and timezone; task sets must match profile revisions/periods. Versions and hashes change when scope or rules change. A malformed selected configuration fails startup instead of silently falling back to demo data.

## Region decision and sources

The user selected **Leningrad Oblast as the first region**, then further regions. No region or city names are hard-coded in calculation/HTTP behavior. `StudyArea.region_ids` and full `city_ids` are separate from `demo_city_ids`, which must be a subset. IANA timezones are configuration, concrete timestamps have an offset, feed IDs retain their namespaces. Adding another region means another area/configuration and an accepted bundle, not a new HTTP implementation. Cross-region connections must remain explicit in the accepted data.

The local A2 specification 1.1 PDF (SHA-256 `a5be80001a0999cf0e817ed22d6b412a4f4e4450ac863534ce828e21380c24b4`) describes an older Kaliningrad pilot. Pages 1, 3, 17 and 18 distinguish the minimum three-territory demonstration and the historical six-city extension. Neither is the accepted number of cities in Leningrad Oblast. The checked PDF/DOCX/MD/CSV and archives contain no agreed Leningrad city list or named medical/work/study targets. Their selection remains with the coordinator/data owners; backend accepts it through configuration.

Time/rules already given by the requirements (PDF pages 5, 17): regular departure >=06:00, arrival 08:30–09:00, activity until 18:00, return <=21:00; medical windows 10:00/12:00/15:00, visit 60 minutes, arrival 15–45 minutes early, departure >=07:00, return <=20:00. Both use <=90 minutes per side, <=2 transfers and <=20 minutes per final access segment. Budget is off with `null`, step-free is off with `false`; enabled unknown conditions remain unknown. These are scenario parameters, not actual institutional schedules. Their original dates/timezone are historical regional configuration, not defaults silently applied to Leningrad.

## Checks and schema export

```sh
export PYTHONPATH=packages/contracts/src:apps/api/src
.venv/bin/python -m pytest -q -o asyncio_default_fixture_loop_scope=function
.venv/bin/ruff check packages/contracts/src packages/contracts/tests apps/api/src apps/api/tests
.venv/bin/ruff format --check packages/contracts/src packages/contracts/tests apps/api/src apps/api/tests
MYPYPATH=packages/contracts/src:apps/api/src .venv/bin/python -m mypy --explicit-package-bases packages/contracts/src/a2_contracts apps/api/src/a2_api
.venv/bin/python -m a2_api.export --output contracts/generated
.venv/bin/python -m a2_api.export --output contracts/generated --check
```

The pytest CLI override avoids the pre-existing unset async-fixture loop-scope warning without editing DEVOPS-owned root configuration. API tests use real FastAPI validation/serialization, injected test catalogs and synthetic inputs. Model tests cover extra fields, finite values, offsets, conditional activity requests, partial scores, scenario consistency, accepted-data references and medical availability. Export tests compare two independently generated directories and resolve local references.

Schemas describe field structure, ranges, nullability and discriminated operations. Cross-field/context checks remain mandatory in Python: schema validation alone does not accept a bundle, prove a timetable, resolve IDs or establish scenario compatibility. See [handoff contract](docs/handoff-ml1-ml2.md) for exact integration requirements.

## DEVOPS handoff

New source packages are `packages/contracts/src/a2_contracts` and `apps/api/src/a2_api`, both have `py.typed`. The workspace currently uses `package=false`, so local commands explicitly set `PYTHONPATH`. DEVOPS must enable packaging and declare workspace dependencies before production distribution; BACKEND did not edit any manifest or `uv.lock`.

API entrypoint is `a2_api.app:create_configured_app` with `--factory`; schema entrypoint is `python -m a2_api.export`. Backend uses port 8033 in examples; choose a free role-specific port locally. Add the check commands above to CI after packaging. Worker entrypoint and PostgreSQL migration commands will be handed off once implemented and tested; none are claimed in this release.
