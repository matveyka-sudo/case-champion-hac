# Worker-side freight network observations

An `accepted_inputs` urban cargo profile may opt into `truck_routing` in its
hash-pinned product manifest. The existing analysis endpoint and worker then
compute missing selected site/node pairs. No route collection CLI is required.
Prepared routes remain reusable. Missing physical sites/nodes are not invented.

The binding is `waydex-truck-routing/1`: local `endpoint_url`,
`graph_directory_env`, `graph_manifest_sha256`, engine `3.5.1`,
`tileset_last_modified`, `max_pairs`, `max_seconds`, `requests_per_second`,
`request_timeout_seconds`. The cargo context build ID must equal the build
manifest hash. Recompute the profile's input fingerprints and the runtime hash
when adding the binding. The binding is operator data, not an HTTP parameter.

Apply `infra/full-stack/compose.product-truck-routing.yaml` after the existing
product/cargo overlays. Restore each graph archive into its operator directory
(`WAYDEX_KALININGRAD_TRUCK_GRAPH_DIR` names the actual Kaliningrad router mount,
independently of `WAYDEX_KALININGRAD_GRAPH_DIR` used by the native certificate)
and mount the **same bytes** read-only into its router and worker. The worker
checks manifest, config, tile hashes/inventory and tiles-directory timestamp;
HTTP status is checked around each new route. HTTP status alone is not a
cryptographic attestation that a remote router serves those tiles. Deployment
must preserve the local router/mount association. Do not bind a public provider
to this local graph policy.
`product-check` verifies all distinct truck graph mounts before activation,
including timestamp identity and rejection of a preferred unverified tile archive.

The fixed model sends `costing=truck`, explicit freeflow speeds, and the accepted
vehicle's weight (kg converted to tonnes), height, width and length. Actual site
and facility coordinates come from the accepted snapshot. Correlation to the
network does **not** confirm either physical approach or freight compatibility.
Only observed network movement is available; approaches and compatibility remain
unknown, and no minimum receipt is generated. Existing ML1 formulas and strict
index eligibility remain unchanged.

Execution is serial and bounded to at most 1024 pairs and 240 seconds per job,
with a per-request timeout. `requests_per_second` currently limits route
observations; each uncached observation also performs two status checks.
Three consecutive unknown observations stop provider probing; remaining pairs
stay unknown in ML1. Wrong graph identity aborts publication. Parent worker
cancellation, deadline, lease checks and atomic PostgreSQL publication remain
in force. This executor does not yet report per-pair PostgreSQL progress or use
the pedestrian checkpoint protocol.

Raw responses, request inputs, versions, hashes and retrieval times are stored
under the existing operator observation volume in `road-observations-v1`.
Successful observations are atomically cached by snapshot, graph/binding,
profile/requirements, algorithm/code versions, session scope, date, direction,
coordinates and endpoint IDs. Unknown observations are retained as evidence but
are not negative-cached. Retrying the same job inputs reuses completed positive
observations. A process lock serializes executors and duplicate pairs; worker
termination releases it. Storage is bounded to 256 MiB/10000 files, with 256 KiB
per provider response. Reaching the bound fails explicitly; it does not evict
evidence referenced by saved results. Preserve this volume during server moves.

Final analysis cache entries for these snapshots expire after at most five
minutes so a provider outage cannot freeze an unknown result indefinitely.
Saved results remain immutable and retrievable after cache expiry.

Synthetic regression coverage: `apps/api/tests/test_truck_runtime.py`. This is
not evidence of real HTTP, PostgreSQL or browser execution. Record those gates
separately for each release. Moscow with zero accepted freight destinations
continues to return inventory/unknown, not a fabricated road result.
