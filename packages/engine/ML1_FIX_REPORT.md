# ML1 fix — verification and integration handoff

## PR #36 review follow-up — 2026-11-11

Implemented against `0d97963a3b64973c9cb50ab820e78ffaba3678c4` on `ml1-fix`.
Fresh fetch still identifies `origin/dev` as
`9557ee5d67e1883dd076cb5f6ad532b51fe44588`, already an ancestor. Two implementation
agents worked on separate files in the isolated ML1 worktree; a third reviewed
the final diff and ran independently constructed adversarial probes.

- **P1 closed:** confirmed movement modes must agree across every occurrence of
  a physical execution, scoped by snapshot and service date. An unbound first
  copy cannot hide conflicting later bindings. The shared guard also checks
  physical stop patterns and activity at baseline, overlay and occupancy entry
  points. Equal raw run IDs remain checked even when a secondary extract omits
  a redundant identity binding.
- Additional departures retain their declared primary ID. Where another extract
  has a different provider label, it receives a deterministic distinct run alias
  under the same physical identity. This prevents a copied raw label from changing
  road into rail, preserves full same-ID copy equality, and allows rebaselining
  and a second scenario. Alias IDs are collision-checked, all changed executions
  remain visible, and shared resource coverage requires matching occupancy plus
  calendar/event proof for every alias. Forged dates reject; missing proof stays
  unverified. Generated `physical:*` and `unmapped-physical:*` labels cannot grant
  a raw provider record an unaccepted mode.
- **P2 closed:** nonpositive passenger completions carry private mode/first-boarding
  diagnostics. Frequency incompleteness applies only to the matching category and
  public union in the half-open requested interval. Global source limitations,
  missing mode coverage and resource exhaustion remain conservative. Overall
  seven-day selection retains its incomplete diagnosis, and later valid positive
  continuations remain candidates.

Added **24 regression cases** in `test_pr36_physical_alias_modes.py` and
`test_pr36_frequency_diagnostics.py`. Full engine: **1286 passed, zero skipped,
25.81 s**. Ruff check/format: 191 files; mypy source/examples: 139 files; product
config and diff checks passed. Public exports, signatures and dataclass fixtures
passed unchanged; formulas, configuration, DTO and OpenAPI were not changed.
New imports are absolute. Independent reviewer: **APPROVE**, 55 targeted tests
including 12 independently authored probes and public API compatibility.

Seven relevant API test modules passed **129 tests in 8.93 s**, with **35 skipped**
because a dedicated PostgreSQL test database and the external v4 demo runtime
artifact were not configured. This is an
adapter compatibility check, not database or UI E2E acceptance. The first attempt
using the pytest console entry point could not import `apps`; rerunning via
`uv run python -m pytest` from the repository root resolved the runner path.

Replayed the four stress workloads, 16-job scenario benchmark, 24 seeded scenario
identities, synthetic demo matrix and pinned ML2 verifier. Stress full-result
hashes and state counts are unchanged. The 24 scenario identities, complete demo
matrix and ML2 verification JSON match the previously committed artifacts exactly.
Raw follow-up measurements and artifact equality checks are retained in
`benchmarks/pr36_followup_verification.json`. Timing differences on this shared
machine are observations, not a claimed speedup from these correctness fixes.
Three cold and three warm samples of the complete synthetic demo give median
wall times of **1.077/0.930 s** for 24 baseline rows and **7.592/6.700 s** for
18 supported scenario calculations including publication validation. The other
three comparison rows remain explicit unsupported-operation controls. The 16-job
scenario benchmark gives cold/warm medians **0.176/0.175 s** for simulation and
**0.176/0.176 s** for validation. These are finite engine workloads, not frontend
or provider latency measurements.

ML2 remains `0225db8ed1f7ffbd50d3fdba7e895339f3544ca7`; manifest pin is unchanged.
Its 77 declared file checksums were checked, with nine baseline rows and six
comparison calculations replayed. This does not close the independent provenance,
epoch, walking-access or endpoint-boundary findings on PR #34, nor certify all
24/21 declared rows. The six replayed comparisons still have unproven effects;
three coordination feasibility states remain unverified. No cross-owner changes
or merges were performed. A corrected ML2 delivery and real API/worker/DB/UI
integration are still separate acceptance gates.

Reproduction (from repository root):

```sh
uv sync --frozen --all-packages --group dev
uv run python -m pytest packages/engine/tests -q
uv run ruff check packages/engine
uv run ruff format --check packages/engine
uv run mypy packages/engine/src packages/engine/examples
uv run python scripts/check_product_config.py
uv run python packages/engine/examples/benchmark_ml1_fix.py --repeats 3
uv run python packages/engine/examples/benchmark_ml1_scenario.py --jobs 16 --repeats 3
uv run python packages/engine/examples/benchmark_ml1_scenario.py --identity-check
uv run python packages/engine/examples/ml1_demo_runner.py
uv run python packages/engine/examples/ml1_demo_runner.py --benchmark
uv run python packages/engine/examples/ml1_demo_runner.py --ml2-root "$ML2_DEMO_ROOT"
```

`ML2_DEMO_ROOT` must point to the exact pinned `data/demo/waydex-demo-2026-11-11.1`
directory. API module names and commands are recorded in the follow-up JSON.
The sections below preserve the earlier implementation measurements and history.

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
