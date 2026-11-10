# Private resumable walk batches

The approved batch mechanism preserves the existing one-worker queue and final
publication transaction. It adds no public job status, progress DTO, automatic
retry, provider quota or payment authorization. No batch runtime is enabled by
default. Calculation remains in the killable child process with one hard timeout
of at most 600 seconds across resume and every batch.

Use the unified product hooks with a profile whose `executor` is `walk_batches`,
or explicitly configure `a2_api.adapters.walk_batches:registry`, `:preflight` and
`:calculate`. Existing `walk_pilot` hooks retain their reviewed original limits.
The generalized batch loader accepts multiple explicitly approved physical points
and destinations; it does not inherit the old one-point restriction.

API and worker require the same immutable files and these operator settings:

- Existing `A2_WALK_PILOT_ARTIFACT_PATH`, `A2_WALK_PILOT_ARTIFACT_SHA256` and writable
  `A2_WALK_PILOT_EVIDENCE_DIR` for the real HTTP executor and append-only evidence.
- `A2_WALK_BATCH_APPROVAL_PATH` and `A2_WALK_BATCH_APPROVAL_SHA256` for the approved
  `waydex-walk-batch-approval/1` document. Its schema is `Approval.model_json_schema()`.
- `A2_CODE_VERSION` contains both artifact and approval digests. Unified product
  deployments additionally contain the product manifest digest.
- Migration head `0005_od_checkpoints` applied to the isolated deployment schema.

The approval document contains accepted snapshot/canonical hash, accepted bundle
manifest digest (including display metadata file digests), artifact digest,
explicit city/categories and directed `(point_id,destination_id)` pairs. It also
contains batch size, at most three explicit attempts, a total reserved provider-call
budget, checkpoint byte budget, observation retention and an absolute expiry.
There is no implicit Cartesian execution. A generated plan may retain the full
accepted destination catalog while executing only approved, executable pairs.
Missing connectors remain unknown. The private manifest records approved and
unresolved pair counts separately.

`RoutingBinding` pins the actual build manifest by file SHA256. The server checks
its tile-file inventory digest, source PBF hash, image identity and configuration
hash against the declared binding, and binds it to the runtime build/source graph,
endpoint and exact engine costing/profile hash. Its graph manifest must be inside
the approval directory. The deployed graph must be attested immutable by DevOps;
HTTP `/status` alone is not a tile identity proof. In unified profiles,
`graph_fingerprint` is the physical build-manifest SHA256 and
`routing_profile_fingerprint` is `runtime.build.profile_hash`.

The source build manifest uses the existing real build format: `source_sha256`,
`config_sha256`, `image_id`/`image_repo_digests`, and `files` containing tile path,
SHA256 and byte count. `tile_manifest_sha256` is the canonical hash of those file
records sorted by path. Do not substitute a label, endpoint or generated sample
for these reviewed build inputs.

Preflight returns a private `BatchPlan`; the service binds it to normalized request,
versions, snapshot hash and access scope as `BatchExecution`. When that field is
absent it is omitted from old saved inputs and cache fingerprints. The child only
gets a bounded checkpoint channel, never a database connection. The parent
reserves attempt/call budgets, validates pair inventory, typed envelopes, original
raw-file hashes, build/payload identity, actual locate projections and pedestrian
mode, then commits under the current worker connection and active job-row fence.
Acknowledgment follows commit. Checkpoints are limited to 8 MiB each and 16 MiB
total; a plan is limited to 8 MiB. Provider calls are conservatively reserved before
an attempt; unused reservations are not silently refunded after a crash.

Cancellation, expired lease, stale claim or loss of the worker's database session
prevents later checkpoint writes and final publication. A committed checkpoint
remains private after cancellation or a crash. Orphan recovery still fails the old
job. Only a fresh user submission with a new idempotency key may create a retry,
within the approved attempt/call budget; replaying the old key returns the old job.
Matching complete checkpoints are revalidated before reuse and finalization. Missing,
corrupt or expired raw observations fail reuse. Unknown observations stay unknown;
this version never reobserves committed unknowns or attests a minimum/unreachable
route from HTTP data. Native finite-graph certificates need a separate approved
executor/protocol version.

Final analysis is computed once from the retained catalog and all committed
observations. Saved runs/proposals/cache are published solely through the existing
`JobRepository.succeed` transaction. Checkpoints cannot appear as saved runs or
explanations. Retention cleanup must preserve evidence referenced by saved runs;
this stage adds no destructive cleanup process.

Offline acceptance uses disposable PostgreSQL schemas and TEST_ONLY HTTP fixtures:
immutable duplicate/conflicting writes, cross-scope isolation, explicit retry,
cancel/write ordering, expired/stale claims, exhausted budgets, crash after commit
before acknowledgment, malformed/oversized IPC, hard timeout, raw corruption,
multi-point incomplete connectors and identical reconstruction without rerouting.
These tests authorize no additional real OD pairs or paid/provider runs.
