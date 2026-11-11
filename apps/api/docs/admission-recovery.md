# Recovering a slow analysis submission

A cold metadata admission can outlive the browser's 15-second request deadline.
In the observed 47a8 release, the first POST lost its client at 15 seconds, a
retry received `RUNTIME_INPUTS_BUSY`, and a third POST started another metadata
validation before the first request committed its job at 17.3 seconds. That
third client also timed out. PostgreSQL correctly retained one job, but the UI
had not received its ID.

`AnalysisService` now shares one pending admission through context construction
and the final enqueue. It first checks durable database replay. If no row exists,
the same session-scoped idempotency key and existing request hash attach to the
pending operation. Each successful caller receives its own response request ID
and the same persisted job ID. The first request's trace remains the stored trace.
Every newly reserved producer repeats the durable replay before engine/input
validation: an earlier outer SELECT may have captured a miss before another
producer committed, then returned after that producer released its slot.

The operation has one slot per service instance and at most 16 attached callers
including the original caller. Other new keys or scopes receive the existing
retryable busy response. A conflicting request under the same scoped key returns
409. Existing persisted jobs can still replay while a new admission is pending.

Each caller waits at most 30 seconds. Its cancellation or timeout leaves the
single producer running; retrying does not reset the producer's existing
30-second preflight deadline or PostgreSQL command deadlines. No validated-input
cache is introduced. Completion and failure release the admission slot. The
production lifespan cancels and joins pending admission before closing its
database pool, so a shielded metadata thread cannot later enqueue through a
closed pool. Already committed jobs retain their ordinary durable lifecycle.

Sharing applies within one service instance and process. PostgreSQL's existing
scoped idempotency checks and unique enqueue constraint remain authoritative
across instances, process restarts and concurrent API processes. An additional
process may still perform its own metadata validation.

Regression coverage uses deterministic gates at preflight and enqueue with real
HTTP and an isolated PostgreSQL schema. It checks client cancellation, recovery
at both gates, payload conflicts, session isolation, bounded callers and waits,
failure cleanup, shutdown, exact saved requests, request IDs, durable replay and
concurrent service instances. The heavy-runtime reservation, public contracts,
SQL, queue identities, calculation logic and browser deadlines are unchanged.
