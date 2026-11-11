# Serial category calculations

`POST /api/v1/analysis-groups` accepts the existing `UrbanPassengerRequest` and
`Idempotency-Key`. It pins the normalized request, snapshot hash and code/graph/
calendar/population/config/methodology versions in PostgreSQL, then returns queue
metadata. It does not route in the HTTP process. This is a collection of separate
canonical category analyses, **not a new combined score or city coverage claim**.

The ordinary worker admits at most one new child per tick, using the same registry,
preflight, immutable context, checkpoint channel, calculation handler and atomic
publication as `/analyses`. Each child selects one category and its original
priority. One group has at most one queued/running child. Its result remains a
separate `AnalysisResult`; group polling never loads all category result payloads.
This avoids increasing the OD or decoded-result bounds to fit all Moscow categories
inside one process/result. A single category still has to fit existing execution
limits; groups do not partition one oversized category by population or geography.

For exact demo presets, each derived category request must be published on its
own. Create and retry return HTTP 422 with
`ANALYSIS_GROUP_CATEGORY_REQUEST_UNSUPPORTED` if a derived category request is
missing from the published presets.
Submit the complete published preset to `POST /api/v1/analyses` to run all its
categories together.

Endpoints:

- `GET /api/v1/analysis-groups/{id}`: pinned request/versions, each category's status,
  attempt and job/result IDs. Follow the existing job endpoint for OD progress and
  the analysis endpoint for canonical result validation and bytes.
- `POST /api/v1/analysis-groups/{id}/cancel`: cancel the group and any active child
  in one transaction. Finished category results remain immutable.
- `POST /api/v1/analysis-groups/{id}/retry` with a fresh idempotency key: explicitly
  retry stopped categories; retain successful categories. It creates new jobs,
  allowing the existing OD checkpoint/cache policy to reuse eligible work. It
  never changes a failed/cancelled job back to queued.

An unavailable category records its failure and does not prevent independent
categories from running. The final group is failed if any category failed; its
successful result references remain usable. A version mismatch stops the whole
group rather than mixing releases. A successful group means all child jobs
published, not that their metrics are known or their coverage is complete.

Queue admission allows two active groups per session and 32 per deployment;
creation/capacity accounting is serialized transactionally. Category attempts are
bounded at three and retry request history at 27 keys. Existing per-job deadline,
memory, OD pair, provider-call and artifact limits apply independently. There is
no automatic retry of failed routing or provider calls.

Job insertion and group-item linking share one PostgreSQL transaction and a group
row lock. Cancellation takes that same lock. A crash cannot leave a committed
unlinked child or let an in-flight preflight enqueue after cancellation. Internal
child idempotency keys use a separate namespace from user-submitted analysis keys.
Session checks precede reading the plan; scope/plan/version checks are repeated in
the enqueue transaction. Corrupt groups are stopped so they do not block unrelated
jobs. No user-controlled executable hook is accepted.

Migration `0008_analysis_groups` adds two small metadata tables. Startup checks
require them. Downgrade refuses to discard existing group history. Main README,
mathematical formulas, accepted datasets and running deployment are unchanged by
this source change.

Validation uses synthetic requests in isolated real PostgreSQL schemas. Tests
cover serial worker execution, restarting the service, canonical result retrieval,
scope/CSRF, idempotency, cancellation during preflight and after claim, transactional
rollback, concurrent admission, bounded retries, preserving successful categories,
unavailable categories, changed versions, expiry, corrupt metadata isolation and
migration retention. These tests are not proof of real city routes.

Frontend group controls and a real multi-category browser replay are not yet
connected by this backend delivery. Existing single-analysis endpoints and UI stay
available. Do not describe the server API alone as a completed one-click UI flow,
and do not average child category/city scores to invent an overall index.

## Real HTTP/worker replay, 2026-11-11

Source `3d225a2c41776c045b8eefe53ef9e60796f41942` was built into API/web images
and started on the isolated restored stack. Startup took 84.925 seconds; migration
0008 and the full 33-profile/three-router readiness checks passed. The existing
main stand was not replaced. No paid provider or Alice call was made.

One HTTP request selected `adult_polyclinic` and `child_polyclinic` for
`novorossiysk-urban-2026-11-10.3`, walk/static, departure
`2026-11-11T08:30:00+03:00`. Group
`9493a577-7dc1-433e-b294-3275456f66be` was admitted in 0.125 seconds.
Polling observed the first job running with the second pending, then the first
succeeded with the second running, then both succeeded. Total observation time
was 46.525 seconds.

| Category | Worker job | Saved analysis | Worker seconds | Real journeys |
| --- | --- | --- | ---: | ---: |
| Adult polyclinics | `7f0fcb44-be68-44ba-890a-b5993c012b8c` | `59bb3d09-ddd3-40cd-b934-868795b94c28` | 21.492 | 303 |
| Child polyclinics | `715dcfc7-37f7-45f8-a285-39ca774b8b8b` | `c7dcece7-4b52-4c10-a5eb-26f0fd975375` | 21.090 | 303 |

Both results passed canonical validation, exact group-version matching,
byte-identical repeated GET and foreign-session 404. The adult response is
7,270,017 bytes, SHA-256
`7bf0becc33f7b4656ceec290baad30b3130e7846d02334e166d33939b80a2b4b`;
the child response is 7,574,297 bytes, SHA-256
`0e1cb5260b2e50d4080150d159afa61f81cbecf120619a4ab57c1f133234a0e9`.
Each contains 303 numeric `observed_route_accessibility` estimates, marked partial,
`MINIMUM_NOT_PROVEN` and `ranking_eligible=false`; another 106 such estimates are
unknown. Category, point and territory indices remain null. This is real local
Valhalla/ML1/HTTP/PostgreSQL orchestration evidence, not a grouped React user flow,
complete-city rating or proof of a global route minimum.

Final focused backend/PostgreSQL verification: 54 passed, including 15 group
tests. Earlier related admission/private-source/quota/budget checks: 127 passed.
Ruff/format, mypy across 136 source files, schema regeneration/check and React
TypeScript/production build passed. Independent agent review remains unavailable
because the existing reviewer hit the account usage limit.
