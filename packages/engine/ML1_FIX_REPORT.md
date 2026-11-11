# ML1 fix — verification and integration handoff

Base: `9557ee5d67e1883dd076cb5f6ad532b51fe44588` (`origin/dev`, fetched 2026-11-11).
Delivery branch: `ml1-fix`. Implementation is isolated from the user's main checkout.
Review/implementation agents: GPT-6.1 Sol, reasoning effort ultra, each on a separate
detached worktree at that exact base. Final integration and commit ownership stay
with the main agent.

## Baseline evidence

The unchanged base passed **1195 engine tests in 29.56 seconds**, Ruff check,
Ruff format (170 files), and strict mypy over production source (105 files).
Commands, from the repository root:

```sh
uv sync --frozen --all-packages --group dev
.venv/bin/python -m pytest packages/engine/tests -q
.venv/bin/ruff check packages/engine
.venv/bin/ruff format --check packages/engine
.venv/bin/mypy --strict packages/engine/src
```

Current packaging supersedes the historical `package=false` limitation in older
audit entries: `uv build --package a2-engine` built the wheel and sdist. The wheel
installed into a separate environment, and `python -I` imported it from `/tmp`
without PYTHONPATH or the source checkout. The public export count is 269.
The installed wheel also loaded the approved configuration through an explicit
path and calculated the independent control `I(T=H)=50`. Installed deployments
must supply that configuration path; the wheel does not bundle repository config.
No manifest or lock changes are needed for that existing capability.

## Contract boundaries and open deliveries

Open deliveries inspected: PR #34 (`work/ml2-demo`, pinned
`0225db8ed1f7ffbd50d3fdba7e895339f3544ca7`), PR #33 (`work/devops-demo-stack`), and
PR #28 (`dev` integration). No other owner's commits are merged into this branch.

The urban `private_car` mode is supported. The scenario DTO currently admits only
`coordinate_transfer` and `additional_departure`. Engine coordination supports
scheduled compatible intercity freight execution, but does not authorize changing
urban cargo handling or transshipment duration.

PR #34 itself marks the three urban cargo operations `reduce_handling_wait` and
six intercity freight operations `reduce_transshipment_time` /
`add_compatible_departure` as requiring contract changes. Those nine exact proposed
operations must not be reported as production Scenario Engine passes. Separate
tests of supported freight departure coordination do not certify those proposals.

## Confirmed defects and exact optimizations

- A zero-duration passenger destination path could suppress a later valid path
  from the same first boarding. Search now retains positive-duration candidates
  while marking invalid input evidence incomplete; frequency cannot certify the
  invalid departure as known. A 3,600-second continuation and invalid-only case
  reproduce the original failure independently.
- Scenario aliases with disjoint provider run IDs but the same accepted physical
  execution were not updated together. Overlay membership now uses snapshot,
  service date and physical identity, with consistency checks on stop patterns.
- A first-stop arrival or final-stop departure could masquerade as a real
  transfer. Both reachable incoming and onward outgoing legs are required, and
  transfer rules are resolved on the normalized physical timetable.
- An incomplete copy of a physical execution could overwrite confirmed resource
  occupancy. Confirmed occupancy is retained; incomplete evidence stays visible.
- Resource occupancy omitted accepted initial/final stop dwell and could approve
  an overlapping additional departure. Occupancy now spans the first accepted
  arrival through final departure; both boundary cases have negative regressions.
- Timetable transfer lookup is indexed, footpaths are grouped by origin, boarding
  search skips expired departures by binary search, and immutable run-ID encoding
  uses a bounded exact-key cache. No route/result cache is introduced.
- Cargo completed paths are materialized once; passenger chain ordering/hashes
  are reused; OD observations are sorted/grouped once instead of once per slice.
- Scenario metric IDs use the same canonical JSON bytes directly, and generated
  effects are not regenerated unnecessarily. Public result validation still
  reconstructs the baseline and after result; it does not trust supplied deltas.

Independent review rejected an intermediate CargoProfile memoization because
Python equality conflates signed zero while canonical JSON preserves it. That
optimization was removed, and a signed-zero identity regression was retained.

The historical 303-journey case is a walk/static workload, documented in
`docs/full-product/CATEGORY_EXPANSION_20261111.md` and
`apps/api/docs/analysis-groups.md`: roughly 21–25 seconds worker time, 19 batches,
large 7 MB responses, and an externally hosted runtime bundle. The new synthetic
303-execution timetable benchmark is a different workload and cannot explain or
certify that deployment's performance.

## Measured performance and acceptance limits

Raw reproducible results are in `benchmarks/ml1_fix_stress.json`,
`benchmarks/ml1_demo_matrix.json`, `benchmarks/ml1_demo_performance.json`, and
`benchmarks/ml1_pr34_verification.json`. The complete finite control matrix,
independent equations and seven detailed controls are in [DEMO.md](DEMO.md).

| Workload | Cold median before → after, seconds | Returned states/work |
| --- | --- | --- |
| Synthetic 303-execution timetable | 0.7890 → 0.1977 | 305 states |
| Intercity, 240 events | 0.4629 → 0.2579 | 6,858 states; 309 candidates |
| Cargo branching width 32 | 0.5671 → 0.2813 | 1,122 states; 1,024 paths |
| OD, 32 points × 28 slices | 0.6543 → 0.3344 | 27,776 directed pair-slices |

Every complete output hash and returned state count in that table is unchanged.
Three fresh-process cold samples and nine warm samples are recorded, with
min/median/max and separate Python allocation tracing. Measurements are local
CPython 3.12.13 on MacBookPro17,1; the raw artifact notes brief shared-host load.

The final complete demo batch (24 baselines) took 1.823/1.838/1.942 seconds
cold min/median/max, and 1.553/1.568/1.596 seconds warm. The batch of all 18 legal
comparisons, including publication validation, took 12.428/12.558/12.644 seconds
cold and 10.936/11.018/13.189 seconds warm. These are batch totals, not one UI
request. The largest measured individual case was 5.291 seconds and included
both urban operations. Peak process RSS was at most 57.83 MiB in these demo runs.
The 18-comparison batch does NOT satisfy a ten-second batch budget. Individual
finite engine cases are below ten seconds; API/DB/network/browser budget remains
unverified. Cold demo wall time includes parent-observed process startup and
output serialization. Instrumented exclusive Python phase costs are separate.

The independent fixture matrix verifies 24 baselines and 18 actual comparisons;
three urban-cargo mutations are explicitly blocked. Actual PR #34 inputs are
separate: 77 file checksums verified, nine urban baselines replayed, six walk/car
minimum controls agree, three public-transport territory indices remain unknown.
Six external urban transit operations reproduce selected OD times and waits, but
scope-wide effects remain incomplete. This is not full acceptance of PR #34's
24/21 matrix and is not production E2E readiness.

## Final regression

The integrated production/test changes passed **1262 engine tests, zero skips,
43.26 seconds** (1195 baseline plus 67 added cases). Ruff check and formatting
(189 files), strict mypy over source and examples (139 files), public API and
architecture guards all passed. Existing full suite includes native/finite walk,
reference, Fraction OD, timetable, cargo, traffic, pipeline and formula controls.
The scope of independent review is not an exhaustive formal proof of every
possible accepted dataset; unavailable remote routing artifacts remain untested.

The public ML1 exports and signatures remain compatible, canonical contracts and
formulas are unchanged. New code/fixtures/results/docs are confined to engine.
SSH authenticated `matveyka-sudo`; GitHub associates both author and committer of
the previously published `f54122b` with that account using
`matveyyuri <murin8102@gmail.com>`. Final commits use that identity explicitly,
without global Git configuration changes or bot trailers.

## Owner handoffs

**BACKEND / contracts:** `packages/contracts/src/a2_contracts/v2/scenarios.py` owns
the operation allowlist. Before adding a handling operation, specify the accepted
site/node/direction/cargo-profile target, stage identity, old/new duration and its
units, dated resource occupancy/capacity and evidence, calendar, immutable baseline
and dataset versions. Added equipment alone does not prove a linear reduction of
waiting. An approved deterministic operation model must define what may change,
resource conflict rejection and complete affected-entity expansion. Acceptance:
three urban cargo cases through the real engine with independently checked values,
resource-conflict rejection, unchanged unrelated stages, and lossless publication.
Do not implement an API-only numerical adjustment to a stored result.

**ML2:** retain a single dataset format and publish the exact manifest, seed,
checksums and provenance. Differentiate modelled edge times/operations from real
observations. Supply explicit reference infrastructure, applicable modes, service
dates, physical execution aliases, transfer minima, resource schedules and cargo
stage compatibility. Expected values computed by the same engine are useful
examples but are not independent mathematical oracles. Reconcile proposed cargo
operations with the contract owner before labelling their comparisons executable.

**BACKEND / performance:** measure queue wait, input loading, route hydration,
engine calculation, scenario publication validation, serialization and database
commit separately using one request/job identity. `adapters/truck_runtime.py`
hydrates missing site/node pairs with serial external requests; the number and
latency of those requests must be reported separately from the pure engine.
`adapters/product.py` invokes urban routing per point/category/destination.
These code observations do not establish the cause of the previously reported
multi-minute Moscow run or the 40-second browser run; those exact runs have not
been reproduced by this engine-only check.

**FRONTEND:** label finite synthetic scope, keep unknown/not-applicable distinct,
display worsening and connection transitions, and disable proposals without a
supported server operation. Record click-to-result and rendering time separately
from backend/engine spans. Engine tests do not establish UI E2E acceptance.

**DEVOPS:** current wheel packaging works. Provision pinned accepted inputs,
native executors where required, routing services, and isolated demo configuration.
Pass the approved configuration path explicitly to installed engine loaders.
Measure cold startup separately from warm calculation. Enforce version/profile/
mode/time separation for any integration-level cache; never share synthetic and
real results. Full user-flow acceptance still requires API/worker/DB/UI execution
within the agreed ten-second budget on the deployment machine.

## Changed files

```text
packages/engine/DEMO.md
packages/engine/ML1_FIX_REPORT.md
packages/engine/README.md
packages/engine/benchmarks/ml1_demo_matrix.json
packages/engine/benchmarks/ml1_demo_performance.json
packages/engine/benchmarks/ml1_fix_stress.json
packages/engine/benchmarks/ml1_pr34_verification.json
packages/engine/benchmarks/ml1_scenario_identity.json
packages/engine/benchmarks/ml1_scenario_performance.json
packages/engine/examples/benchmark_ml1_fix.py
packages/engine/examples/benchmark_ml1_scenario.py
packages/engine/examples/ml1_demo/__init__.py
packages/engine/examples/ml1_demo/benchmark.py
packages/engine/examples/ml1_demo/cargo.py
packages/engine/examples/ml1_demo/intercity.py
packages/engine/examples/ml1_demo/matrix.py
packages/engine/examples/ml1_demo/ml2_handoff.py
packages/engine/examples/ml1_demo/ml2_scenarios.py
packages/engine/examples/ml1_demo/model.py
packages/engine/examples/ml1_demo/oracle.py
packages/engine/examples/ml1_demo/urban.py
packages/engine/examples/ml1_demo_runner.py
packages/engine/src/a2_engine/freight/delivery/search.py
packages/engine/src/a2_engine/intercity/analysis/calculation.py
packages/engine/src/a2_engine/intercity/analysis/frequency.py
packages/engine/src/a2_engine/routing/scheduled.py
packages/engine/src/a2_engine/routing/timetable.py
packages/engine/src/a2_engine/routing/timetable_models.py
packages/engine/src/a2_engine/scenario/bindings.py
packages/engine/src/a2_engine/scenario/engine.py
packages/engine/src/a2_engine/scenario/feasibility.py
packages/engine/src/a2_engine/scenario/models.py
packages/engine/src/a2_engine/scenario/overlay.py
packages/engine/src/a2_engine/urban/od/collection.py
packages/engine/tests/test_ml1_demo_matrix.py
packages/engine/tests/test_ml1_fix_engine.py
packages/engine/tests/test_ml1_fix_scenarios.py
```
