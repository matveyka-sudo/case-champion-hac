# Phase 2: resumable pedestrian OD work (proposal)

Status: **proposal for architect review; runtime implementation is not approved**.
The current adapter still executes one configured sample and an explicitly bounded
school subset. This document authorizes no larger sample, matrix, provider call,
new job status, migration or background retry. All prospective regression fixtures
below must be labelled `TEST_ONLY` and use mocked routing observations.

## Existing constraints

- `core/jobs.py`: `V2JobContext` binds the normalized request, snapshot hash,
  versions and access scope. `cache_fingerprint` excludes only analysis identity
  and source TTL. Idempotency remains owned by the saved job.
- `db/repositories/jobs.py`: a schema-specific advisory lock permits one worker;
  claim token, running status, lease and immutable saved inputs fence publication.
  `recover_orphans` fails interrupted jobs; it does not requeue them. `attempts<1`
  means a queued job is claimed once. Cancellation is terminal and scoped.
- `workers/runner.py` and `workers/calculation.py`: work runs in a spawned,
  killable process; heartbeat/lock loss cancels calculation. The hard timeout is
  at most 600 seconds. The current IPC sends one final result of at most 16 MiB;
  it does not support checkpoints. The child must not gain direct job SQL access.
- `JobRepository.succeed` is the sole transaction publishing a validated run,
  proposals and succeeded job. Internal checkpoints cannot appear as saved runs,
  partial publications or reusable successful result-cache entries.
- The full accepted catalog stays in the ML1 plan. A bounded execution subset,
  missing observation, timeout, bad response or provider error 442 is unknown;
  none proves global absence, minimum time or a numeric zero index.

## Approval inputs before implementation

Architect and owners must approve a versioned internal manifest containing:

1. Accepted immutable snapshot ID **and content hash**; accepted bundle manifest
   digest (including display metadata); the canonical source graph version.
2. Actual built graph fingerprint: a hash of the deployed tile manifest, source
   PBF hash, build/image identity and relevant build configuration. A symbolic
   source version, endpoint URL, `/status` version or tileset timestamp alone is
   insufficient. DevOps must attest that the server stays bound to these bytes.
3. Explicit routing profile version and hash of canonical costing options,
   including the declared 5 km/h model, ferry exclusions and precision mode.
4. Sorted accepted point/destination IDs and directed OD pairs; connector,
   correlation, serialization and evidence digests; normalized departure/time
   semantics. ML1 approves executable pair generation and retained catalog scope;
   ML2 approves data and evidence. Backend cannot invent endpoints or connectors.
5. Approved batch size, maximum pairs, total calls, bytes, per-attempt elapsed
   time, observation retention and explicit retry ceiling. Start with a small
   reviewed subset, then measure. No default full Cartesian product is allowed.
6. Checkpoint protocol/storage schema, the explicit retry entry point and scoped
   progress exposure. Any public DTO change requires the canonical contract
   owner; none is made by this proposal. No new infrastructure queue is needed.

Unapproved, incomplete or changed inputs fail readiness before routing. Larger
requests remain unavailable until the manifest and bounded call budget are
approved. This work makes zero Yandex.Rasp calls; future Rasp work needs its own
persisted shared quota and cache under the authorized 500/day limit.

## Deterministic identity and reuse

Define `execution_identity = canonical_hash` of all approved inputs above plus
manifest format version, planner/normalizer/code version, methodology/config,
traffic mode and access scope. Include the **actual built graph fingerprint**
and **actual profile/options digest** as separate fields, even when a combined
runtime artifact hash also covers them. Keep canonical source graph provenance
unchanged. The current pilot's full artifact digest in `code_version` already
binds its build/profile; generalized batches must not depend on an operator
remembering to encode these dimensions in a label.

For each directed pair, derive a pair key from execution identity, physical and
access endpoint IDs, exact canonical route payload hash and accepted matching
proofs. Direction is significant. Derive batch IDs from deterministic pair order,
batch ordinal and exact pair-key list. Derive checkpoint content hashes from the
immutable manifest and all envelopes/raw observation digests in the batch.

Only exact identity and scope matches may reuse committed checkpoints. Changing
snapshot bytes, graph tiles, walking speed, connector time, accepted projection,
precision, costing options, departure semantics, code/config or selected pair set
requires a new execution identity. Expired observations and missing/corrupt raw
files invalidate reuse. Endpoint URL/status are additional deployment checks,
not substitutes for graph identity. No unknown result is promoted to reachable
or confirmed unreachable by reuse. A normal scoped result-cache hit still uses
the existing validated immutable run and its original source TTL.

## Checkpoint storage and attempt lifecycle

Proposed backend-internal storage: immutable execution manifests and completed
batch checkpoints, with uniqueness on `(access_scope, execution_identity,
batch_id)`. Retain the source job/claim token and checksums for audit. No mutable
per-pair `running` lease table is required for one worker. A checkpoint contains
only complete batches; it retains every planned pair's typed envelope, explicit
unknown reason, raw-response evidence and exact input identity. A failed provider
observation may complete a pair as unknown. An interrupted or missing observation
cannot complete that pair. Retry policy must state whether an explicit retry may
reobserve unknowns; doing so requires a new versioned retry-policy identity.

1. **Submit:** current service normalizes/scopes the request and runs bounded
   preflight. Persist the approved execution manifest/hash in trusted job inputs
   using an additive internal model after approval. Existing original-request
   idempotency and final-result cache semantics remain intact.
2. **Claim:** acquire the current worker advisory lock, recover old orphans and
   claim once. Verify manifest, snapshot, actual build/profile and server binding
   against queued inputs before opening reusable checkpoint state.
3. **Resume:** load only complete, hash-valid checkpoints matching every identity
   dimension and scope. Compare the batch inventory with the manifest; reject
   duplicates, overlapping/foreign pairs and unexpected batches. All missing
   batches remain pending internally, never unreachable.
4. **Execute:** the child routes only the next approved bounded batch. Proposed
   versioned IPC sends a size-bounded checkpoint message, then waits for parent
   acknowledgment before continuing. The parent validates message identity,
   pair inventory, envelope structure/evidence and immutable hashes. It commits
   a checkpoint in a short transaction through the existing worker-owned
   connection, locking/checking the saved job with the same running status,
   claim token, active lease, scope and exact-input guards as publication.
   Acknowledgment follows commit. No raw provider body is logged.
5. **Cancellation/lease loss:** cancellation and checkpoint insertion serialize
   on the job row. If cancellation wins, reject the write; if a prior checkpoint
   commits first, retain it privately for audit. Cancelled jobs stay cancelled;
   checkpoint existence cannot publish a run or create an implicit retry. Lost
   database connection/advisory lock, expired lease, hard timeout or process exit
   stops work under the current worker rules. No timeout extension per batch.
6. **Explicit retry:** an interrupted/failed job remains terminal. Only a new
   authorized submission with a fresh idempotency key creates a new job/claim;
   it may attach the exact matching private execution manifest/checkpoints.
   Retrying the original idempotency key returns the original job. After a
   cancellation, reuse requires a fresh explicit submission too. Retry limits
   count all attempts against the approved execution budget. Never auto-requeue
   running, cancelled or failed jobs or reset their `attempts` counter.
7. **Finalize:** after all approved batches are committed and verified, feed the
   retained full catalog plan plus all valid envelopes to ML1 once. Recheck
   deployment binding before completion. Preserve unknown omitted catalog pairs
   and honest partial coverage. Validate the canonical result and use the
   existing fenced `succeed` transaction. No separate writer may insert a run,
   mark success or update cache ahead of that transaction. If final calculation
   exceeds the current time/IPC bounds, the attempt fails; solve that separately
   through architecture review rather than bypassing the bounds.

Crash windows are deliberate: before checkpoint commit, a subsequent explicit
attempt may repeat that batch's read-only calls; after commit but before IPC
acknowledgment, uniqueness plus identical content hash makes the repeated commit
idempotent. A conflicting hash fails rather than overwrites. Raw content-addressed
observations may outlive a killed process without becoming trusted checkpoints.
Retention cleanup must preserve every saved run's evidence and never treat raw
orphan files as accepted progress. A checkpoint committed by an earlier attempt
is immutable; its stale claim cannot write new progress after retry.

## Required validation before rollout

Use isolated PostgreSQL test schemas and spawned worker processes. Test both
transaction orderings for every race; tests must assert saved job/run/cache rows,
not just method return values.

- Deterministic manifest/pair/batch hashes; reverse OD direction differs; exact
  repeat reuses progress. Vary snapshot content under the same label, actual tile
  fingerprint, profile/options/5 km/h, source provenance, correlation precision,
  connector/evidence, category/departure, config/code and scope independently:
  every variation refuses checkpoint reuse. Foreign-scope reads reveal nothing.
- Kill child before a batch, during route I/O, after raw evidence creation,
  before/after commit, before acknowledgment and during finalization. The old job
  is failed after recovery; only explicit new submission resumes. Original
  idempotency replay never schedules another attempt.
- Cancel queued/running work; race cancel vs checkpoint and final publication.
  Expire heartbeat, lose advisory-lock connection, replace graph/profile between
  batches and attempt writes from stale claims. No later checkpoint/publication
  succeeds under an inactive claim and no cancelled job is resurrected.
- Duplicate IPC/checkpoint writes with equal hash are safe; different payload,
  foreign/missing/duplicate pair, truncated JSON, nonfinite values, malformed
  nested route/locate structures, missing raw files and oversized/compressed
  bodies fail conservatively. Bound IPC message count, memory and bytes before
  decoding; fail unknown message versions safely. Parent/child shutdown must not
  leave a writer or route process alive.
- Timeout, malformed response and provider 442 preserve known H, null unproven
  index, no ranking eligibility and no false confirmed-unreachable state. A
  committed unknown remains unknown after restart. Sparse execution never gains
  full school, population or city coverage.
- Reconstruct from checkpoints in a different read order and compare canonical
  final hashes with an uninterrupted test run using the same observation bytes.
  Final validation/proposal failure rolls back run, job success and cache. No
  checkpoints appear through saved analysis/export/explanation endpoints.
- Validate bounded request/call/time/response/result budgets, explicit retry
  count, source TTL and evidence retention. Run the existing lease/cancel,
  immutable read/cache and atomic-publication suites unchanged.

Acceptance requires architect approval of the six input groups, owner review of
cache identity and transport semantics, and the above offline/process checks.
Live acceptance is a separately approved small real subset; no mass matrix or
paid provider execution follows automatically from a green synthetic suite.
