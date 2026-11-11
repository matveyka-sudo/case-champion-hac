# Trusted product runtime

Operator hooks are `a2_api.adapters.product:registry`,
`a2_api.adapters.product:preflight` and `a2_api.adapters.product:calculate`.
Set `A2_PRODUCT_RUNTIME_PATH` and `A2_PRODUCT_RUNTIME_SHA256` identically in API
and worker. `A2_CODE_VERSION` must include that manifest digest. Mount the
manifest directory read-only; all referenced files must remain below it.
No production profile, synthetic fixture or external service is enabled by default.

`Manifest` and `InputDocument` in `adapters/product_runtime.py` and
`adapters/product_inputs.py` are the executable schemas. Obtain JSON Schema with
`Manifest.model_json_schema()` / `InputDocument.model_json_schema()` using the
installed backend environment. No public API DTO or generated canonical schema
is changed by this adapter.

The manifest schema is `waydex-product-runtime/1`. It contains accepted snapshot
file references (`path`, byte `sha256`, `snapshot_id`, canonical `content_hash`),
territory/area/profile catalogs and executable scope profiles. A profile contains:

```json
{
  "profile_id": "operator-approved-scope",
  "analysis_kind": "urban_passenger",
  "city_id": "accepted-city-id",
  "passenger_modes": ["public_transport"],
  "categories": ["school"],
  "traffic_modes": ["static"],
  "valid_from": "2026-11-05T00:00:00Z",
  "valid_to": "2026-11-30T00:00:00Z",
  "snapshot_id": "accepted-snapshot-id",
  "snapshot_hash": "canonical accepted snapshot hash",
  "graph_fingerprint": "computed normalized input hash",
  "routing_profile_fingerprint": "computed methodology and profile hash",
  "executor": "accepted_inputs",
  "inputs": {"path": "transport-inputs.json", "sha256": "file byte hash"},
  "max_route_pairs": 100,
  "evidence": [],
  "static_observations": false
}
```

This example describes the shape and deliberately has invalid placeholder hashes.
Use `input_fingerprints(profile, inputs, ProductRegistry())` to compute the two
fingerprints from actual normalized facts. They bind those facts and methodology;
they do not attest a deployed physical routing tile store. A live executor must
also bind and verify its physical graph/build/profile. The existing `walk_pilot`
executor retains its original independent artifact/build/profile/status checks;
its code version must contain both runtime digests. Its existing scope remains
Kaliningrad, walk/static, schools and the configured one-point sample.

The input envelope is:

```text
{"schema_version":"waydex-engine-inputs/1", "inputs": <typed domain input>}
```

The tagged domain inputs are:

| `kind` | Engine input facts | Public calculation |
| --- | --- | --- |
| `urban_passenger` | context, accepted points/destinations, area, original population weights, scoped coverage, optional dated timetable/static observed chains and independently calculated thresholds | `search_urban_transit`, `analyze_urban_candidates` |
| `intercity_passenger` | scope, dated timetable, explicit mode/physical identities, reference graph/scope, frequency period, boarding readiness, optional accepted car input | `build_reference`, `analyze_intercity_passenger` |
| `urban_cargo` | profile contents, physical sites and nodes, directed truck observations and minimum receipts, roster/compatibility/coverage | `analyze_urban_cargo` |
| `intercity_cargo` | profile, finite delivery scope, states, dated movement/handling actions, explicit readiness/compatibility | `analyze_intercity_cargo` |
| `pipeline` | directed accepted endpoint/connection observations and quantities with matching product/unit/period | `analyze_pipeline_connections` |

Requests dynamically choose departure, categories/priorities, modes and boundary
or node-type filters within the approved scope. There is no request-to-golden-result
table. Supplemental inputs cannot contain precomputed canonical Metric/Score
payloads, executable imports or arbitrary client tools. Point population/access
and destination semantics are checked against the immutable accepted snapshot.
Pipeline observations also retain accepted endpoint, direction and quantity facts.
Static observed chains require an explicit accepted static model and validity.
Truck receipts cannot be retimed without an independently accepted invariance
proof: changed departure is unavailable rather than receiving a fabricated receipt.
Accepted intercity car facts carry their source-bound `car_departure_at`; a car-only
request uses the public reference/car calculation and cannot silently retime that
observation. A public-only request neither calculates nor returns the car.

Complete catalog coverage requires a matching canonical full source scope for the
entire profile validity, mode and category with its evidence. Accepted destination
IDs alone do not establish a complete source catalog. Serving objects outside the
city boundary remain eligible under that source scope. Population completeness is
checked against all original cells, including positive or unknown excluded weights;
the accepted point list cannot erase those weights.

A supplemental timetable declares `timetable_service_clock` explicitly as
`gtfs_noon_elapsed_v1` or `local_service_midnight_v1`. The server derives a
`TimetableSourceReceipt` by matching every active normalized run to canonical
trip/date/source/timezone/events/pickup/dropoff facts and calendar evidence.
Declared complete schedules must contain the exact canonical dated execution
inventory. Current canonical data cannot prove exhaustive physical connectors,
run-specific transfer permissions or continuation closure; their complete flags
are rejected until an independently verifiable closure receipt is supported.
Verified catalog evidence alone is never an exhaustive routing certificate.

For dated urban timetables, the admitted route-pair budget counts actual searches:
origins and destinations must have physical nodes in the timetable scope, and a
destination selected in two categories consumes two searches. Points and catalog
objects without those boundaries remain in the input and result as unknown.
Physical endpoints cannot become timetable stops or connect through another
access node. Untimetabled inputs retain the existing catalog-pair bound.
Once all result reference edges exist, dated urban results remove only unused
evidence definitions and compute a fresh hash before scenario proposals are built.
The accepted snapshot, all numerical values, uncertainty and reference edges stay
unchanged.

A known best route without minimum proof keeps its actual timed journey and raw
value, with `completeness=partial` and `MINIMUM_TIME_UNPROVEN`. Scenarios retain
both observed values but require an explicit unknown minimum-time effect. This
exception applies only to `minimum_journey_seconds` with evidence and a matching
partial after journey; other known partial numerical effects keep their existing
comparison requirements. Full proven effects retain numeric deltas. No public
schema field changes are needed.

Each input file is bounded to 32 MiB; all manifest, snapshot and supplemental
bytes together are bounded to 64 MiB before JSON decoding. Duplicate keys,
nonfinite numbers, wrong hashes, missing evidence and mismatched identities fail
closed. Invalid profiles cannot enable their analysis kind. Unsupported request
scope and an exceeded approved route-pair budget fail preflight with a typed
reason. A missing physical point/school connector remains unknown; an accepted
stop-to-stop timetable route does not establish that connector or a city index.

The spawned worker, 600-second maximum, 16 MiB final result wire limit, claim/lease
fencing and sole atomic publication transaction remain unchanged. This adapter
does not implement resumable checkpoints, schedule scenarios or run paid providers.
The resumable design remains in `phase2-batches.md` pending its separate stage.
The decoded JSON budget defaults to 16 MiB; reviewed deployments can explicitly
set `A2_WORKER_RESULT_DECODED_MIB=32` for bounded lossless result frames, as described
in the API README. This setting is operator configuration, never a request field.

Verification includes TEST_ONLY public-engine numeric controls for all four
analysis kinds and pipeline utilization, real canonical DTO validation, immutable
input tampering, partial/null preservation and dynamic request binding. These
checks do not assert production data readiness or physical-graph minimum proofs.
