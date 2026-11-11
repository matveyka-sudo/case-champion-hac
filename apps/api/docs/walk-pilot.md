# Kaliningrad walk/static pilot

The operator-selected adapter is `a2_api.adapters.walk_pilot`. It connects the
accepted `kaliningrad-urban-2026-11-10.2` or `kaliningrad-urban-2026-11-10.3`
bundle to the existing PostgreSQL job,
spawned worker, ML1 pedestrian analysis and canonical v2 result publication.
It supports one configured sample point and a bounded subset of accepted schools.
The accepted catalog remains intact when ML1 evaluates completeness.

Only those two exact bundle IDs are allowed: the reviewed school inventory and
configured destination subset are bounded to 76 for `.2` and 77 for `.3`, with
one configured sample point. `.3` adds one verified gymnasium campus and retains
partial school coverage. No latest-version lookup or fallback selects a bundle.
Existing `.2` artifacts remain `.2`; opting into `.3` requires an explicit new
artifact with matching bundle/build/connector snapshot IDs, its full digest in
the code version, and newly queued inputs bound to that exact snapshot hash.
Unchanged physical endpoints or source graphs do not permit old queued inputs
to run against the new snapshot. These bounds do not enable a mass matrix.

Configure API and worker with the same immutable runtime and code version:

```text
A2_REGISTRY_HOOK=a2_api.adapters.walk_pilot:registry
A2_PREFLIGHT_HOOK=a2_api.adapters.walk_pilot:preflight
A2_CALCULATION_HANDLER=a2_api.adapters.walk_pilot:calculate
A2_WALK_PILOT_ARTIFACT_PATH=/absolute/path/runtime.json
A2_WALK_PILOT_ARTIFACT_SHA256=<SHA256 of exact runtime.json bytes>
A2_WALK_PILOT_EVIDENCE_DIR=/absolute/path/existing-writable-observations
A2_CODE_VERSION=walk-pilot-<implementation-commit>-<full-artifact-sha256>
```

Existing database/schema configuration and migration/worker commands apply. The
full artifact digest must occur in `A2_CODE_VERSION`, so queued inputs and cache
identities bind the build, profile, endpoint and selected sample. Changing an
artifact requires a new digest and code version. Backend manifests need the
installed engine and data-pipeline packages; source `PYTHONPATH` alone is a local
test setup, not deployment acceptance.

The JSON artifact has these fields:

```json
{
  "bundle_id": "kaliningrad-urban-2026-11-10.2",
  "endpoint_url": "http://127.0.0.1:8007",
  "status_expectation": {"version": "3.5.1", "tileset_last_modified": 123},
  "point_ids": ["<accepted sample point ID>"],
  "destination_ids": ["<accepted school destination ID>"],
  "build": {
    "snapshot_id": "kaliningrad-urban-2026-11-10.2",
    "source_graph_version": "<exact snapshot provenance graph version>",
    "build_id": "<immutable deployed build identity>",
    "graph_version": "<built Valhalla graph fingerprint>",
    "engine_version": "3.5.1",
    "profile_version": "waydex-walk-static-5kmh-v1",
    "pedestrian_options_json": "{\"type\":\"foot\",\"walking_speed\":5}",
    "evidence_refs": ["<build and explicit model configuration evidence>"]
  },
  "connectors": [],
  "correlations": [],
  "evidence_refs": []
}
```

The status number and IDs above are placeholders. `status_expectation` comes from
the actual pinned server and must include version and tileset timestamp. Build
evidence must retain the deployed image identity, source PBF hash, tile manifest
hash and model configuration hash: `/status` does not expose tile content hashes.
The deployment owner must keep that server bound to the accepted build.

Connector rows are ML1 `WalkConnector` fields, with coordinate objects containing
`lon` and `lat`. Supply only canonical accepted endpoints (ML2 supplements can
also contain quarantined endpoints). The explicit 5 km/h model determines
connector seconds; unconfirmed connectors retain null seconds. Correlation rows
are ML1 `ValhallaCorrelation` fields without `build` (the loader supplies it).
Their coordinates and evidence must come from actual `/locate` observations.
The additive precision mode requires `coordinate_precision_digits: 6` and
`serialization_evidence_refs`; default correlations retain exact equality.

Runtime evidence uses canonical `EvidenceRef`: `evidence_id`, `source_id`, HTTP(S)
`url`, `retrieved_at`, nullable `observed_at`, and `section`. Every supplemental
reference must resolve to that inventory or accepted snapshot evidence. IDs must
not collide with different accepted evidence. Preflight checks the hash, source
graph, prepared inputs, executable selected pairs and server status before queue
acceptance. The worker checks them again against saved snapshot/config/version
inputs. Existing cancellation, lease, timeout and atomic publication own job
lifecycle; the adapter does not write job/run SQL.

For each chosen executable pair the worker observes `/locate` and `/route`, saves
content-addressed raw JSON atomically, checks every returned locate candidate,
and inspects pedestrian mode and ferry exclusion. Request location echoes do not
prove actual matching. Provider failures, missing projection evidence or shifted
endpoints do not produce zero travel times or confirmed absence. Responses are
bounded to 4 MiB and compressed payloads are refused before decoding.

Valid observations yield a partial three-leg physical journey (origin connector,
network walk, destination connector). Network-only geometry is not promoted into
complete journey geometry. Canonical source graph version stays the source
version; built graph/profile identities remain in pinned runtime and evidence.
No minimum-time search is attested. The city index is null, ranking is disabled,
school catalog coverage remains partial, population exclusions stay explicit,
and map features describe the configured sample only. The known ML1 H metric
remains visible even when provider routes are unavailable.

Malformed nested route containers are rejected before pedestrian-mode inspection;
the pair remains unknown, including null legs or maneuvers. Destination labels
come from the checksum-verified accepted bundle rather than opaque object IDs.
The saved sample-point population retains its accepted year, method and evidence;
it describes the associated cell, not occupants at the point. This two-feature
map has no cell feature, so it intentionally has no dangling `cell_id` reference.
The UI may show the saved population metadata without requiring that reference.

The proposed bounded resume lifecycle and required architecture approvals are in
[phase2-batches.md](phase2-batches.md). No batch runtime or mass matrix is enabled.

Focused regression tests deliberately mock provider observations and label all
proofs `TEST_ONLY`; real accepted geography does not make those responses real.
The `.2`/`.3` regression requires both accepted bundles. In an isolated backend
checkout before the data commit is integrated, set `A2_DATA_DIR` to the reviewed
data checkout's `data` directory; no source-data copy or fallback is performed.
Live deployment acceptance requires a separate real API→PostgreSQL→spawned worker
run and inspection of its saved raw observations and canonical partial result.

This pilot makes zero Yandex.Rasp requests. The user's allowance is 500 requests
per day; intercity/provider fetching remains disabled. Enabling it later requires
a shared persisted quota guard and cache before any provider execution.
