"""TEST_ONLY real PG claim fences stay responsive during bounded raw validation."""

import asyncio
import threading
import time
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
import pytest_asyncio
from a2_api.core.errors import DomainError
from a2_api.db.repositories import checkpoints as module
from a2_api.db.repositories.checkpoints import CheckpointRepository
from a2_api.db.repositories.jobs import JobRepository
from a2_api.workers.runner import Worker
from batch_handlers import result
from test_od_checkpoints import batch_context as batch_context
from test_od_checkpoints import enqueue, timeout_checkpoint
from test_walk_batches import AcknowledgedChannel, prepared_claim, walk_batches
from test_walk_batches import approved as approved
from test_walk_pilot import configured as configured


@pytest_asyncio.fixture
async def saved(database, batch_context):
    _, schema, url = database
    # Scale the production 10s-query/long-validation conflict down for a fast,
    # deterministic regression at precisely the same real PG lock boundary.
    pool = await asyncpg.create_pool(
        url,
        min_size=2,
        max_size=5,
        command_timeout=0.2,
        server_settings={"search_path": schema},
    )
    try:
        repo = JobRepository(pool, lease_seconds=3)
        claim = await enqueue(repo, batch_context)
        progress = CheckpointRepository(repo)
        await progress.begin(claim)
        checkpoint = timeout_checkpoint(claim.context.batch_execution)
        await progress.put(claim, checkpoint)
        yield pool, repo, claim, checkpoint
    finally:
        await pool.close()


def gate_validation(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = module.validate_raw_files

    def gated(*args):
        value = original(*args)
        entered.set()
        assert release.wait(5), "Test must release the bounded validation gate"
        return value

    monkeypatch.setattr(module, "validate_raw_files", gated)
    return entered, release


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["begin", "put", "finalize", "publish"])
async def test_raw_validation_does_not_lock_out_heartbeat(saved, batch_context, monkeypatch, stage):
    pool, repo, claim, checkpoint = saved
    if stage == "begin":
        await repo.cancel(claim.job.job_id, scope=claim.context.access_scope)
        claim = await enqueue(repo, batch_context)
    entered, release = gate_validation(monkeypatch)
    progress = CheckpointRepository(repo)
    operation = asyncio.create_task(
        progress.begin(claim)
        if stage == "begin"
        else progress.put(claim, checkpoint)
        if stage == "put"
        else progress.finalize(claim)
        if stage == "finalize"
        else repo.succeed(claim, result(claim))
    )
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        assert await asyncio.wait_for(repo.heartbeat(claim), 0.5)
    finally:
        release.set()
        await operation
    assert await pool.fetchval("SELECT count(*) FROM runs") == int(stage == "publish")


@pytest.mark.asyncio
async def test_worker_keeps_heartbeat_through_finalization_and_actual_publication(
    saved, monkeypatch
):
    pool, repo, claim, _ = saved
    original = module.validate_raw_files
    validations = []

    def slow(*args):
        validations.append(True)
        time.sleep(0.4)
        return original(*args)

    monkeypatch.setattr(module, "validate_raw_files", slow)

    class SavedWorker(Worker):
        async def _calculate(self, incoming, handler, timeout_seconds):
            await CheckpointRepository(self.repository).finalize(incoming)
            return result(incoming)

    async with repo.worker_lock(asyncio.Event()) as acquired:
        assert acquired
        worker = SavedWorker(
            repo,
            handler="TEST_ONLY:unused",
            code_version=claim.context.code_version,
            timeout_seconds=5,
        )
        await worker._calculate_with_lease(claim, asyncio.Event())
    assert validations == [True, True]  # Fresh raw validation on publication too.
    assert (await repo.get_job(claim.job.job_id, claim.context.access_scope)).status == "succeeded"
    assert await pool.fetchval("SELECT count(*) FROM runs") == 1


@pytest.mark.asyncio
async def test_cancellation_during_publication_prevalidation_stays_responsive_and_unpublished(
    saved, monkeypatch
):
    pool, repo, claim, _ = saved
    entered, release = gate_validation(monkeypatch)
    operation = asyncio.create_task(repo.succeed(claim, result(claim)))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        cancelled = await asyncio.wait_for(
            repo.cancel(claim.job.job_id, claim.context.access_scope), 0.5
        )
        assert cancelled.status == "cancelled"
    finally:
        release.set()
    with pytest.raises(DomainError, match="no longer active"):
        await operation
    assert await pool.fetchval("SELECT count(*) FROM runs") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation", ["payload", "hash", "bytes", "created_at", "source_claim", "removed"]
)
async def test_publication_rechecks_the_whole_validated_checkpoint_snapshot(
    saved, monkeypatch, mutation
):
    pool, repo, claim, _ = saved
    entered, release = gate_validation(monkeypatch)
    operation = asyncio.create_task(repo.succeed(claim, result(claim)))
    statements = {
        "payload": "UPDATE od_checkpoints SET payload=jsonb_set(payload,'{observations,0,pair_id}',to_jsonb(repeat('f',64)))",
        "hash": "UPDATE od_checkpoints SET content_hash=repeat('f',64)",
        "bytes": "UPDATE od_checkpoints SET payload_bytes=payload_bytes+1",
        "created_at": "UPDATE od_checkpoints SET created_at=created_at+interval '1 second'",
        "source_claim": "UPDATE od_checkpoints SET source_claim_token='foreign'",
        "removed": "DELETE FROM od_checkpoints",
    }
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        await pool.execute(statements[mutation])
    finally:
        release.set()
    with pytest.raises(DomainError, match="inventory changed"):
        await operation
    assert await pool.fetchval("SELECT count(*) FROM runs") == 0
    assert (await repo.get_job(claim.job.job_id, claim.context.access_scope)).status == "running"


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["claim_token", "scope", "context", "lease"])
async def test_publication_rechecks_fresh_job_fences_after_validation(saved, monkeypatch, mutation):
    pool, repo, claim, _ = saved
    entered, release = gate_validation(monkeypatch)
    operation = asyncio.create_task(repo.succeed(claim, result(claim)))
    statements = {
        "claim_token": "UPDATE jobs SET claim_token='foreign'",
        "scope": "UPDATE jobs SET access_scope='foreign'",
        "context": "UPDATE jobs SET payload=jsonb_set(payload,'{context,analysis_id}',to_jsonb('foreign'::text))",
        "lease": "UPDATE jobs SET lease_expires_at=clock_timestamp()-interval '1 second'",
    }
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        await pool.execute(statements[mutation])
    finally:
        release.set()
    with pytest.raises(DomainError):
        await operation
    assert await pool.fetchval("SELECT count(*) FROM runs") == 0


@pytest.mark.asyncio
async def test_post_commit_false_heartbeat_cannot_turn_success_into_failure(saved, monkeypatch):
    pool, repo, claim, _ = saved
    committed, observed = asyncio.Event(), asyncio.Event()
    succeed = repo.succeed

    async def delayed_return(*args, **kwargs):
        await succeed(*args, **kwargs)
        committed.set()
        await observed.wait()

    async def heartbeat(incoming):
        await committed.wait()
        observed.set()
        return False

    monkeypatch.setattr(repo, "succeed", delayed_return)
    monkeypatch.setattr(repo, "heartbeat", heartbeat)

    class SavedWorker(Worker):
        async def _calculate(self, incoming, handler, timeout_seconds):
            return result(incoming)

    worker = SavedWorker(
        repo, handler="TEST_ONLY:unused", code_version=claim.context.code_version, timeout_seconds=5
    )
    await worker._calculate_with_lease(claim, asyncio.Event())
    assert (await repo.get_job(claim.job.job_id, claim.context.access_scope)).status == "succeeded"
    assert await pool.fetchval("SELECT count(*) FROM runs") == 1


@pytest.mark.asyncio
async def test_overall_hard_deadline_includes_publication(saved, monkeypatch):
    pool, repo, claim, _ = saved
    entered, release = gate_validation(monkeypatch)

    class SavedWorker(Worker):
        async def _calculate(self, incoming, handler, timeout_seconds):
            return result(incoming)

    worker = SavedWorker(
        repo,
        handler="TEST_ONLY:unused",
        code_version=claim.context.code_version,
        timeout_seconds=0.3,
    )
    try:
        with pytest.raises(DomainError) as error:
            await worker._calculate_with_lease(claim, asyncio.Event())
        assert error.value.code == "job_timeout" and entered.is_set()
    finally:
        release.set()
    assert await pool.fetchval("SELECT count(*) FROM runs") == 0


@pytest.mark.asyncio
async def test_lost_worker_session_during_validation_cannot_publish(saved, monkeypatch):
    pool, repo, claim, _ = saved
    entered, release = gate_validation(monkeypatch)
    lost = asyncio.Event()

    class SavedWorker(Worker):
        async def _calculate(self, incoming, handler, timeout_seconds):
            return result(incoming)

    async with repo.worker_lock(lost) as acquired:
        assert acquired
        worker = SavedWorker(
            repo,
            handler="TEST_ONLY:unused",
            code_version=claim.context.code_version,
            timeout_seconds=5,
        )
        operation = asyncio.create_task(worker._calculate_with_lease(claim, lost))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            connection = repo._worker_connection.get()
            await pool.execute("SELECT pg_terminate_backend($1)", connection.get_server_pid())
            await asyncio.wait_for(lost.wait(), 1)
            with pytest.raises((DomainError, asyncpg.InterfaceError)):
                await operation
        finally:
            release.set()
    assert await pool.fetchval("SELECT count(*) FROM runs") == 0


@pytest.mark.asyncio
async def test_publication_revalidates_real_raw_files_after_finalization(database, approved):
    pool, _, _ = database
    snapshot, _, _, artifact = approved
    source = await prepared_claim(snapshot)
    repo = JobRepository(pool)
    await repo.enqueue(
        "analysis",
        source.request,
        source.context,
        "TEST_ONLY-raw-recheck",
        "TEST_ONLY",
        scope=source.context.access_scope,
    )
    claim = await repo.claim("TEST_ONLY-raw-recheck")
    progress = CheckpointRepository(repo)
    await progress.begin(claim)
    channel = AcknowledgedChannel(claim.context.batch_execution)
    calculated = walk_batches.calculate(claim, channel)
    for checkpoint in channel.completed:
        await progress.put(claim, checkpoint)
    await progress.finalize(claim)
    raw = channel.completed[0].observations[0].raw_observations[0]
    (artifact.parent / (raw.sha256 + ".json")).write_text("TEST_ONLY-corrupt-after-finalization")
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        await repo.succeed(claim, calculated)
    assert await pool.fetchval("SELECT count(*) FROM runs") == 0


@pytest.mark.asyncio
async def test_publication_fresh_clock_rechecks_frozen_context_cache_expiry(
    database, batch_context, monkeypatch
):
    pool, _, _ = database
    request, context = batch_context
    context = context.model_copy(
        update={"cache_valid_until": datetime.now(UTC) + timedelta(seconds=1)}
    )
    repo = JobRepository(pool)
    claim = await enqueue(repo, (request, context))
    progress = CheckpointRepository(repo)
    await progress.begin(claim)
    await progress.put(claim, timeout_checkpoint(claim.context.batch_execution))
    original = repo._check_v2_result

    async def after_validation(*args):
        await original(*args)
        await asyncio.sleep(
            max(0, (context.cache_valid_until - datetime.now(UTC)).total_seconds()) + 0.05
        )

    monkeypatch.setattr(repo, "_check_v2_result", after_validation)
    with pytest.raises(DomainError, match="validity expired"):
        await repo.succeed(claim, result(claim))
    assert await pool.fetchval("SELECT count(*) FROM runs") == 0
