# Draft product expansion after PR29

PR29 merged into `dev` at `436dc7494fd44ecded5d69a2e5d5de7cee881961`.
PR26 and PR27 were included by ancestry and are also merged. PR28 is a reverse
sync into `work/frontend`; it was left untouched. The main README, expert links
and main HEAD `bec7055` were preserved; README SHA256 remains
`658b597b65d22720acd055105f62956de61f29a6736ae50065299bfa1ad00890`.

## Working real categories

The next local runtime uses the existing public walking engine and bounded jobs.
It selects one geographically nearest source-confirmed, graph-correlated target
per origin/category, then calculates its actual route. This is an observed-route
estimate, not a proven nearest-by-time result. Full source snapshots and unknown
population/points remain in results; canonical city indices remain null.

| City | Planned origins | Registered walking categories |
|---|---:|---|
| Moscow | 1549 of 2361 | Adult/child clinics, schools, kindergartens, colleges/universities, railway/bus terminals, airports, shopping centers |
| Kaliningrad | 154 of 216 | The same categories; airport retains its separate historical model profile |
| Novorossiysk | 303 of 409 | The same categories except railway terminals, which have no confirmed correlated target |

The accepted national directory restores selection of 1210 cities/regions.
Catalog presence does not imply that every intercity calculation is connected.

Root actual React → worker → PostgreSQL → React runs:

- Novorossiysk adult clinics: job `825738ef-a559-48b6-992b-548d4f3ad4fc`, analysis
  `5178d3ce-b889-44fd-913a-76c88a7f041c`; 303 journeys, 106 unknown point estimates,
  19 batches, 25.355 seconds in the worker. Canonical DTO validation passed.
- Kaliningrad eight categories: job `33005b20-c440-494d-8a9d-34c782a2b106`, analysis
  `6fc3e473-eb7a-4926-a9bd-a7499952cb62`; 1230 journeys from 1232 planned pairs,
  77 batches, 263.547 seconds; 498 unknown point/category estimates retained.
  Canonical DTO validation passed. Two planned routes did not become invented times.
- First expanded Moscow school run completed 97 routing batches but failed at
  final publication with a measured cgroup OOM at 2147483648 bytes. Its failed job
  `d6516ba4-d99c-4d80-9dee-059f391357e0` is preserved. The optional
  `compose.product-city-categories.yaml` raises only worker memory to 3 GiB;
  the follow-up real replay must be recorded separately.

## Execution changes

The immutable category catalog may contain up to 32768 approved pairs, while
each submitted job remains capped at 4096 pairs and an 8 MiB plan. The Moscow
catalog contains 13941 pairs across nine categories, not one giant job. Select
categories separately or in small groups. Oversized requests now return a typed
422 budget error instead of triggering automatic retries of a deterministic 503.
Source identity, lease/cancellation, checkpoint and final publication rules remain.
The frontend preserves city order and explains how to reduce an oversized request.

Targeted checks: 13 passed / 1 PostgreSQL-dependent skip for the planning and
walking batch tests; strict mypy on changed runtime modules and Ruff passed.
Frontend TypeScript passed. These checks are not substitute real E2E claims.
PR29 separately passed all six GitHub checks, 283 frontend unit tests and 89
browser fixture tests before its merge.

## Local artifacts and restoration

Large runtime archives, native-correlation metadata, raw graphs, browser sessions
and private environment files are outside Git. The current candidate manifest is
`507305fe12379cb875af535a6904a27a158fc5c184beed6abd86957df88f2abd`.
The local delivery directory is
`/Users/kirito/waydex-full-runtime/category-all-moscow-20261111-v1/`;
`active-candidate.json` records the exact archive path, archive SHA256, bundle
manifest SHA256 and source commit. Transfer the named archive explicitly when
moving this stand to a server, and restore with `infra/full-stack/bundle.py` using
both recorded checksums. Mount the restored directory read-only as `/runtime`,
alongside the existing pinned graph artifacts and accepted bundles. Use the
existing full-stack composition plus the category-worker overlay. Private
candidate environment files include credentials and must not be committed or
published with artifacts. This is a local delivery, not an externally hosted dataset.

Moscow correlation receipts are under
`waydex-full-runtime/ml2/moscow-category-expansion-20261111-v1/`: 130 bounded
local native locate batches, no paid or external routing calls. Kaliningrad and
Novorossiysk reuse their previously verified native correlation packet. Existing
snapshots, geographic boundaries, connector tolerances and formulas were not changed.

## Still open

Passenger intercity and cargo calculations are not working production profiles
yet. The local Yandex Rasp key is empty; accepted place bindings are not schedules.
Cargo inventory contains real industrial locations/ports, but does not establish
vehicle compatibility, freight access or handling/delivery times. Pipeline
volume/capacity observations are also missing. These remain product work, not
successful demos. Alice live mode and its 500 RUB durable budget remain enabled;
this expansion makes no additional paid calls or new end-to-end LLM claim.
Full city/nationwide indices are not established.
