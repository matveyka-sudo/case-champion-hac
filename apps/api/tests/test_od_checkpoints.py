"""TEST_ONLY: durable OD progress, scoped explicit retry and transaction fencing."""

import asyncio
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from a2_api.adapters import walk_pilot
from a2_api.core.batches import (
    BatchCheckpoint,
    BatchExecution,
    BatchPlan,
    PairObservation,
    PlannedPair,
)
from a2_api.core.errors import DomainError
from a2_api.core.jobs import V2JobContext, batch_context_fingerprint
from a2_api.db.repositories.checkpoints import CheckpointRepository
from a2_api.db.repositories.jobs import JobRepository
from a2_api.workers.runner import Worker
from a2_contracts.common import canonical_hash
from a2_data_pipeline.store import load_bundle
from a2_engine import WalkResponseEnvelope, WalkRouteRequest, plan_walk_routes, prepare_walk_inputs
from pydantic import TypeAdapter
from test_walk_pilot import claim_for as pilot_claim
from test_walk_pilot import configured as configured


@pytest.fixture
def batch_context(configured):
    _, _, snapshot = configured
    source = pilot_claim(snapshot)
    context = source.context
    runtime = walk_pilot.load_runtime()
    inputs = prepare_walk_inputs(
        snapshot.model_dump(mode="json"),
        city_id="kaliningrad",
        departure_at=context.normalized_request.departure_at,
        methodology_version=context.versions.methodology_version,
        config_hash=context.versions.config_hash,
        search_parameters_id="TEST_ONLY-batches",
        build=runtime.build,
        connectors=runtime.connectors,
        correlations=runtime.correlations,
    )
    plan = plan_walk_routes(inputs, point_ids=runtime.point_ids, categories=["school"])
    pairs = []
    for route in plan.requests:
        if route.destination_id in runtime.destination_ids:
            encoded = {"request": TypeAdapter(WalkRouteRequest).dump_python(route, mode="json")}
            pairs.append(PlannedPair(pair_id=canonical_hash(encoded), request=route))
    batch = BatchPlan(
        schema_version="waydex-walk-execution/1",
        snapshot_id=snapshot.snapshot_id,
        snapshot_hash=context.snapshot_hash,
        bundle_manifest_hash=canonical_hash(
            load_bundle(snapshot.snapshot_id).manifest.model_dump(mode="json")
        ),
        source_graph_version=context.versions.graph_version,
        actual_graph_fingerprint="b" * 64,
        profile_fingerprint=runtime.build.profile_hash,
        runtime_artifact_hash=runtime.digest,
        approval_manifest_hash="c" * 64,
        pairs=sorted(pairs, key=lambda p: p.pair_id),
        approved_pair_count=len(pairs),
        unresolved_pair_count=0,
        batch_size=1,
        max_attempts=3,
        max_provider_calls=100,
        max_checkpoint_bytes=1024 * 1024,
        observation_retention_seconds=3600,
        valid_until=datetime.now(UTC) + timedelta(hours=1),
    )
    execution = BatchExecution(
        **batch.model_dump(mode="python"),
        access_scope=context.access_scope,
        context_fingerprint=batch_context_fingerprint(context),
    )
    context = V2JobContext.model_validate(
        {**context.model_dump(mode="json"), "batch_execution": execution.model_dump(mode="json")}
    )
    return source.request, context


async def enqueue(repo, batch_context, *, scope=None):
    request, context = batch_context
    context = context.model_copy(update={"analysis_id": "TEST_ONLY-" + uuid4().hex})
    if scope is not None:
        execution = context.batch_execution
        context = context.model_copy(update={"access_scope": scope, "batch_execution": None})
        execution = execution.model_copy(
            update={
                "access_scope": scope,
                "context_fingerprint": batch_context_fingerprint(context),
            }
        )
        context = context.model_copy(update={"batch_execution": execution})
    accepted = await repo.enqueue(
        "analysis",
        request,
        context,
        "TEST_ONLY-" + uuid4().hex,
        "TEST_ONLY-trace",
        scope=context.access_scope,
    )
    claim = await repo.claim("TEST_ONLY-token-" + uuid4().hex)
    assert claim is not None and claim.job.job_id == accepted.job_id
    return claim


def timeout_checkpoint(execution):
    batch_id, pairs = execution.batches[0]
    observations = []
    for pair in pairs:
        r = pair.request
        envelope = WalkResponseEnvelope(
            r.request_id,
            r.point_id,
            r.destination_id,
            r.build,
            r.payload_hash,
            r.origin.correlation_id,
            r.destination.correlation_id,
            "timeout",
            None,
            (),
        )
        observations.append(
            PairObservation(
                pair_id=pair.pair_id, envelope=envelope, evidence=[], raw_observations=[]
            )
        )
    return BatchCheckpoint(
        schema_version="waydex-walk-checkpoint/1",
        execution_identity=execution.identity,
        batch_id=batch_id,
        observations=observations,
    )


@pytest.mark.asyncio
async def test_checkpoint_is_private_immutable_and_only_explicit_new_job_resumes(
    database, batch_context
):
    pool, _, _ = database
    repo = JobRepository(pool)
    checkpoints = CheckpointRepository(repo)
    claim = await enqueue(repo, batch_context)
    assert await checkpoints.begin(claim) == []
    checkpoint = timeout_checkpoint(claim.context.batch_execution)
    await checkpoints.put(claim, checkpoint)
    await checkpoints.put(claim, checkpoint)
    assert await pool.fetchval("SELECT count(*) FROM od_checkpoints") == 1
    assert await pool.fetchval("SELECT count(*) FROM runs") == 0
    assert (
        await repo.get_job(claim.job.job_id, scope=claim.context.access_scope)
    ).status == "running"
    changed = checkpoint.model_copy(
        update={
            "observations": [
                checkpoint.observations[0].model_copy(
                    update={
                        "envelope": replace(
                            checkpoint.observations[0].envelope, outcome="http_error"
                        )
                    }
                )
            ]
        }
    )
    with pytest.raises(DomainError, match="conflicts"):
        await checkpoints.put(claim, changed)
    await repo.cancel(claim.job.job_id, scope=claim.context.access_scope)
    with pytest.raises(DomainError, match="no longer active"):
        await checkpoints.put(claim, checkpoint)
    resumed = await enqueue(repo, batch_context)
    assert resumed.context.batch_execution.identity == claim.context.batch_execution.identity
    assert await checkpoints.begin(resumed) == [checkpoint]
    assert await pool.fetchval("SELECT attempts FROM od_executions") == 2
    assert await pool.fetchval("SELECT source_job_id FROM od_checkpoints") == claim.job.job_id
    assert (
        await repo.get_job(claim.job.job_id, scope=claim.context.access_scope)
    ).status == "cancelled"


@pytest.mark.asyncio
async def test_checkpoint_cannot_be_read_or_written_through_other_scope(database, batch_context):
    pool, _, _ = database
    repo = JobRepository(pool)
    progress = CheckpointRepository(repo)
    original = await enqueue(repo, batch_context)
    await progress.begin(original)
    checkpoint = timeout_checkpoint(original.context.batch_execution)
    await progress.put(original, checkpoint)
    await repo.cancel(original.job.job_id, scope=original.context.access_scope)
    foreign = await enqueue(repo, batch_context, scope="TEST_ONLY-other-owner")
    assert await progress.begin(foreign) == []
    with pytest.raises(ValueError, match="EXECUTION_MISMATCH"):
        await progress.put(foreign, checkpoint)
    assert await pool.fetchval("SELECT count(*) FROM od_checkpoints") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("fence", ["stale_token", "expired_lease", "cancelled"])
async def test_inactive_claim_cannot_commit_progress(database, batch_context, fence):
    pool, _, _ = database
    repo = JobRepository(pool)
    progress = CheckpointRepository(repo)
    claim = await enqueue(repo, batch_context)
    await progress.begin(claim)
    checkpoint = timeout_checkpoint(claim.context.batch_execution)
    if fence == "stale_token":
        claim = claim.model_copy(update={"claim_token": "TEST_ONLY-stale"})
    elif fence == "expired_lease":
        await pool.execute(
            "UPDATE jobs SET lease_expires_at=now()-interval '1 second' WHERE job_id=$1",
            claim.job.job_id,
        )
    else:
        await repo.cancel(claim.job.job_id, scope=claim.context.access_scope)
    with pytest.raises(DomainError, match="no longer active"):
        await progress.put(claim, checkpoint)
    assert await pool.fetchval("SELECT count(*) FROM od_checkpoints") == 0
    assert await pool.fetchval("SELECT count(*) FROM runs") == 0


@pytest.mark.asyncio
async def test_lease_expiring_during_raw_validation_cannot_commit(
    database, batch_context, monkeypatch
):
    from a2_api.db.repositories import checkpoints as module

    pool, _, _ = database
    repo = JobRepository(pool)
    progress = CheckpointRepository(repo)
    claim = await enqueue(repo, batch_context)
    await progress.begin(claim)
    checkpoint = timeout_checkpoint(claim.context.batch_execution)
    original = module.validate_raw_files
    validated = []

    def slow(*args):
        validated.append(True)
        time.sleep(0.7)
        return original(*args)

    monkeypatch.setattr(module, "validate_raw_files", slow)
    await pool.execute(
        "UPDATE jobs SET lease_expires_at=clock_timestamp()+interval '0.5 second' WHERE job_id=$1",
        claim.job.job_id,
    )
    with pytest.raises(DomainError, match="no longer active"):
        await progress.put(claim, checkpoint)
    assert validated == [True]
    assert await pool.fetchval("SELECT count(*) FROM od_checkpoints") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("budget", ["attempts", "provider_calls"])
async def test_budget_is_not_reset_by_fresh_idempotency_key(database, batch_context, budget):
    pool, _, _ = database
    request, context = batch_context
    values = {"max_attempts": 1} if budget == "attempts" else {"max_provider_calls": 5}
    execution = context.batch_execution.model_copy(update=values)
    context = context.model_copy(update={"batch_execution": execution})
    repo = JobRepository(pool)
    progress = CheckpointRepository(repo)
    first = await enqueue(repo, (request, context))
    await progress.begin(first)
    await repo.cancel(first.job.job_id, scope=context.access_scope)
    second = await enqueue(repo, (request, context))
    with pytest.raises(DomainError, match="budget exhausted"):
        await progress.begin(second)
    assert await pool.fetchval("SELECT attempts FROM od_executions") == 1
    assert await pool.fetchval("SELECT count(*) FROM od_attempts") == 1


def test_saved_execution_cannot_be_rebound_to_changed_user_departure(batch_context):
    _, context = batch_context
    request = context.normalized_request.model_copy(
        update={"departure_at": context.normalized_request.departure_at + timedelta(minutes=15)}
    )
    with pytest.raises(ValueError, match="Batch execution differs"):
        V2JobContext.model_validate(
            {
                **context.model_dump(mode="json"),
                "normalized_request": request.model_dump(mode="json"),
            }
        )


@pytest.mark.asyncio
async def test_checkpoint_total_byte_budget_is_enforced_before_insert(database, batch_context):
    pool, _, _ = database
    request, context = batch_context
    execution = context.batch_execution.model_copy(update={"max_checkpoint_bytes": 1})
    context = context.model_copy(update={"batch_execution": execution})
    repo = JobRepository(pool)
    progress = CheckpointRepository(repo)
    claim = await enqueue(repo, (request, context))
    await progress.begin(claim)
    with pytest.raises(DomainError, match="byte budget exhausted"):
        await progress.put(claim, timeout_checkpoint(claim.context.batch_execution))
    assert await pool.fetchval("SELECT count(*) FROM od_checkpoints") == 0
    assert await pool.fetchval("SELECT count(*) FROM runs") == 0


@pytest.mark.asyncio
async def test_expired_unknown_checkpoint_is_not_promoted_or_reused(database, batch_context):
    pool, _, _ = database
    repo = JobRepository(pool)
    progress = CheckpointRepository(repo)
    first = await enqueue(repo, batch_context)
    await progress.begin(first)
    checkpoint = timeout_checkpoint(first.context.batch_execution)
    await progress.put(first, checkpoint)
    await repo.cancel(first.job.job_id, scope=first.context.access_scope)
    await pool.execute("UPDATE od_checkpoints SET created_at=now()-interval '2 hours'")
    second = await enqueue(repo, batch_context)
    with pytest.raises(DomainError, match="observations expired"):
        await progress.begin(second)
    assert await pool.fetchval("SELECT count(*) FROM runs") == 0


@pytest.mark.asyncio
async def test_checkpoint_commit_and_cancel_serialize_on_saved_job(
    database, batch_context, monkeypatch
):
    pool, _, _ = database
    repo = JobRepository(pool)
    progress = CheckpointRepository(repo)
    claim = await enqueue(repo, batch_context)
    await progress.begin(claim)
    checkpoint = timeout_checkpoint(claim.context.batch_execution)
    active, release = asyncio.Event(), asyncio.Event()
    original = progress._active

    async def hold(connection, incoming):
        now = await original(connection, incoming)
        active.set()
        await release.wait()
        return now

    monkeypatch.setattr(progress, "_active", hold)
    committing = asyncio.create_task(progress.put(claim, checkpoint))
    await asyncio.wait_for(active.wait(), timeout=5)
    cancelling = asyncio.create_task(
        repo.cancel(claim.job.job_id, scope=claim.context.access_scope)
    )
    await asyncio.sleep(0.02)
    release.set()
    await asyncio.gather(committing, cancelling)
    assert await pool.fetchval("SELECT count(*) FROM od_checkpoints") == 1
    assert (
        await repo.get_job(claim.job.job_id, scope=claim.context.access_scope)
    ).status == "cancelled"
    assert await pool.fetchval("SELECT count(*) FROM runs") == 0


@pytest.mark.asyncio
async def test_spawned_checkpoint_worker_publishes_only_after_all_batches(database, batch_context):
    pool, _, _ = database
    repo = JobRepository(pool)
    request, context = batch_context
    accepted = await repo.enqueue(
        "analysis",
        request,
        context,
        "TEST_ONLY-spawn",
        "TEST_ONLY-trace",
        scope=context.access_scope,
    )
    await Worker(
        repo,
        handler="batch_handlers:success",
        code_version=context.code_version,
        timeout_seconds=10,
    ).run_once()
    saved = await repo.get_job(accepted.job_id, scope=context.access_scope)
    assert saved.status == "succeeded"
    assert await pool.fetchval("SELECT count(*) FROM od_checkpoints") == len(
        context.batch_execution.batches
    )
    result = await repo.get_run(saved.run_id, scope=context.access_scope)
    assert result.status == "partial" and all(s.value is None for s in result.scores)


@pytest.mark.asyncio
@pytest.mark.parametrize("handler", ["omit", "malformed", "oversized", "hang"])
async def test_bad_or_interrupted_child_cannot_publish_partial_progress(
    database, batch_context, handler
):
    pool, _, _ = database
    repo = JobRepository(pool)
    request, context = batch_context
    accepted = await repo.enqueue(
        "analysis",
        request,
        context,
        "TEST_ONLY-spawn",
        "TEST_ONLY-trace",
        scope=context.access_scope,
    )
    await Worker(
        repo,
        handler="batch_handlers:" + handler,
        code_version=context.code_version,
        timeout_seconds=1,
    ).run_once()
    assert (await repo.get_job(accepted.job_id, scope=context.access_scope)).status == "failed"
    assert await pool.fetchval("SELECT count(*) FROM runs") == 0


@pytest.mark.asyncio
async def test_crash_after_commit_before_ack_resumes_only_in_new_job(
    database, batch_context, monkeypatch
):
    pool, _, _ = database
    repo = JobRepository(pool)
    request, context = batch_context
    first = await repo.enqueue(
        "analysis",
        request,
        context,
        "TEST_ONLY-first",
        "TEST_ONLY-trace",
        scope=context.access_scope,
    )
    original = CheckpointRepository.put

    async def crash(self, claim, checkpoint):
        await original(self, claim, checkpoint)
        raise RuntimeError("TEST_ONLY parent crash after commit before acknowledgment")

    monkeypatch.setattr(CheckpointRepository, "put", crash)
    worker = Worker(
        repo,
        handler="batch_handlers:success",
        code_version=context.code_version,
        timeout_seconds=10,
    )
    await worker.run_once()
    assert (await repo.get_job(first.job_id, scope=context.access_scope)).status == "failed"
    assert await pool.fetchval("SELECT count(*) FROM runs") == 0
    assert await pool.fetchval("SELECT count(*) FROM od_checkpoints") == 1
    replay = await repo.replay("analysis", request, "TEST_ONLY-first", scope=context.access_scope)
    assert replay.job_id == first.job_id and replay.status == "failed"
    monkeypatch.setattr(CheckpointRepository, "put", original)
    retry_context = context.model_copy(update={"analysis_id": "TEST_ONLY-retry"})
    second = await repo.enqueue(
        "analysis",
        request,
        retry_context,
        "TEST_ONLY-explicit-retry",
        "TEST_ONLY-trace",
        scope=context.access_scope,
    )
    await worker.run_once()
    assert (await repo.get_job(second.job_id, scope=context.access_scope)).status == "succeeded"
    assert await pool.fetchval("SELECT count(*) FROM od_checkpoints") == 1
    assert await pool.fetchval("SELECT source_job_id FROM od_checkpoints") == first.job_id
