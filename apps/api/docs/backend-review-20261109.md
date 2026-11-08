# Backend independent-review corrections, 2026-11-09

Pre-publication audit snapshot (the following no-push/no-commit statements describe the audit stage):

Local isolated checkout: `/Users/kirito/backend-review-isolated-1109`, branch `work/backend`. No branch commits, push, merge, rebase, reset, review publication, discussion resolution or changes to shared checkout. Verification commit-tree objects have one parent, are not branch commits or merge commits. Temporary PostgreSQL 16 container `backend-review-1109-pg` uses its own database/volume/port 55439; tests create UUID schemas. No production database or paid provider traffic.

## Source revisions and tree construction

| Input | Published SHA |
| --- | --- |
| PR #8 / work/backend | 18f5281b9e130981bd99c88bcfa963d94e72f086 |
| dev | c78343563193ab3e3bf270b714cad423bd69f549 |
| merge base backend/dev | 2e769c7b8ccc55088d8ffdf5695af2f5787ea801 |
| work/ml1 | c6531ad18176052bcdc11e856f076c78df587606 |
| work/ml2 (read-only handoff evidence) | 26a4f5a283f054560123dab87a05806fc253bc9e |

Refs were rechecked with ls-remote and remained unchanged. GitHub connector returned a stale base_sha (2e769c7) while remote dev is c783435; integration uses fetched Git refs, not the stale metadata. PR diff starts at merge base, whereas tree A includes current dev frontend commits.

Private index snapshot -> single-parent commit-tree object -> `git merge-tree --write-tree origin/dev <snapshot>` gives A. Single-parent verification object with A's tree and snapshot ancestry -> `git merge-tree --write-tree <A-object> origin/work/ml1` gives B. Both operations exit 0, no conflicts. This is history-aware three-way composition, not an engine directory overlay. No merge commit or branch update is created. Engine in B is byte-identical to published ML1. Frontend is preserved from dev. uv.lock differs from dev only by the previously published backend dependency fix; this task changes no manifest/lock.

Tested code trees: A `6f14e3530b681cb5cfbe47eb7b2e85ac338c7783`, B `eb50b7a8fb06fde376b6d7a6d873e6c3fee083f5`; backend snapshot tree `5b9fb166d4e5020fa157ad1e12eea052b3e3f270`. This report and task-status append are documentation written after these tests; runtime/contracts/tests/generated content is identical.

## Findings and proofs

| Finding | State | Cause / change / regression |
| --- | --- | --- |
| P1 deleted/lost scenario task | Fixed | Old validator required two available numeric values. Preserve old task representation, match ID/semantic task independently of deltas, require effects for status/semantic changes. `lost_connection` is a confirmed event with null delta. Original DTO RED 8 failures / 9 passes; original spawned worker accepted both removed and no-route-hidden A (job succeeded, API200, cache succeeded). Fixed spawned controls reject both (failed, no run, API404, retry queued, baseline unchanged); full control, honest loss and unknown accepted. |
| P1 mandatory reference/journey/basis completeness | Fixed | Full score validation stopped at Metric and weight rules depended on ranking_eligible. Validate Score -> Metric -> Journey -> Reference and explicit reference_ids, and full population/site basis regardless of ranking flag. Initial valid-control RED: 8 DIDNOTRAISE / 7 passes; new 19 contract + 5 spawned publication cases pass. Raw independent times remain available; DTO computes no ML1 formula. |
| P2 Rasp scope/date/direction | Fixed | Ignored echoed search, no station endpoint check. Require matching search/date and station endpoints; city queries rely on echoed city scope. New wrong direction/scope/date cases previously DIDNOTRAISE; now typed invalid_response; local/UTC date and city-station distinctions pass. Gateway rejection yields unknown, no rows/cache key. |
| P2 conflicting duplicates | Fixed | Last row won dictionary assignment. Identical rows deduplicate; conflict rejects entire observation, even across pages. Both row orders, cross-page no-cache retry and gateway tests pass. |
| P2 unbounded body | Fixed | send buffered body before parsing. stream=True, decoded-byte cap (2 MiB default, max16 MiB), Content-Length not trusted, finally closes stream. Exact/+1, chunked, false length, gzip, 500 body, timeout/cancel tests pass; no raw text in typed error. |
| Shared conftest import | Fixed | migrate resolved engine conftest during collection. Absolute apps.api.tests.migration_support used by backend fixture and all three importing test files. Initial B: 775 collected plus collection error; intermediate B exposed two runtime imports (1204 passed/2 failed). Final B collection805, combined suite1206 pass. ML1 files unchanged. |
| Historic contradictory immutable result/cache | Fixed safe refusal | New canonical validation rejects old contradictory payloads. Read and cache reuse produce safe409 stored_result_incompatible, before creating a succeeded cache job. Real PG regression proves stored JSON/hash unchanged. |
| District graph fingerprint | External ML1/ML2 | See handoff below; no owner code modified. |
| Installed source packaging | External DevOps | Frozen sync succeeds but bare Python import a2_api fails without source PYTHONPATH. Explicit source imports pass. This is not real deployment readiness. |

## Contracts and compatibility

Additive `ScenarioDelta.outcome=lost_connection`, `Score.reference_ids` (empty default excluded from serialization to preserve old valid wire/hashes), and error code `stored_result_incompatible`. Generated validation/serialization schemas and both affected OpenAPI documents are regenerated. No migration or new table is needed. Correct historical results retain shape/hash; contradictory old results get an explicit safe409, are never rewritten or silently reused from cache. Consumers must regenerate types, render loss separately from unknown and support recalculation after409. Frontend and engine were not edited.

## Commands and actual results

Pinned uv used via `uvx --from uv==0.12.17 uv`; global uv0.13.6 was rejected by project version check and was not used for sync. Frozen dependencies are unchanged.

Set `PYTHONPATH=.:packages/contracts/src:apps/api/src:packages/assistant/src:packages/engine/src` and `A2_TEST_DATABASE_URL` to the dedicated disposable database. Backend source lists below mean apps/api/src apps/api/tests apps/api/migrations packages/contracts/src packages/contracts/tests packages/assistant/src packages/assistant/tests.

| Tree | Command | Exit | Result |
| --- | --- | --- | --- |
| backend local | python -m pytest -q -o asyncio_default_fixture_loop_scope=function --tb=short | 0 | 660 passed, 0 failed, 0 skipped |
| A | python -m pytest apps/api/tests packages/contracts/tests packages/assistant/tests -q -o asyncio_default_fixture_loop_scope=function --tb=short | 0 | 660 passed, 0 failed, 0 skipped |
| B | python -m pytest apps/api/tests packages/contracts/tests packages/assistant/tests packages/engine/tests -q -o asyncio_default_fixture_loop_scope=function --tb=short | 0 | 1206 passed, 0 failed, 0 skipped |
| B | python -m pytest --collect-only -q apps/api/tests packages/engine/tests -o asyncio_default_fixture_loop_scope=function | 0 | 805 collected, no errors |
| backend/A/B | uv lock --check; uv sync --frozen --all-packages --group dev; uv pip check | 0 each | 57 compatible packages; separate clean frozen environments |
| backend/A/B | python -m a2_api.export --output contracts/generated --check | 0 | schemas reproducible |
| backend/A/B | ruff check <backend source lists> | 0 | PASS, no autofix |
| backend/A/B | ruff format --check <backend source lists> | 0 | 102 files formatted |
| backend/A/B | MYPYPATH=packages/contracts/src:apps/api/src:packages/assistant/src python -m mypy --explicit-package-bases packages/contracts/src/a2_contracts apps/api/src/a2_api packages/assistant/src/a2_assistant | 0 | strict, 43 source files |
| backend/A/B | python scripts/check_product_config.py | 0 | approved product config |
| A/B | source-PYTHONPATH python imports a2_api/a2_engine/a2_contracts/greenlet | 0 | PASS |
| B | bare python import a2_api (no PYTHONPATH) | 1 | FAIL, existing source packaging dependency |
| backend | git diff --check | 0 | PASS |

Full PostgreSQL suites include fresh migration to0003, upgrade with existing rows, guarded downgrade with v2/evidence data preservation, worker cancellation/lease/heartbeat/publication, idempotency/cache, cookies/permissions, assistant/tools/fallback and process API/worker smoke tests. New worker publication tests use spawned processes, not only in-process DTO checks. Live Alice/Rasp, real-city routing and browser E2E were NOT RUN. Synthetic PASS does not establish readiness of production calculations.

## Cross-owner handoff

ML1 at c6531ad: `packages/engine/src/a2_engine/urban/district_inputs.py:prepare_urban_district_inputs` compares provenance.graph_version directly with manifest.graph_version. ML2 at26a4f5a: `packages/data_pipeline/src/a2_data_pipeline/contract_v2.py` builds provenance as `+`.join(walk, road). Actual published manifests kaliningrad-urban-2026-11-08.5 and novorossiysk-urban-2026-11-08.3 both have road_graph_version, so this equality fails. Static reproduction: git show work/ml2:<manifest>, concatenate graph_version and road_graph_version as canonical producer does; compare with manifest.graph_version. Kaliningrad yields osm_geofabrik.kaliningrad-261107:osm-walk-network-v1:main-component versus the same string plus +osm_geofabrik.kaliningrad-261107:osm-road-network-v1:main-component. Novorossiysk likewise uses south-fed-district-261107. This is a reproduced version-gate mismatch, not a claim of successful real-bundle integration. Owners must agree composite fingerprint while retaining version integrity, with walk-only/walk+road/tampered-version regressions. No ML2 branch was included in B.

ML1: implement/connect approved calculation handler and scenario/reference/urban interfaces with new completeness dependencies/outcome; no backend formula/routing substitute. ML2: accepted real datasets and reviewed metadata/evidence/maps, agreed fingerprint. FRONTEND: API gateway instead of mocks, new enum/error handling, generated types, real browser integration. DEVOPS: source packaging/imports without manual PYTHONPATH, deployment/PostGIS/CI/process configuration. Production calculation handler remains an explicit readiness dependency. No blanket production-ready claim.

## Full changed-file inventory

- `apps/api/README.md`
- `apps/api/docs/backend-review-20261109.md`
- `apps/api/docs/frontend-integration-handoff.md`
- `apps/api/docs/handoff-ml1-ml2.md`
- `apps/api/src/a2_api/providers/rasp.py`
- `apps/api/src/a2_api/storage.py`
- `apps/api/tests/conftest.py`
- `apps/api/tests/dependency_publication_handlers.py`
- `apps/api/tests/migration_support.py`
- `apps/api/tests/scenario_transition_handlers.py`
- `apps/api/tests/test_departure_gateway.py`
- `apps/api/tests/test_evidence_search.py`
- `apps/api/tests/test_migrations.py`
- `apps/api/tests/test_rasp_provider.py`
- `apps/api/tests/test_rasp_review.py`
- `apps/api/tests/test_required_dependency_publication.py`
- `apps/api/tests/test_runtime.py`
- `apps/api/tests/test_scenario_transition_publication.py`
- `apps/api/tests/test_stored_dependency_compatibility.py`
- `apps/api/tests/test_v2_tools.py`
- `contracts/generated/ErrorDetail.serialization.schema.json`
- `contracts/generated/ErrorDetail.validation.schema.json`
- `contracts/generated/ErrorEnvelope.serialization.schema.json`
- `contracts/generated/ErrorEnvelope.validation.schema.json`
- `contracts/generated/Job.serialization.schema.json`
- `contracts/generated/Job.validation.schema.json`
- `contracts/generated/legacy-openapi.json`
- `contracts/generated/manifest.json`
- `contracts/generated/openapi.json`
- `contracts/generated/v2.AnalysisResult.serialization.schema.json`
- `contracts/generated/v2.AnalysisResult.validation.schema.json`
- `contracts/generated/v2.ScenarioDelta.serialization.schema.json`
- `contracts/generated/v2.ScenarioDelta.validation.schema.json`
- `contracts/generated/v2.ScenarioResult.serialization.schema.json`
- `contracts/generated/v2.ScenarioResult.validation.schema.json`
- `contracts/generated/v2.ScenarioValues.serialization.schema.json`
- `contracts/generated/v2.ScenarioValues.validation.schema.json`
- `contracts/generated/v2.Score.serialization.schema.json`
- `contracts/generated/v2.Score.validation.schema.json`
- `docs/tasks/backend.md`
- `packages/contracts/fixtures/v2/required-dependencies-synthetic.json`
- `packages/contracts/specs/scenario.md`
- `packages/contracts/src/a2_contracts/api.py`
- `packages/contracts/src/a2_contracts/v2/results.py`
- `packages/contracts/src/a2_contracts/v2/scenarios.py`
- `packages/contracts/tests/test_required_result_dependencies.py`
- `packages/contracts/tests/test_scenario_transitions.py`

## Publication preparation verification

A new fetch confirmed local HEAD equals origin/work/backend at18f5281 and dev remains c783435. ML1 has advanced to `cd7572ebb0a2d1f054c974d304ab37a0bf9a81f1`; the earlier B/1206 result applies specifically to c6531ad, not the newer ML1 revision. Its intervening diff adds static car/OD implementation and documentation under ML1 ownership; none of it is included in this backend commit. The fingerprint handoff above records the audited versions.

Before publication the full PostgreSQL backend suite was rerun: **660 passed, 0 failed, 0 skipped** (21.46s, exit0). Ruff check, format check (102 files), strict mypy (43 files), schema export --check, product configuration, pinned uv lock --check/pip check and git diff --check all exit0. All runtime/contracts/tests/generated files match the previously tested backend tree5b9fb166 byte-for-byte; only this report and docs/tasks/backend.md were later audit documents. No production database or paid calls.

Contract consistency: v2 OpenAPI error.code remains an Identifier string and therefore accepts stored_result_incompatible; it is not an enumerated code list. The shared DomainError typing enum and legacy generated error schemas include the new code. The runtime409 shape and immutable/cache refusal are covered by the PostgreSQL API regression. lost_connection and Score.reference_ids are present in v2 OpenAPI and validation/serialization schemas and both consumer handoffs.

The user now authorizes one backend commit and ordinary push to the existing PR8; no merge, new PR or discussion resolution is authorized. Final publication SHA is reported in the chat to avoid a self-referential commit hash in this file.
