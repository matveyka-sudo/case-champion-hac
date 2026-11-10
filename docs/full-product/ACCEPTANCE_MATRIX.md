# Full product acceptance ledger

Baseline: integration/full-product-20261110, parent PR27
`297accd35fb99f816c2a4d857da09df6ea6b0eda`; PR26 is an ancestor.
This ledger records open acceptance obligations, not a completion claim.
Authority: PRODUCT_SPEC.md → FORMULAS.md → IMPLEMENTATION.md → contracts.
Data, functional implementation, numerical eligibility and national coverage
must be assessed independently. Earlier Phase2 evidence is historical until replayed.

## Execution and ownership

| Task | Owner / branch | Status | Delivery and independent acceptance |
|---|---|---|---|
| Dynamic dispatcher, all four kinds and pipeline | BACKEND / work/full-backend | RUNNING | Typed source-bound inputs, request-sensitive engine calls, canonical projection; negative semantic/hash/coverage tests; root diff and DB review |
| Map/form/snapshot correctness | FRONTEND / work/full-frontend | RUNNING | Urban city guard, explicit snapshot selection, pipeline product binding, partial map, selected last returns; browser tests |
| Actual graph minimum bounds | ML1 / work/full-ml1 | RUNNING | Source-bound graph export, independent lower bound and feasible upper bound, exact equality only with complete endpoint model; real measured replay |
| External data restoration and normalized inputs | ML2 / work/full-ml2 | RUNNING | Immutable checksum-verified restore, accepted timetable adapter, per-profile source inventory; quarantine preserved |
| Isolated API-mode stack | DEVOPS / work/full-devops | RUNNING | New Compose project, PostGIS, migrations, API, worker, Valhalla, SPA, resource bounds; clean checkout launch |
| Independent reliability review | reviewer / read-only baseline | RUNNING | Reproducible defects with tests, then review delivered commits |
| Selection-aware grounded Alice | root / integration branch | RUNNING | Metrics beyond first twenty remain explainable; fallback matches selected entity; no new LLM permissions |
| Bounded resumable OD executor | BACKEND after dispatcher | PLANNED | Durable checkpoints/cache identity, fenced retries, cancel/deadline, deterministic atomic final publication |
| Saved baseline scenario integration | BACKEND + ML1, approved shared contract | PLANNED | Dated physical executions, immutable overlay, before/after, negative effects, explicit user run |

Six child threads were simultaneously active after runtime reconfiguration;
all were requested as gpt-6.1-sol / ultra. Before reconfiguration the third child
was rejected. Current practical evidence includes two ongoing implementation
threads and four newly launched read-only audits, subsequently assigned work.
It is not a claim that the earlier five-read-only probe succeeded.

## Product gates

| Requirement | Existing implementation / evidence boundary | Required completion evidence | Dependency / owner |
|---|---|---|---|
| Three urban pilot cities, nine approved categories, modes/date/time/territory | Canonical request/UI exist; real walk adapter restricts Kaliningrad/school/static | Each exposed capability matches accepted city/category/mode/calendar; parameters alter real calculation | ML2 → BACKEND → FRONTEND |
| Stable metric grid, water/buildings, original cell population, districts | Accepted bundles and engine preparation exist; excluded cells remain QA | Source-verified cell/point geometry, population/year/method, nonduplicating district shares; positive unknown weight blocks full aggregate | ML2 / ML1 |
| Physical approaches + nearest full-time object | One observed network route and two approaches; no minimum proof | All admissible endpoint alternatives and candidate objects covered; independent minimum bound equals feasible total time | ML1 + ML2 |
| Urban routes/breakdown/geometry and honest missing geometry | Walk pilot observed 547.404 seconds historically | Repeat live executor and canonical/API/UI persistence; no fabricated connector line or duplicate wait | DEVOPS / BACKEND / FRONTEND |
| Category, weighted point, population territory/district indices | Pure engine implemented; real pilot indices null | Formula boundary tests plus genuinely complete restricted scope; label exact territory, never city-wide by inference | ML1 + ML2 |
| OD τ, 28 local weekly slices, accessible counts, last return | Engine functions exist; no production mass executor | Correct week/calendar/direction/population weighting, separate H, persisted resumable jobs and selected return context | BACKEND + ML1 |
| Intercity passengers, city and region-via-capitals, road separately | Engine/public DTO/UI exist; production dispatcher pending | Real dated chains, 7-day departure window, initial wait/path separation, reference A proof/coverage, actual transfers and unique departures | ML2 + BACKEND |
| Static/typical/live/historical | Contract and version validators exist | Requested profile used or explicit unavailable; stale/wrong graph/direction rejected; no static fallback | BACKEND / ML2 |
| Urban freight, physical sites, compatible infrastructure, direction | Engine and accepted site inventory exist | Real truck approaches + compatibility + all required operations; separate receipt/dispatch, equal site weights only when volumes absent | ML2 + BACKEND |
| Intercity freight, city/subject, urgency 24/72/744 h | Engine/public DTO/UI exist | Full preparation/wait/movement/handling chain, 31-day departure window, confirmed compatibility; absent operations retain unknown | ML2 + BACKEND |
| Pipeline separate | Engine and contract exist; terminal coordinates alone insufficient | Directed accepted connection/product plus comparable period/unit V/Q, >100% QA; never delivery index | ML2 + BACKEND |
| Approved scenario templates and explicit execution | HTTP/jobs/UI and pure Scenario Engine exist | Saved immutable baseline → validated dated physical overlay → recompute → persisted canonical result → UI; losses/negative effects visible | BACKEND + ML1 |
| Scenario feasibility/demand/resources | Engine richer than wire interface | No silent loss of dated event/resource constraints; unverified remains model experiment; adding service requires evidence | Shared contract review |
| Alice saved-result explanation | Read-only tools/budget/grounding exist; selected metrics after #20 defect confirmed | Context-relevant bounded facts and fallback, exact saved claims, evidence, prompt injection/timeout/budget tests; analytics survives disabled AI | root + reviewer |
| Worker/session reliability | Existing leases, subprocess timeout, PG atomic publication | Real isolated DB: duplicate payload/keys, restart, cancel, stale worker, concurrent claims, unauthorized session, final persistence | BACKEND + reviewer |
| Scalable batches | Design document only at baseline | Bounded chunks, stable identities, checkpoints/resume/cache, rate limits/backpressure/memory/progress/deadline; measured real matrix workload | BACKEND |
| Functional UI across all profiles | Existing forms/map/jobs/scenarios/Alice; five audited defects being fixed | Real backend browser tests; no dead controls, responsive/keyboard/a11y, supported/partial/unavailable, null/zero/long names | FRONTEND |
| Reproducible whole stack | Old Compose demo-only/no worker; host pilot launcher exists | Clean archived checkout + restored artifacts; migration order/PostGIS/API/worker/real graph/API SPA health and real job; restart persistence | DEVOPS |
| Data retrieval and provenance | Manifests and local handoff; missing artifacts in fresh checkout | Verified external retrieval or explicit local licensed handoff, immutable hashes/source versions, QA, reproducible restore and exact unavailable items | ML2 / DEVOPS |
| CI and final QA | PR27 historical five checks green | Current integration Python suites/Ruff/mypy/import, frontend install/types/lint/build/unit/browser, PG and real E2E; distinguish synthetic and skipped | root + all owners |
| National coverage | Catalog is not route/data coverage | Accepted source coverage for requested nationwide scope; no inference from pilot success | External data + ML2 |

## Critical dependencies

Accepted source facts → canonical snapshot + immutable supplements → dynamic
input projection → real executor → pure ML1 → canonical output → fenced atomic
PostgreSQL publication → API → UI. Scenarios reuse saved baseline versions and
dated physical executions. Alice reads the saved output only.

Numerical proof has additional independent gates: complete object roster,
population scope, admissible physical endpoint roster and minimum full-time proof.
Closing deployment or passing fixture tests cannot close these gates.

External blockers do not block independent implementation: no pipeline V/Q or
cargo operations means an honest unavailable profile, while handlers, validation,
jobs, UI, data restore, scenario wiring and route proof work continue.

## Current readiness verdict (not final acceptance)

| Flag | Verdict | Basis |
|---|---|---|
| PRODUCT_FUNCTIONAL_READY | partial | One real partial chain; full dispatcher/batches/scenario deployment still open |
| REAL_DATA_COVERAGE_READY | partial | Real Kaliningrad bundle and accepted source artifacts; city catalog/connectors incomplete |
| NUMERICAL_INDEX_READY | no | Actual pilot minimum and complete population/object scope not proved |
| NATIONWIDE_COVERAGE_READY | no | National catalog/bindings do not prove national accepted routing/operation coverage |

No dev merge is authorized. Acceptance requires reviewing final diffs and replaying
the integrated implementation, not accepting an agent's success message alone.
