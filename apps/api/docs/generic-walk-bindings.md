# Per-profile walking inputs

The unified `product` dispatcher can register independent urban walking profiles
for any accepted city. Every profile uses the existing ML1 `prepare_walk_inputs`,
`plan_walk_routes`, `analyze_walk_responses`, calculation child, checkpoint
repository and final publication transaction. No city selects a different engine.
The original `walk_pilot` gate and the reviewed certified airport model are
unchanged. Ordinary HTTP observations cannot authorize their minimum proof.

An operator adds `walk_binding: FileRef` to a `walk_batches` profile. The referenced
private document has this shape:

```json
{
  "schema_version": "waydex-urban-walk-binding/1",
  "artifact": {"path": "artifact.json", "sha256": "<reviewed file SHA256>"},
  "approval": {"path": "approval.json", "sha256": "<reviewed file SHA256>"},
  "route_executor": "valhalla_http_observation/1"
}
```

`RuntimeArtifact` in `adapters/walk_runtime.py` is the authoritative private input
schema. It binds `snapshot_id`, `city_id`, approved point/destination inventories,
the actual `ValhallaBuild`, connector projections, accepted correlations,
evidence, and the profile's own `endpoint_url` and status expectation. Paths and
URLs come only from the pinned operator files. Bound profiles do not fall back to
the legacy artifact, approval or routing endpoint environment configuration.

The existing approval retains its graph/tile/source PBF/configuration/image and
physical-pair bindings. Categories now use the canonical nine-category type and
must be unique and sorted. Requests may select an approved category subset;
preflight plans only the selected approved pairs. Missing endpoints stay unknown.
At least one executable approved pair per declared category is required for
registration. `area_id` remains unsupported until its population scope is proven.

The loader verifies all referenced bytes and the immutable producer bundle. The
graph's PBF checksum must match the producer source version, and the entire
AcceptedData canonical projection must match that bundle. Connector fields must
equal ML2's pure `project_walk_connectors` projection. A retained ID cannot hide
changed population, coordinates, traversability, time, category or source graph.
No request supplies normalized metrics or arbitrary connector data.

Startup verification has a cumulative **96 MiB** document/source budget. This
covers the roughly 78 MB current three-city input inventory, including producer
files, and retains the **32 MiB** individual document limit. Hashing producer files
uses bounded streaming before model parsing. It does not authorize a larger OD
execution. Resource tests measure fresh-process wall time and peak RSS separately
from serialized bytes.

The preserved macOS Python 3.12 test measurement for three `TEST_ONLY` deployment
artifacts over real Kaliningrad `.2`, Moscow `.3` and Novorossiysk `.2` source
bundles was **7.9344 seconds**, **737,771,520 bytes peak RSS**. It includes no
routing or native certificate replay. It is a measured startup cost, not a
production memory guarantee or acceptance of later corrected source revisions.
The resource regression prints a fresh measurement on each run.

API metadata preflight admits one expensive validation per process. Additional
requests receive retryable `503 RUNTIME_INPUTS_BUSY` immediately, without joining
an executor queue. The admitted thread owns capacity across the full adapter
chain; cancellation or the HTTP timeout does not release it while work continues.
Scheduling failures and cancellation before execution release capacity without
executing the abandoned scheduled work. No runtime object cache is introduced.

With corrected Moscow `.4` and Novorossiysk `.3` inputs, the later fresh load
measured **6.1889 seconds / 892,223,488 bytes peak RSS**. An actual configured ASGI
app retaining its registry plus admitted Moscow preflight measured
**10.4301 seconds / 999,227,392 bytes peak RSS**. Retaining app and runtime during
one additional Moscow source revalidation measured **4.0953 seconds**, peaking at
**1,092,927,488 bytes**. These macOS Python 3.12 measurements use test deployment
artifacts over real sources, without database lifespan, provider calls or native
replay. A 1 GiB deployment has insufficient headroom for the measured path;
deployment memory limits require separate Linux/cgroup acceptance.

Private `BatchPlan`/`BatchExecution` version `/2` additionally requires the pinned
binding hash and a server-derived executor implementation fingerprint. These also
enter deterministic route identities. The frozen execution, normalized request,
snapshot/config/code and access scope remain in the existing result-cache key.
Cross-city, graph, source, category, binding or executor changes cannot reuse old
checkpoints. Old `/1` serialization and identity omit the new fields entirely.

`A2_WALK_OBSERVATION_DIR` selects the shared content-addressed observation store
for `/2`, with the old `A2_WALK_PILOT_EVIDENCE_DIR` as a fallback. Legacy `/1`
continues using its original directory. Each checkpoint still validates raw
request/build/response bytes, source evidence and retention. Existing lease,
cancellation, attempt/call budgets and atomic publication fences are retained.
Resume requires a fresh explicit submission; the original idempotency key
returns its original terminal job.

The approved bounds remain 4096 pairs, 16 pairs per checkpoint, three explicit
attempts, 600 seconds per job, and 16 MiB checkpoint inventory/final IPC. Whole-city
Cartesian routing does not follow from registering several profiles. It requires
reviewed inputs and a separately reviewed capacity plan. No provider traffic is
performed by metadata preflight.

Observed HTTP routes remain partial with `MINIMUM_TIME_UNPROVEN`; all unproven
indices stay null and nonranking. H comes from the accepted administrative area
and public configuration. The producer's historical approach-time projection
rounds once to 0.1 seconds; `WALK_CONNECTOR_TIME_0_1_SECOND_PRECISION` discloses it.
This is separate from exact native/physical certificate operands.

`Runtime.walk_bindings` exposes the verified bindings and derived `resolved_pairs`
to server code. A binding also exposes read-only `destination_names` from its
verified producer bundle, limited to accepted canonical city destinations with
known names. The existing bundle manifest hash binds those display facts.
`Runtime.profile_failures` retains only allowlisted stable failure
codes, never exception text, paths, URLs or source bodies. Failure of one profile
cannot borrow another city's readiness. Public readiness/progress DTOs are a
separate contract change; internal completed work is never a percentage claim of
catalog, population, minimum-time or index completeness.
