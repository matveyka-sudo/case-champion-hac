"""TEST_ONLY compact OD checkpoints over real accepted endpoints, no provider calls."""

from datetime import UTC, datetime

import pytest
from a2_api.core.batches import BatchCheckpoint, BatchExecution
from a2_api.core.errors import DomainError
from a2_api.core.jobs import V2JobContext
from a2_api.core.walk_observations import observation_directory
from a2_api.core.walk_route_artifacts import (
    read_route_artifact,
    resolve_checkpoint,
    write_route_artifact,
)
from a2_api.db.repositories.checkpoints import CheckpointRepository, validate_raw_files
from a2_api.db.repositories.jobs import JobRepository
from a2_contracts.api import ErrorDetail
from test_od_checkpoints import batch_context as batch_context
from test_od_checkpoints import enqueue, timeout_checkpoint
from test_walk_pilot import configured as configured


@pytest.fixture
def compact_context(batch_context, monkeypatch):
    request, context = batch_context
    old = context.batch_execution
    values = old.model_dump(mode="json") | {
        "schema_version": "waydex-walk-execution/3",
        "runtime_binding_hash": "a" * 64,
        "executor_fingerprint": "d" * 64,
        "checkpoint_format": "artifact_refs_v2",
        "max_route_artifact_bytes": 256 * 1024 * 1024,
    }
    execution = BatchExecution.model_validate(values)
    context = V2JobContext.model_validate(
        context.model_dump(mode="json") | {"batch_execution": execution.model_dump(mode="json")}
    )
    return request, context


def artifact_checkpoint(execution):
    inline = timeout_checkpoint(execution)
    references = [
        write_route_artifact(observation, execution) for observation in inline.observations
    ]
    return BatchCheckpoint(
        schema_version="waydex-walk-checkpoint/2",
        execution_identity=execution.identity,
        batch_id=inline.batch_id,
        observations=references,
    )


def test_compact_refs_roundtrip_without_inline_response_and_exact_execution(compact_context):
    execution = compact_context[1].batch_execution
    checkpoint = artifact_checkpoint(execution)
    encoded = checkpoint.model_dump_json()
    assert "response_json" not in encoded and "retained_bytes" in encoded
    assert len(encoded) < 1024
    original = timeout_checkpoint(execution).observations
    assert resolve_checkpoint(checkpoint, execution) == original
    validate_raw_files(checkpoint, execution, datetime.now(UTC))
    assert artifact_checkpoint(execution) == checkpoint
    other = execution.model_copy(update={"access_scope": "TEST_ONLY-other-session"})
    with pytest.raises(ValueError, match="PAIR_EXECUTION|EXECUTION_MISMATCH"):
        read_route_artifact(checkpoint.observations[0], other)
    with pytest.raises(ValueError, match="REQUIRE_RESOLUTION"):
        checkpoint.validate_inventory(execution)


@pytest.mark.parametrize(
    "attack",
    [
        "missing",
        "mutation",
        "truncated",
        "symlink",
        "fifo",
        "wrong_size",
        "wrong_pair",
        "wrong_closure",
    ],
)
def test_changed_artifact_never_resumes_or_validates(compact_context, attack):
    execution = compact_context[1].batch_execution
    checkpoint = artifact_checkpoint(execution)
    reference = checkpoint.observations[0]
    path = observation_directory() / "route-results" / (reference.artifact_sha256 + ".json")
    if attack == "missing":
        path.unlink()
    elif attack in {"mutation", "truncated"}:
        path.chmod(0o600)
        content = path.read_bytes()
        path.write_bytes(content[:-1] if attack == "truncated" else b"x" + content[1:])
    elif attack == "symlink":
        original = path.read_bytes()
        path.unlink()
        target = path.parent / "TEST_ONLY-untrusted.json"
        target.write_bytes(original)
        path.symlink_to(target)
    elif attack == "fifo":
        import os

        path.unlink()
        os.mkfifo(path)
    elif attack == "wrong_size":
        checkpoint = checkpoint.model_copy(
            update={
                "observations": [
                    reference.model_copy(update={"size_bytes": reference.size_bytes + 1})
                ]
            }
        )
    elif attack == "wrong_pair":
        checkpoint = checkpoint.model_copy(
            update={"observations": [reference.model_copy(update={"pair_id": "e" * 64})]}
        )
    else:
        checkpoint = checkpoint.model_copy(
            update={
                "observations": [
                    reference.model_copy(update={"retained_bytes": reference.retained_bytes + 1})
                ]
            }
        )
    with pytest.raises((ValueError, OSError)):
        validate_raw_files(checkpoint, execution, datetime.now(UTC))


def test_route_store_does_not_follow_directory_symlink(compact_context, tmp_path):
    folder = observation_directory() / "route-results"
    outside = tmp_path / "TEST_ONLY-outside"
    outside.mkdir()
    folder.symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        artifact_checkpoint(compact_context[1].batch_execution)
    assert not list(outside.iterdir())


def test_artifact_budget_and_legacy_protocol_cannot_be_bypassed(compact_context):
    execution = compact_context[1].batch_execution
    observation = timeout_checkpoint(execution).observations[0]
    with pytest.raises(ValueError, match="BYTE_BUDGET"):
        write_route_artifact(
            observation, execution.model_copy(update={"max_route_artifact_bytes": 1})
        )
    with pytest.raises(ValueError, match="PROTOCOL_REQUIRED"):
        write_route_artifact(observation, execution.model_copy(update={"checkpoint_format": None}))
    with pytest.raises(ValueError, match="BYTE_BUDGET"):
        write_route_artifact(observation, execution, remaining_bytes=1)
    folder = observation_directory() / "route-results"
    assert not folder.exists() or not list(folder.iterdir())


def test_artifact_window_validation_has_a_deadline(compact_context, monkeypatch):
    from a2_api.core import walk_route_artifacts

    execution = compact_context[1].batch_execution
    checkpoint = artifact_checkpoint(execution)
    ticks = iter((0, 16))
    monkeypatch.setattr(walk_route_artifacts, "monotonic", lambda: next(ticks))
    with pytest.raises(ValueError, match="DEADLINE_EXCEEDED"):
        resolve_checkpoint(checkpoint, execution)


@pytest.mark.asyncio
async def test_compact_progress_is_immutable_scoped_and_resumable(database, compact_context):
    pool, _, _ = database
    repo = JobRepository(pool)
    progress = CheckpointRepository(repo)
    first = await enqueue(repo, compact_context)
    assert await progress.begin(first) == []
    checkpoint = artifact_checkpoint(first.context.batch_execution)
    await progress.put(first, checkpoint)
    await progress.put(first, checkpoint)
    assert await pool.fetchval("SELECT count(*) FROM od_checkpoints") == 1
    await repo.fail(
        first,
        ErrorDetail(
            code="engine_unavailable",
            message="TEST_ONLY_INTERRUPTED",
            trace_id=first.job.trace_id,
            field_errors=[],
            retryable=True,
        ),
    )
    second = await enqueue(repo, compact_context)
    assert await progress.begin(second) == [checkpoint]
    await progress.finalize(second)
    other = await enqueue(repo, compact_context, scope="TEST_ONLY-other-session")
    assert await progress.begin(other) == []
    with pytest.raises((DomainError, ValueError)):
        await progress.put(other, checkpoint)


@pytest.mark.asyncio
async def test_missing_artifact_cannot_be_reused_from_checkpoint_cache(database, compact_context):
    pool, _, _ = database
    repo = JobRepository(pool)
    progress = CheckpointRepository(repo)
    first = await enqueue(repo, compact_context)
    await progress.begin(first)
    checkpoint = artifact_checkpoint(first.context.batch_execution)
    await progress.put(first, checkpoint)
    await repo.fail(
        first,
        ErrorDetail(
            code="engine_unavailable",
            message="TEST_ONLY_INTERRUPTED",
            trace_id=first.job.trace_id,
            field_errors=[],
            retryable=True,
        ),
    )
    reference = checkpoint.observations[0]
    (observation_directory() / "route-results" / (reference.artifact_sha256 + ".json")).unlink()
    second = await enqueue(repo, compact_context)
    with pytest.raises((ValueError, OSError)):
        await progress.begin(second)
    assert (await repo.get_job(second.job.job_id, scope=second.context.access_scope)).run_id is None


@pytest.mark.asyncio
async def test_actual_child_loss_and_explicit_retry_resume_compact_progress(
    database, compact_context
):
    from uuid import uuid4

    from a2_api.workers.runner import Worker

    pool, _, _ = database
    repo = JobRepository(pool)
    request, context = compact_context
    first = await repo.enqueue(
        "analysis",
        request,
        context,
        "TEST_ONLY-" + uuid4().hex,
        "TEST_ONLY",
        scope=context.access_scope,
    )
    worker = Worker(
        repo,
        code_version=context.code_version,
        handler="compact_batch_handlers:hang_after_commit",
        timeout_seconds=2,
    )
    assert await worker.run_once()
    stopped = await repo.get_job(first.job_id, scope=context.access_scope)
    assert stopped.status == "failed" and stopped.run_id is None
    assert await pool.fetchval("SELECT count(*) FROM od_checkpoints") == 1
    resumed_context = context.model_copy(update={"analysis_id": "TEST_ONLY-" + uuid4().hex})
    second = await repo.enqueue(
        "analysis",
        request,
        resumed_context,
        "TEST_ONLY-" + uuid4().hex,
        "TEST_ONLY",
        scope=context.access_scope,
    )
    worker = Worker(
        repo,
        code_version=context.code_version,
        handler="compact_batch_handlers:success",
        timeout_seconds=10,
    )
    assert await worker.run_once()
    completed = await repo.get_job(second.job_id, scope=context.access_scope)
    assert completed.status == "succeeded" and completed.run_id is not None
    assert await pool.fetchval("SELECT count(*) FROM runs") == 1
    assert await pool.fetchval("SELECT attempts FROM od_executions") == 2
    assert await pool.fetchval("SELECT count(*) FROM od_checkpoints") == 1


@pytest.mark.asyncio
async def test_mutation_after_finalize_rolls_back_atomic_publication(database, compact_context):
    from batch_handlers import result

    pool, _, _ = database
    repo = JobRepository(pool)
    progress = CheckpointRepository(repo)
    claim = await enqueue(repo, compact_context)
    await progress.begin(claim)
    checkpoint = artifact_checkpoint(claim.context.batch_execution)
    await progress.put(claim, checkpoint)
    await progress.finalize(claim)
    reference = checkpoint.observations[0]
    path = observation_directory() / "route-results" / (reference.artifact_sha256 + ".json")
    path.chmod(0o600)
    content = path.read_bytes()
    path.write_bytes(b"x" + content[1:])
    with pytest.raises(ValueError, match="CONTENT_HASH"):
        await repo.succeed(claim, result(claim))
    assert await pool.fetchval("SELECT count(*) FROM runs") == 0
    assert (await repo.get_job(claim.job.job_id, scope=claim.context.access_scope)).run_id is None
