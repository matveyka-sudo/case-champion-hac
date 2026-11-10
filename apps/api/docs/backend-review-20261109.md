# Backend independent-review corrections, 2026-11-09

This is a historical audit record. Its schema-export command is summarized
without the retired module path; exit codes and results are unchanged. Use
the current commands in [the API README](../README.md) for new checkouts.

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
| backend/A/B | schema-export check (historical CLI) | 0 | schemas reproducible |
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

# Follow-up review: mandatory operands and bounded Rasp memory

This section supersedes the earlier readiness/counts for the current uncommitted work. Work resumed in the existing isolated checkout; no initial work or tests were discarded. No commit, push, merge, rebase/reset/clean or GitHub review publication was performed. The independent evidence ZIP supplied in the request was absent, so the defects were reproduced independently. PostgreSQL is the existing dedicated test container/database on127.0.0.1:55439; only UUID test schemas were written.

## Exact revisions and trees

- `backend_head`: `2c193054feee9ab7daa3b0c93a6e76c3bbc59e8d`
- `dev`: `c78343563193ab3e3bf270b714cad423bd69f549`
- `ml1`: `f4c8ae476c431ef18501d7aa4e11bd7f1cbea783`
- `ml2`: `26a4f5a283f054560123dab87a05806fc253bc9e`
- `backend_tree`: `ff5e319d50eb8ac336247a928a8221bed4dcce24`
- `A`: `826794a95cdea3ea8b5ab44e8020712b00a7a02e`
- `B`: `663d1363ed9fa015d02358d902eb702ae26aa014`
- `base_a`: `2e769c7b8ccc55088d8ffdf5695af2f5787ea801`
- `base_b`: `4d388babdc20285b3894a92fbb90e62707eeb9c6`

All published refs were rechecked before completion and remained unchanged. Current PR8 HEAD is still2c19305. Private-index write-tree captured all local code/tests/schemas. A was constructed with `git merge-tree --write-tree --merge-base=<backend/dev merge base> origin/dev <backend-tree>`. B used `git merge-tree --write-tree --merge-base=<backend/ML1 merge base> <A-tree> origin/work/ml1`. Both exited0 with no conflicts; no commit objects or merge commits were needed. Engine in B equals published ML1; frontend/infra in A equal dev. No directory overlay was used. Final report/task/handoff additions after the snapshot are documentation only; executable code, tests and schemas match the tested tree.

## Findings, minimal fixes and proof

| Finding | Fix and independent evidence |
| --- | --- |
| P1 deleting mandatory reference links | Existing links do not encode necessity. One optional calculation_kind field declares reference_ratio/initial_wait/aggregate/delivery_deadline for intercity. Reference-based full values require nonempty resolvable mandatory operands even when journey/reference links are removed. Before fix: originalHEAD2c19305, valid controls passed,5 spawned adversarial cases succeeded/published/API200/cache succeeded. After fix: failed job/no run/API404/retryqueued, immutable baseline unchanged. |
| P1 false territorial fullness with ranking=false | Full territorial scores require aggregation basis; urban passenger/cargo require PopulationBasis/SiteBasis respectively. Known partial/unknown positive component weights propagate independently of ranking flag. Urban H and standalone waiting remain independent of unrelated Reference; raw times and honest preliminary values stay visible. |
| P1 deleting entire path subtree | New independent tests removed the path score and left waiting alone, directly or behind another aggregate:2 DIDNOTRAISE failures. Passenger aggregate now requires a transitive path operand. No further enum or field was added. |
| Scenario references missing/unavailable | New valid partial baseline→full after with empty direct IDs passed the earlier guard. Real PG regression RED1 DIDNOTRAISE, then GREEN. Publication now resolves implicit baseline operands as well as explicit IDs. |
| Scenario rebinding to another journey/reference | New PG tests RED3 DIDNOTRAISE plus1 valid journey-rename control; additional legacy partial-score upgrade RED1 DIDNOTRAISE. Frozen operands now remain mandatory across rebinding, even if the replacement reference is full or shares boundaries. GREEN5: four rejected cases and correct renamed-journey control. For rejected cases: no run, job not succeeded, API404, retryqueued, baseline JSON/hash unchanged. |
| Cargo aggregate compatibility | New positive aggregate→delivery score was rejected by the old direct-metric guard (RED1 fail/2 negative controls pass). Complete-chain lookup now follows the validated acyclic score graph with caching. Valid aggregate passes, movement-only/unknown operands still reject. |
| P2 gzip memory amplification | HTTPX decoded before our byte check; fixed-size rechunking also copied large identity chunks. Actual nonidentity Content-Encoding is rejected before reading, identity request header is advisory only, identity stream uses original chunks and checks before copying. Finally closes response on limit/error/timeout/cancellation. Controlled transport only. |

### RED and intermediate failures, retained explicitly

- Initial independent DTO controls were valid:7 missing-operand protections failed DIDNOTRAISE,1 control passed.
- Original-code real worker reproduction in `/tmp/backend-original-operands-red`: **5 failed,2 passed,1 deselected**, exit1. Deselection excluded the separate historical-compatibility test, not an adversarial case. Every rejected-oracle case actually had succeeded job, two total runs (baseline+candidate), API200 and succeeded cache hit. Log `/tmp/operand-original-red.log`.
- Original Rasp memory suite: **11 failed,3 passed**, exit1; `/tmp/backend-pr8-round3/rasp-original-red.log`. gzip16,328bytes→16MiB peaked50,408,045/50,396,628bytes forHTTP200/503. Identity16MiB chunks peaked33,577,628/33,577,532bytes. Fixtures were allocated before measuring provider processing.
- GREEN memory suite: **14 passed**, exit0; `/tmp/backend-pr8-round3/rasp-green.log`. gzip peaks28,349/16,102bytes and zero compressed chunks read; identity peaks13,968/14,132bytes. Existing exact-limit/+1/chunked/false-Length/error/timeout/cancel/close tests remain enabled.
- First full integration iteration: **5 failed,705 passed**. Old positive fixtures lacked newly required typed semantics/basis, and a tool fixture gained an inconsistent journey snapshot. These were made valid explicit controls without deleting assertions.
- Next iteration: **2 failed,709 passed**. The population-change test accidentally compared identical new full bases; it now changes100→200. The provider-observation control was restored to its intended no-saved-departures state using a legitimate independent raw/reference operand. Assertions remain unchanged.
- A shared-weight extraction mistake introduced an incorrectly indented loop: **64 failed,651 passed**. Fixed before final verification; no skip or assertion removal. The raw-only/empty-score controls and full suites pass again.
- Preliminary backend/A717 and B1313 passes preceded the last reference-rebinding fix. They are retained as earlier snapshots; final totals below cover the final guard and five extra PG cases.
- Reviewer-reported parallel legacy smoke ConnectErrors were not observed in our sequential final runs. Their cause is not established by this work; no timeout or readiness test was weakened to hide them.

## Canonical DTO/OpenAPI compatibility

Only `Score.calculation_kind` is a new wire field: optional nullable enum, defaultNone omitted from serialization. It is structurally additive; for numeric/full intercity scores it is conditionally mandatory and therefore semantically breaking for old full payloads without declared calculation semantics. Such immutable payloads return safe409 stored_result_incompatible on read/cache; they are never relabelled, rewritten or rehashed. Honest partial legacy scores, raw times and valid urban H values remain supported. ML1 must produce a new result with explicit operands. No generic dependency framework, required request field, new endpoint, formula computation or name-based inference was added.

Existing lost_connection/reference_ids/stored_result_incompatible remain supported and regression-tested. Repeated scenario tests include A30→35minutes/B60→40minutes, omitted/deleted/null A, false NO_FEASIBLE_ROUTE, false lost/new connection, ID/semantic substitutions, duplicate effects, partial/unknown and legitimate journey-ID changes. Both schemas and consumer handoffs reflect the single new response property. Full territory basis rules are semantic validation, not another new field.

Before/after OpenAPI patch: `apps/api/docs/review-openapi-score-semantics.diff` (only Score.calculation_kind). Before SHA256:0179e533319b7d5a3f6461b134003d1a40e5728588666c2f3fe3ddebdaac59fe; after:a2e36eebe61c5ba82c048aaa0d9b80b28b2d709cda53fdd666cffdde7b1ccf0c. Generated nested Score/AnalysisResult/ScenarioResult/ScenarioValues schemas derive from the canonical DTO; no independent JSON-Schema editing.

## Final commands and results

Environment: dedicated A2_TEST_DATABASE_URL; PYTHONPATH=.:packages/contracts/src:apps/api/src:packages/assistant/src:packages/engine/src; strict mypy uses matching MYPYPATH withoutengine. Separate own frozen environments in `/Users/kirito/backend-round3-A` and B. Tests run sequentially. API/worker subprocess tests and migrations use real PostgreSQL; no live model/provider requests.

Backend command: `.venv/bin/python -m pytest -q -o asyncio_default_fixture_loop_scope=function --tb=short`: **exit0,722 passed,0 failed,0 skipped** (25.36s).

| Tree | Command | Exit | Actual result |
| --- | --- | --- | --- |
| A | `uvx --from uv==0.12.17 uv lock --check` | 0 | Resolved 158 packages in 13ms |
| A | `uvx --from uv==0.12.17 uv sync --frozen --all-packages --group dev` | 0 | Checked 57 packages in 8ms |
| A | `uvx --from uv==0.12.17 uv pip check` | 0 | All installed packages are compatible |
| A | `.venv/bin/python -c import a2_api,a2_contracts,a2_assistant,greenlet; print("source imports OK")` | 0 | source imports OK |
| A | `.venv/bin/ruff check apps/api/src apps/api/tests apps/api/migrations packages/contracts/src packages/contracts/tests packages/assistant/src packages/assistant/tests` | 0 | All checks passed! |
| A | `.venv/bin/ruff format --check apps/api/src apps/api/tests apps/api/migrations packages/contracts/src packages/contracts/tests packages/assistant/src packages/assistant/tests` | 0 | 110 files already formatted |
| A | `.venv/bin/python -m mypy --explicit-package-bases packages/contracts/src/a2_contracts apps/api/src/a2_api packages/assistant/src/a2_assistant` | 0 | Success: no issues found in 43 source files |
| A | `schema-export check (historical CLI)` | 0 | Schema exports match canonical models and API |
| A | `.venv/bin/python scripts/check_product_config.py` | 0 | Product specification valid: approved methodology v3, Alice Flash, explicit data modes. |
| A | `.venv/bin/python -m pytest -q apps/api/tests packages/contracts/tests packages/assistant/tests -o asyncio_default_fixture_loop_scope=function --tb=short` | 0 | 722 passed in 22.72s |
| B | `uvx --from uv==0.12.17 uv lock --check` | 0 | Resolved 158 packages in 11ms |
| B | `uvx --from uv==0.12.17 uv sync --frozen --all-packages --group dev` | 0 | Checked 57 packages in 6ms |
| B | `uvx --from uv==0.12.17 uv pip check` | 0 | All installed packages are compatible |
| B | `.venv/bin/python -c import a2_api,a2_contracts,a2_assistant,greenlet; print("source imports OK")` | 0 | source imports OK |
| B | `.venv/bin/ruff check apps/api/src apps/api/tests apps/api/migrations packages/contracts/src packages/contracts/tests packages/assistant/src packages/assistant/tests` | 0 | All checks passed! |
| B | `.venv/bin/ruff format --check apps/api/src apps/api/tests apps/api/migrations packages/contracts/src packages/contracts/tests packages/assistant/src packages/assistant/tests` | 0 | 110 files already formatted |
| B | `.venv/bin/python -m mypy --explicit-package-bases packages/contracts/src/a2_contracts apps/api/src/a2_api packages/assistant/src/a2_assistant` | 0 | Success: no issues found in 43 source files |
| B | `schema-export check (historical CLI)` | 0 | Schema exports match canonical models and API |
| B | `.venv/bin/python scripts/check_product_config.py` | 0 | Product specification valid: approved methodology v3, Alice Flash, explicit data modes. |
| B | `.venv/bin/python -m pytest --collect-only -q apps/api/tests packages/engine/tests -o asyncio_default_fixture_loop_scope=function` | 0 | 884 tests collected in 0.38s |
| B | `.venv/bin/python -m pytest -q apps/api/tests packages/contracts/tests packages/assistant/tests packages/engine/tests -o asyncio_default_fixture_loop_scope=function --tb=short` | 0 | 1318 passed in 23.94s |

A:722 passed,0failed,0skipped. B:1318 passed,0failed,0skipped on actualML1f4c8ae4. Joint API+engine collection:884 collected,exit0. Backend local Ruff/format/strict mypy/export/diff checks also passed (110Python files /43mypy files). Schema exports reproduce exactly. Full suites include migration from zero to0003, existing-record upgrade/guarded downgrade, process publication/cancellation/lease/heartbeat, sessions/permissions, cache/idempotency, assistant/fallback/tools and providers. None of those were replaced by SQLite.

Frozen sync succeeds and source-path imports pass, including a2_engine in B. Bare `import a2_api` without PYTHONPATH still fails in both trees with ModuleNotFoundError (exit1): known external packaging limitation, not labelled PASS. No manifests, lock, frontend, engine, ML2 data, CI, Compose or a2-dev.bundle were edited.

## Handoff and readiness

ML1: populate calculation_kind and actual mandatory operands, retain full scope/basis metadata, validate real handlers against this contract. FRONTEND: regenerate response types, handle optional kind and existing409 recalculation/partial display; do not infer formula from name. ML2/ML1 external fingerprint mismatch persists: ML1f4c8ae4 implementation is now `packages/engine/src/a2_engine/urban/districts/inputs.py:53` (old district_inputs.py re-exports it), comparing single manifest graph_version with ML2 canonical walk+road string. The owner handoff remains walk-only/walk+road/tampered-version regressions without deleting integrity checks. DevOps retains source packaging/deployment/PostGIS/CI ownership. None of those external modules were changed.

No confirmed blocker remains in the reproduced BACKEND cases; local changes are ready for another independent review. Production calculation handler, real accepted-city integration, browser E2E and paid Alice remain outside the verified synthetic acceptance and are not declared ready. No commit/push/review publication/merge performed. All existing and new files remain local and uncommitted.

## Complete current modified/new file inventory

- `apps/api/README.md`
- `apps/api/docs/backend-review-20261109.md`
- `apps/api/docs/frontend-integration-handoff.md`
- `apps/api/docs/handoff-ml1-ml2.md`
- `apps/api/docs/review-openapi-score-semantics.diff`
- `apps/api/src/a2_api/providers/rasp.py`
- `apps/api/src/a2_api/storage.py`
- `apps/api/tests/operand_removal_handlers.py`
- `apps/api/tests/test_operand_removal_publication.py`
- `apps/api/tests/test_rasp_decompression_memory.py`
- `apps/api/tests/test_scenario_frozen_reference.py`
- `apps/api/tests/test_scenario_reference_boundary.py`
- `apps/api/tests/test_scenario_reference_dependencies.py`
- `apps/api/tests/test_v2_tools.py`
- `contracts/generated/manifest.json`
- `contracts/generated/openapi.json`
- `contracts/generated/v2.AnalysisResult.serialization.schema.json`
- `contracts/generated/v2.AnalysisResult.validation.schema.json`
- `contracts/generated/v2.ScenarioResult.serialization.schema.json`
- `contracts/generated/v2.ScenarioResult.validation.schema.json`
- `contracts/generated/v2.ScenarioValues.serialization.schema.json`
- `contracts/generated/v2.ScenarioValues.validation.schema.json`
- `contracts/generated/v2.Score.serialization.schema.json`
- `contracts/generated/v2.Score.validation.schema.json`
- `docs/tasks/backend.md`
- `packages/contracts/fixtures/v2/intercity_cargo-result.json`
- `packages/contracts/fixtures/v2/intercity_passenger-result.json`
- `packages/contracts/fixtures/v2/required-dependencies-synthetic.json`
- `packages/contracts/src/a2_contracts/v2/results.py`
- `packages/contracts/src/a2_contracts/v2/scenarios.py`
- `packages/contracts/tests/test_missing_required_operands.py`
- `packages/contracts/tests/test_scenario_effect_coverage.py`
- `packages/contracts/tests/test_scenario_review_minutes.py`
- `packages/contracts/tests/test_v2_scenarios.py`

# Fourth independent review: scope applicability and historical reuse

Current work remains uncommitted in work/backend. No commit, push, merge/rebase, reset or review discussion change was performed. The working tree was clean at the initial fetch, so all earlier committed tests were retained. Actual historical code and separate worker processes were used for the new reproductions; the earlier report was not treated as proof.

## External history changed during verification

PR8 was merged externally into dev while this task was running. GitHub read-only metadata confirms closed/merged at2026-11-09T09:22:29Z, merge3446f7e250d72971c1a8c2f75bf43bf1fa8d0f87. The assistant did not perform this merge. Initialdevc783435 was refreshed; final A/B below use the newdev3446f7e. Future publication workflow needs separate user authorization because PR8 is already closed. Local fixes remain valid follow-up work, not a claim to have updated that PR.

## Exact inputs and verified trees

- `backend_head`: `be98b68da156a32561e0a12bb125e4681aacae8f`
- `dev`: `3446f7e250d72971c1a8c2f75bf43bf1fa8d0f87`
- `ml1`: `f4c8ae476c431ef18501d7aa4e11bd7f1cbea783`
- `ml2`: `26a4f5a283f054560123dab87a05806fc253bc9e`
- `backend_tree`: `574a6ae908e7324121753c403f16c3aed49d9f12`
- `A`: `723556ce10901fb4af0bde089878cc5beb67baa5`
- `B`: `32b15b900676c65d7778afa3cb78acd6f6c3467f`
- `base_a`: `be98b68da156a32561e0a12bb125e4681aacae8f`
- `base_b`: `4d388babdc20285b3894a92fbb90e62707eeb9c6`

A: private index/write-tree of local backend, then `git merge-tree --write-tree --merge-base=<base_a> origin/dev <backend_tree>`. B: `git merge-tree --write-tree --merge-base=<base_b> <A-tree> origin/work/ml1`. Both exit0, no conflicts or commit objects. The base of A is now be98b68 because dev externally merged it. Engine in B exactly matches published ML1; frontend/infra in A exactly match dev. No directory overlay. Report/task/handoff edits after these snapshots are documentation only; runtime/tests/schema bytes match the verified snapshot.

## P1 causes and fixes

1. Full Reference validation previously checked existence/completeness but not applicability. Added reusable validate_reference_applicability in canonical contracts, using exact directed fixed boundary sets or proof from evaluated operand Journeys. Evaluated Journeys also must belong to fixed scope. No comparison of city IDs to node IDs, no formula computation, no new field. Missing proof blocks full, not honest partial/raw/urban H values.
2. Publication used baseline context but stored read only parsed ScenarioResult. Extracted one _validate_scenario_semantics(result,baseline,command), reused at publication and read/cache. Scope is checked before parsing. Read validates stored hashes, immutable baseline/proposal, the before snapshot, versions, identities, fixed operation and all required operand/fullness/scope rules. Public reads use repeatable-read snapshots; reuse/publication hold FOR SHARE on validated rows until transaction completion. replay/get_job/get_run_parameters/expired-cache idempotency/terminal cancel also validate successful reused results. Errors are safe409 stored_result_incompatible; another session sees404.

Two related defects were independently reproduced while completing the shared validator: it rejected a correct explicit Reference on a nested aggregate because it inspected only direct Metric children; it allowed an aggregate to erase its own declared partial Reference because frozen declarations were retained only for reference_ratio. Cached recursive operand traversal and retaining all same-task declared references fixed both. The same common validator is used, not separate publication/read rule sets.

Integrity and publication-race checks were also required to keep semantics bound to actual persisted inputs: raw payload/result_hash and proposal/content_hash are verified; succeed validates canonical saved instead of a mutable original while awaiting I/O. No immutable record is rewritten or rehashed on read.

## Independent RED -> GREEN evidence

| Reproduction | RED before protection | GREEN / proof |
| --- | --- | --- |
| Full Reference scope | Final31newcases against archived actualbe98b68:16failed,15passed,exit1. Eight bypasses each failed DTO+spawned oracle: foreign,reverse,superset,narrower,unbound,foreignunfixed,unlinkedmultinode,fixedforeignjourney. Bad workers actuallysucceeded,publishedrun,API200,cachesucceeded; controls passed. | Same31newcases plus41prior dependency cases:72passed. Negative calculations fail before publication,API404,retryqueued,baselineunchanged; correct fixed/journey/city-vs-node controls publish. |
| Historical scenario | Pinned git archive2c193054, originalDTO and originalJobRepository.succeed in a subprocess actually publish the row. Current originalbe98 code returned HTTP200/cacheSucceeded for invalid oldfull-after/partial-reference, while two correct oldurban/partial controls passed. DefinitiveRED1failed,2passed,exit1. | Invalid historical read/cache/replay/job/input/cancel/idempotency paths give409; no new succeeded job; other session404; correct oldcontrols200/cacheSucceeded. |
| Canonical publication DTO race |2failed,9deselected,exit1: checked mutable original differed from canonical bytes being stored. | Both race directions pass: bad saved payload cannot be rescued by mutating original; valid saved payload is not rejected by later original mutation. |
| Proposal integrity and terminal cancel |3failed,2passed,8deselected,exit1: changed proposal content_hash, removed assumption and terminalcancel bypass. | Shared proposal integrity checker and successful cancel reuse validation pass; no stored scenario rewrite. |
| Nested explicit Reference |1failed positive control,1passed foreign negative,exit1. Serializer warning in an initial test builder was corrected using ComponentRef before final run; no assertion weakened. | Correct nested Reference publishes/reads/caches; foreign Reference rejects. |
| Erasing aggregate Reference | Initial test setup used an available helper with partial input and failed before the boundary; this was fixed and is not counted as RED proof. Definitive validbaseline+validDTO RED1 DIDNOTRAISE,2passed,exit1. | Required frozen aggregate declaration retained, final nested suite3passed. |

Historical/race/integrity suite13passed, parent nested3passed; combined16passed. Historical tests include8 owner and8 other-scope concurrent reads, baseline payload/hash and proposal tampering, unchanged payload/hash checks, and actual old publication. No database fixture inserts a fabricated historical run in place of the required old Repository.succeed.

Definitive logs in /tmp/backend-pr8-round4: reference-original-final-red.log, reference-green.log, historical-red.log, canonical-race-red.log, proposal-integrity-red.log, nested-reference-red.log, aggregate-retention-red.log, backend-first.log, backend-final.log and per-tree check logs/results.json. The old-writer helper initially resolved a current test helper during setup; it was corrected to run inside the old archive and assert old Repository origin/absence of calculation_kind before definitive RED. No setup failure is labelled proof of the requested defect. Deselections above were focused test selections, not skipped acceptance cases; final suites below contain no skips.

## Final verification, real PostgreSQL

Backend local: `.venv/bin/python -m pytest -q -o asyncio_default_fixture_loop_scope=function --tb=short` => exit0, **769passed,0failed,0skipped**,30.18s. Earlier full pass764 preceded five additional nested/integrity regressions; final count covers them all. Existing process/worker/migration/guarded-downgrade/cache/idempotency/session/security/scenario/assistant/provider and gzip-memory cases remain enabled. Dedicated PostgreSQL container/database on127.0.0.1:55439 only, UUID schemas. No paid or live provider/model call.

Test environment: PYTHONPATH=.:packages/contracts/src:apps/api/src:packages/assistant/src:packages/engine/src, matching backend MYPYPATH; A2_TEST_DATABASE_URL targets the dedicated disposable database. Test-only A2_TEST_HISTORICAL_REPOSITORY points to the isolated Git checkout containing pinned2c193054; it does not load the dirty working files into the old runtime. Each A/B has its own frozen environment. Tests run sequentially.

| Tree | Command | Exit | Actual result |
| --- | --- | --- | --- |
| A | `uvx --from uv==0.12.17 uv lock --check` | 0 | Resolved 158 packages in 12ms |
| A | `uvx --from uv==0.12.17 uv sync --frozen --all-packages --group dev` | 0 |  + websockets==17.4 |
| A | `uvx --from uv==0.12.17 uv pip check` | 0 | All installed packages are compatible |
| A | `.venv/bin/python -c import a2_api,a2_contracts,a2_assistant,greenlet; print("source imports OK")` | 0 | source imports OK |
| A | `.venv/bin/ruff check apps/api/src apps/api/tests apps/api/migrations packages/contracts/src packages/contracts/tests packages/assistant/src packages/assistant/tests` | 0 | All checks passed! |
| A | `.venv/bin/ruff format --check apps/api/src apps/api/tests apps/api/migrations packages/contracts/src packages/contracts/tests packages/assistant/src packages/assistant/tests` | 0 | 116 files already formatted |
| A | `.venv/bin/python -m mypy --explicit-package-bases packages/contracts/src/a2_contracts apps/api/src/a2_api packages/assistant/src/a2_assistant` | 0 | Success: no issues found in 43 source files |
| A | `schema-export check (historical CLI)` | 0 | Schema exports match canonical models and API |
| A | `.venv/bin/python scripts/check_product_config.py` | 0 | Product specification valid: approved methodology v3, Alice Flash, explicit data modes. |
| A | `.venv/bin/python -m pytest -q apps/api/tests packages/contracts/tests packages/assistant/tests -o asyncio_default_fixture_loop_scope=function --tb=short` | 0 | 769 passed in 35.18s |
| B | `uvx --from uv==0.12.17 uv lock --check` | 0 | Resolved 158 packages in 12ms |
| B | `uvx --from uv==0.12.17 uv sync --frozen --all-packages --group dev` | 0 |  + websockets==17.4 |
| B | `uvx --from uv==0.12.17 uv pip check` | 0 | All installed packages are compatible |
| B | `.venv/bin/python -c import a2_api,a2_contracts,a2_assistant,greenlet; print("source imports OK")` | 0 | source imports OK |
| B | `.venv/bin/ruff check apps/api/src apps/api/tests apps/api/migrations packages/contracts/src packages/contracts/tests packages/assistant/src packages/assistant/tests` | 0 | All checks passed! |
| B | `.venv/bin/ruff format --check apps/api/src apps/api/tests apps/api/migrations packages/contracts/src packages/contracts/tests packages/assistant/src packages/assistant/tests` | 0 | 116 files already formatted |
| B | `.venv/bin/python -m mypy --explicit-package-bases packages/contracts/src/a2_contracts apps/api/src/a2_api packages/assistant/src/a2_assistant` | 0 | Success: no issues found in 43 source files |
| B | `schema-export check (historical CLI)` | 0 | Schema exports match canonical models and API |
| B | `.venv/bin/python scripts/check_product_config.py` | 0 | Product specification valid: approved methodology v3, Alice Flash, explicit data modes. |
| B | `.venv/bin/python -m pytest --collect-only -q apps/api/tests packages/engine/tests -o asyncio_default_fixture_loop_scope=function` | 0 | 915 tests collected in 0.73s |
| B | `.venv/bin/python -m pytest -q apps/api/tests packages/contracts/tests packages/assistant/tests packages/engine/tests -o asyncio_default_fixture_loop_scope=function --tb=short` | 0 | 1365 passed in 31.95s |

A:769passed,0failed,0skipped. B:1365passed,0failed,0skipped onML1f4c8ae4. Joint API+engine collection:915tests,exit0. Ruff/format116files, strictmypy43sourcefiles, exact schema export, frozen lock/sync/pip, config and source imports pass. All commands above exit0. Full backend local checks also pass. `git diff --check` pass. No final test failure or skip is hidden.

## Contract consequences and limits

OpenAPI/JSON Schema and generated/manifest.json have zero diff against be98b68; the byte-reproducible export remains current. No new required field, enum, request or endpoint. Full indexes now require verifiable directed Reference applicability. Historical scenarios are held to the same semantics as newly published ones; unsafe rows receive existing safe409 instead of silently being served. Correct verified historical urban/partial records remain readable; immutable payload/hash are unchanged by validation.

ML1 must declare directed node boundaries or evaluated Journey provenance. FRONTEND must handle existing stored_result_incompatible also on polling/replay/cancel and request recalculation, not loop on succeeded. Bare installed a2_api import without source paths remains the external DevOps packaging limitation from prior verification. ML1/ML2 walk-only vs walk+road graph-fingerprint mismatch remains outside this change; no engine/data/infra/dependency ownership was crossed. CI tests executing historical publication require the pinned Git commit to be available (or test-only source-repository env), not a shallow history missing2c193054. No runtime dependency on old Git code.

Ready for independent repeat review of local changes. The previously reproduced P1 cases now reject publication/reuse; no claim is made of complete production-city integration, live Alice or browser E2E. PR8 is externally merged, so any subsequent publication needs a separately agreed follow-up workflow. No commit/push/merge/review publication performed here.

## Complete fourth-cycle modified/new file inventory

- `apps/api/docs/backend-review-20261109.md`
- `apps/api/docs/frontend-integration-handoff.md`
- `apps/api/docs/handoff-ml1-ml2.md`
- `apps/api/src/a2_api/storage.py`
- `apps/api/tests/historical_scenario_writer4.py`
- `apps/api/tests/refscope4_handlers.py`
- `apps/api/tests/test_historical_scenario4.py`
- `apps/api/tests/test_refscope4_publication.py`
- `apps/api/tests/test_scenario_frozen_reference.py`
- `apps/api/tests/test_scenario_nested_reference4.py`
- `apps/api/tests/test_v2_tools.py`
- `docs/tasks/backend.md`
- `packages/contracts/src/a2_contracts/v2/results.py`
- `packages/contracts/tests/test_missing_required_operands.py`
- `packages/contracts/tests/test_refscope4_applicability.py`
- `packages/contracts/tests/test_required_result_dependencies.py`
