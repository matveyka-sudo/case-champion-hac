# Waydex Full Product — restart checkpoint, 2026-11-10

## Active user objective

Continue autonomous FULL PRODUCT COMPLETION, not another partial-pilot report.
Implement all available approved product flows: urban passenger, intercity
passenger, urban cargo, intercity cargo, separate pipeline, real scenarios,
saved-result Alice, full API UI, bounded resumable OD execution and reproducible
stack. Pursue verified numerical indices without weakening input coverage or
minimum-time proof. If external data blocks one direction, continue independent
directions. Do not stop after planning or the first successful E2E.

Requirements: docs/PRODUCT_SPEC.md > FORMULAS.md > IMPLEMENTATION.md > contracts.
Preserve asyncpg/raw SQL, pure ML1, ML2 provenance, existing design and formulas.
No merge to dev/main without explicit approval, no force push, no permanent
work-branch deletion. Ordinary commits/push/Draft PR are explicitly authorized.
No additional paid services without cost estimate and permission. Yandex Rasp
quota remains 500/day; this new stage has made zero provider/paid calls.
Do not enable the excluded arbitrary user scenario constructor: use approved
server templates and explicit separate user execution instead.

## Current branch and baseline

- New root worktree: `/Users/kirito/waydex-full`
- Branch: `integration/full-product-20261110`
- Base HEAD: `297accd35fb99f816c2a4d857da09df6ea6b0eda` (PR #27)
- dev: `91ed5262b62c16afa3ca619f8140c43917cf8325`
- PR #26: OPEN/DRAFT, `dd46659f2500844285b3c8849b756fe88664a39a`
- PR #27: OPEN/DRAFT, `297accd35fb99f816c2a4d857da09df6ea6b0eda`
- PR #26 is an ancestor of #27: symmetric rev-list count `0 15`.
  Do not merge/cherry-pick #26 a second time.
- `.venv` in the new worktree installed successfully using pinned uv 0.12.17.
- No product implementation edits have yet been made in this new stage.
- Previous proven work is documented in `docs/phase2/REPORT.md`; do not redo it
  merely to reconstruct history. Reverify mutable environment when needed.

## Concurrency configuration and practical result

User explicitly requests GPT-6.1 Sol **ultra**, up to six simultaneous subagents.
Chief requested GPT-6 Astra High. Use real collaboration tools, no manual relay.

Observed local runtime: Codex CLI/app-server **0.167.0**;
`CODEX_HOME=/Users/kirito/.codex-pro`. Mechanism: `collaboration.spawn_agent`.
`~/.codex/config.toml` had `[agents] max_concurrent_threads_per_session = 2`;
active-home `.codex-pro/config.toml` had no agents section. No project config
was present in the repository paths inspected.

Both config files now contain, without disturbing other sections:

```toml
[agents]
enabled = true
max_concurrent_threads_per_session = 6
default_subagent_model = "gpt-6.1-sol"
default_subagent_reasoning_effort = "ultra"
```

`codex app-server --strict-config --print-startup-config` passed before and
after the write. Both local model catalogs explicitly list Sol `ultra`.
Backups (0600) and verification JSON are outside Git:
`/Users/kirito/waydex-full-runtime/config-backups/`,
`/Users/kirito/waydex-full-runtime/concurrency-verification.json`.

Practical Test A in the OLD session:
- `/root/concurrency_frontend`: created with Sol/ultra, RUNNING >90s;
- `/root/concurrency_backend`: created with Sol/ultra, RUNNING concurrently >90s;
- third `/root/concurrency_ml1`: rejected `agent thread limit reached`;
- list_agents confirmed root + two RUNNING children;
- Test B was not run because A failed.

The file change did not change the old session's live limit. Do not claim six
already worked. After restart/new session, repeat A with three simultaneous
read-only tasks and, only if successful, B with five. Record actual active
counts, accepted model/effort and errors. Do not bypass a platform/server cap
using unrelated tasks or paid APIs. If still capped, continue useful waves.

Official key reference:
https://learn.chatgpt.com/docs/config-file/config-reference

## Read-only initial findings (need implementation, not merely documentation)

FRONTEND audit completed, no edits/tests/children:

1. `apps/web/src/pages/ApiAtlasPage.tsx:305` and
   `src/api/adapters/request.ts:114`: urban city dropdown/request ignores
   `caps.urban_city_names`; national catalog can offer unsupported urban cities.
2. `request.ts:40`: no snapshot selection/submission even though capabilities
   publish snapshot coverage and backend `core/registry.py:241` rejects
   overlapping snapshots unless explicitly selected.
3. `ApiAtlasPage.tsx:391`: pipeline profile options filter kind but not selected
   product, causing predictable validation rejection at `request.ts:225`.
4. `widgets/ApiMap.tsx:71`: available partial scores get same visual treatment
   as complete ones; distinguish completeness on map/tooltips.
5. `ApiAtlasPage.tsx:1084`: last_returns render all rows rather than the selected
   point/destination's rows.
6. No production comparable-territory ranking renderer; needs contract/backend
   review before frontend implementation.
7. `App.tsx:12`: default is demo unless query/env selects API. Production launcher
   must explicitly choose API; demo cannot represent production analysis.

Existing frontend already supports four forms, separate pipeline controls,
server scenario proposals/runs, saved-result assistant, date/time/IANA, jobs,
cancellation, before/after. Reuse these components instead of redesign.

BACKEND initial audit:
- Only production engine boundary is `apps/api/src/a2_api/adapters/walk_pilot.py`:
  Kaliningrad, static, walk, school, one point. Full product dispatch absent.
- Stable seam to retain: `registry()`, async
  `preflight(AnalysisRequest, AcceptedData) -> None`, sync
  `calculate(ClaimedJob) -> AnalysisResult | CalculationOutput | ScenarioResult`.
- Worker already accepts canonical analysis/scenario results, uses subprocess
  execution, leases and atomic publication. Reuse it.
- ML1 public engine already has walk/car/traffic/transit/tau/districts,
  intercity passenger/reference, urban/intercity cargo, pipeline and Scenario
  Engine v3. Main gap is real input/orchestration/result integration, not formulas.
- Scenario wire lacks dated event/execution/physical/resource supplements;
  canonical feasibility only verified/unverified, while engine can produce
  infeasible; ServiceChange requires whole_trip=True while engine can express
  departure_tail. Architecture agreement needed before contract edits.
- `apps/api/docs/phase2-batches.md` is a proposal; no checkpoint runtime exists.

## Next execution order

1. Verify new-session concurrency without product edits; report actual result.
2. Finish full acceptance matrix from specs, actual entrypoints, real accepted
   bundles and available runtime; keep independent functional/data/index/nationwide
   readiness columns. Continue to implementation immediately after contracts.
3. Give isolated worktrees to BACKEND (dispatcher + batches), ML1 (real graph
   minimum proof/exporter interface + input adapters), ML2 (data for all profiles
   + source coverage), DEVOPS (one stack + executors), FRONTEND (verified gaps
   above + integrated flows). Use reviewer after meaningful deliveries.
4. Assign one owner to canonical/shared contracts and lockfiles. Reuse approved
   algorithms; avoid simultaneous edits of shared files.
5. Keep first numerical index as P0, but do not let catalog/route-proof external
   blockers stop handlers, scenarios, batches, UI and deployment.

## Existing useful local environment

- Previous worktrees remain: `/Users/kirito/waydex-phase2`, `waydex-real-e2e`,
  `waydex-phase2-{backend,data,ml1}`; never delete permanent work branches.
- Pinned uv binary:
  `/Users/kirito/.cache/uv/archive-v0/6qHRHWN-oUgk86kx/uv-0.12.17.data/scripts/uv`
- Node/npm pins: node24.22.2 / npm11.19.1 via npm exec packages.
- Baseline external runtime: `/Users/kirito/waydex-e2e-runtime`.
- Phase2 fresh graph and evidence: `/Users/kirito/waydex-phase2-runtime/clean-graph`.
- PBF SHA: `769a6a6ac552b534057f2480b88f1c2d0709636761ab6d08483efe69c1e97b69`.
- `.3` AcceptedData hash: `882030e7dc77f1216bd4ef2c289e65946af814d8b25c4332ddc7cb1f496b416f`.
- PostgreSQL container `waydex-real-e2e-postgres-1`, port55447, local dev database
  a2; isolate new work in a unique schema or Compose project. Do not alter old
  data destructively. Existing migration head0004.
- Existing Valhalla ports8007 (baseline),8027 (fresh phase2); inspect before use.
- Previous Phase2 UI5189/API8038/schemaa2_phase2_revision3 may still run;
  runtime processes are not a reproducible deployment contract.
- Existing ignored ML2 handoff at
  `/Users/kirito/Desktop/chemp-case/ml2-handoff-2026-11-10`; GTFS/bindings raw files
  were copied into old worktrees' ignored data/processed, not this new checkout.
- Raw data, keys, tiles, database files remain outside Git.

The user suggested restarting now to load the updated concurrency. Preserve
this checkpoint and continue the full mission; do not treat restart as completion.
