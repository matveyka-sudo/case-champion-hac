"""TEST_ONLY scoped read-only progress. No observed route, provider, or source I/O."""

import asyncio
import hashlib
from pathlib import Path

import httpx
import pytest
from a2_api.api.application import create_app
from a2_api.core.batches import BatchCheckpoint, BatchExecution, PairObservation, PlannedPair
from a2_api.db.repositories.checkpoints import CheckpointRepository
from a2_api.db.repositories.job_progress import JOB_PROGRESS_SQL, job_with_progress
from a2_api.db.repositories.jobs import JobRepository
from a2_api.services.analyses import AnalysisService
from a2_contracts.api import ErrorDetail
from a2_contracts.common import canonical_hash
from a2_engine import WalkResponseEnvelope
from a2_engine.urban.valhalla.requests import make_route_request
from pydantic import TypeAdapter
from test_od_checkpoints import batch_context as batch_context
from test_od_checkpoints import enqueue
from test_walk_pilot import configured as configured


def planned_context(batch_context, count, batch_size):
    request, context = batch_context
    execution = context.batch_execution
    prototype = execution.pairs[0].request
    adapter = TypeAdapter(type(prototype))
    pairs = []
    for i in range(count):
        route = make_route_request(
            "TEST_ONLY-pair-" + str(i),
            prototype.destination_id,
            prototype.build,
            prototype.search_parameters_id,
            prototype.origin,
            prototype.destination,
        )
        pairs.append(
            PlannedPair(
                pair_id=canonical_hash({"request": adapter.dump_python(route, mode="json")}),
                request=route,
            )
        )
    execution = BatchExecution.model_validate(
        execution.model_dump(mode="python")
        | {
            "pairs": sorted(pairs, key=lambda p: p.pair_id),
            "batch_size": batch_size,
            "approved_pair_count": count,
            "unresolved_pair_count": 0,
            "max_provider_calls": 100000,
            "max_checkpoint_bytes": 16 * 1024 * 1024,
        }
    )
    return request, context.model_copy(update={"batch_execution": execution})


def checkpoint_for(execution, index=0):
    batch_id, pairs = execution.batches[index]
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


async def admitted(database, batch_context, count=2, batch_size=1, scope=None):
    pool, _, _ = database
    repo = JobRepository(pool)
    claim = await enqueue(repo, planned_context(batch_context, count, batch_size), scope=scope)
    await CheckpointRepository(repo).begin(claim)
    return repo, claim


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "count,batch_size", [(33, size) for size in range(1, 17)] + [(1401, 16), (4096, 1), (4096, 16)]
)
async def test_sql_batch_roster_matches_actual_canonical_execution_property(
    database, batch_context, count, batch_size
):
    repo, claim = await admitted(database, batch_context, count, batch_size)
    # The production CTE itself is exercised; no separate test hash implementation.
    prefix = JOB_PROGRESS_SQL.rsplit("\nSELECT j.job_id", 1)[0]
    rows = await repo.pool.fetch(
        prefix + "\nSELECT batch_id,pair_ids FROM planned",
        claim.job.job_id,
        claim.context.access_scope,
    )
    actual = {row["batch_id"]: row["pair_ids"] for row in rows}
    expected = {
        batch_id: [pair.pair_id for pair in pairs]
        for batch_id, pairs in claim.context.batch_execution.batches
    }
    assert actual == expected


@pytest.mark.asyncio
async def test_persisted_unknown_batches_are_progress_without_source_reads(
    database, batch_context, monkeypatch
):
    repo, claim = await admitted(database, batch_context, 17, 16)
    progress = CheckpointRepository(repo)
    await progress.put(claim, checkpoint_for(claim.context.batch_execution))

    def forbidden(*args, **kwargs):
        raise AssertionError("Progress must not read source files")

    with monkeypatch.context() as guard:
        guard.setattr(Path, "open", forbidden)
        job, counts = await job_with_progress(repo, claim.job.job_id, claim.context.access_scope)
        assert job.status == "running" and counts.model_dump() == {
            "completed_batches": 1,
            "total_batches": 2,
            "phase": "routing",
        }
    await progress.put(claim, checkpoint_for(claim.context.batch_execution, 1))
    _, counts = await job_with_progress(repo, claim.job.job_id, claim.context.access_scope)
    assert counts.completed_batches == counts.total_batches == 2 and counts.phase == "finalizing"
    assert (await repo.get_job(claim.job.job_id, claim.context.access_scope)).run_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        "scope",
        "claim",
        "attempt",
        "execution",
        "context",
        "batch_id",
        "payload_batch",
        "payload_execution",
        "schema",
        "pair",
        "pair_order",
        "observations",
        "unplanned",
        "source_claim",
        "plan_bound",
        "plan_pair",
    ],
)
async def test_invalid_scoped_binding_or_checkpoint_inventory_omits_progress(
    database, batch_context, mutation
):
    repo, claim = await admitted(database, batch_context, 3, 2)
    await CheckpointRepository(repo).put(claim, checkpoint_for(claim.context.batch_execution))
    queries = {
        "scope": "UPDATE od_attempts SET access_scope='foreign'",
        "claim": "UPDATE od_attempts SET claim_token='foreign'",
        "attempt": "DELETE FROM od_attempts",
        "execution": "UPDATE od_executions SET payload=jsonb_set(payload,'{snapshot_id}',to_jsonb('foreign'::text))",
        "context": "UPDATE jobs SET payload=jsonb_set(payload,'{context,batch_execution,snapshot_id}',to_jsonb('foreign'::text))",
        "batch_id": "UPDATE od_checkpoints SET batch_id=repeat('f',64)",
        "payload_batch": "UPDATE od_checkpoints SET payload=jsonb_set(payload,'{batch_id}',to_jsonb(repeat('f',64)))",
        "payload_execution": "UPDATE od_checkpoints SET payload=jsonb_set(payload,'{execution_identity}',to_jsonb(repeat('f',64)))",
        "schema": "UPDATE od_checkpoints SET payload=jsonb_set(payload,'{schema_version}',to_jsonb('foreign'::text))",
        "pair": "UPDATE od_checkpoints SET payload=jsonb_set(payload,'{observations,0,pair_id}',to_jsonb(repeat('f',64)))",
        "pair_order": "UPDATE od_checkpoints SET payload=jsonb_set(payload,'{observations}',jsonb_build_array(payload#>'{observations,1}',payload#>'{observations,0}'))",
        "observations": "UPDATE od_checkpoints SET payload=jsonb_set(payload,'{observations}','{}'::jsonb)",
        "unplanned": "UPDATE od_checkpoints SET batch_id=repeat('f',64),payload=jsonb_set(payload,'{batch_id}',to_jsonb(repeat('f',64)))",
        "source_claim": "UPDATE od_checkpoints SET source_claim_token='foreign'",
    }
    if mutation == "scope":
        # Keep FK-valid foreign execution, never broaden the actual job scope.
        await repo.pool.execute(
            "INSERT INTO od_executions SELECT 'foreign',execution_identity,payload,attempts,reserved_calls,created_at FROM od_executions"
        )
    if mutation.startswith("plan_"):
        path = "{batch_size}" if mutation == "plan_bound" else "{pairs,0,pair_id}"
        value = "0" if mutation == "plan_bound" else '"foreign"'
        await repo.pool.execute(
            "UPDATE od_executions SET payload=jsonb_set(payload,$1::text[],$2::jsonb)",
            path.strip("{}").split(","),
            value,
        )
        await repo.pool.execute(
            "UPDATE jobs SET payload=jsonb_set(payload,'{context,batch_execution}',(SELECT payload FROM od_executions LIMIT 1))"
        )
    else:
        await repo.pool.execute(queries[mutation])
    job, counts = await job_with_progress(repo, claim.job.job_id, claim.context.access_scope)
    assert job.status == "running" and counts is None


@pytest.mark.asyncio
async def test_failed_job_progress_is_frozen_while_new_job_resumes_earlier_checkpoints(
    database, batch_context
):
    repo, first = await admitted(database, batch_context)
    progress = CheckpointRepository(repo)
    execution = first.context.batch_execution
    await progress.put(first, checkpoint_for(execution))
    await repo.fail(
        first,
        ErrorDetail(
            code="engine_unavailable",
            message="TEST_ONLY",
            field_errors=[],
            retryable=True,
            trace_id=first.job.trace_id,
        ),
    )
    second = await enqueue(repo, (first.request, first.context))
    assert await progress.begin(second) == [checkpoint_for(execution)]
    _, counts = await job_with_progress(repo, second.job.job_id, second.context.access_scope)
    assert counts.completed_batches == 1
    await progress.put(second, checkpoint_for(execution, 1))
    _, old = await job_with_progress(repo, first.job.job_id, first.context.access_scope)
    _, new = await job_with_progress(repo, second.job.job_id, second.context.access_scope)
    assert old.completed_batches == 1 and old.phase == "stopped"
    assert new.completed_batches == 2 and new.phase == "finalizing"


@pytest.mark.asyncio
async def test_progress_read_does_not_block_cancel_or_heartbeat(database, batch_context):
    repo, claim = await admitted(database, batch_context)
    reads = [
        job_with_progress(repo, claim.job.job_id, claim.context.access_scope) for _ in range(5)
    ]
    results = await asyncio.wait_for(asyncio.gather(*reads, repo.heartbeat(claim)), 2)
    assert results[-1] is True
    cancelled = await asyncio.wait_for(repo.cancel(claim.job.job_id, claim.context.access_scope), 1)
    _, counts = await job_with_progress(repo, claim.job.job_id, claim.context.access_scope)
    assert cancelled.status == "cancelled" and counts.phase == "stopped"


@pytest.mark.asyncio
async def test_http_progress_is_opt_in_and_old_default_and_cancel_responses_omit_it(
    database, batch_context, monkeypatch
):
    token = "a" * 43
    scope = hashlib.sha256(token.encode()).hexdigest()
    repo, claim = await admitted(database, batch_context, scope=scope)
    service = AnalysisService(repository=repo)
    app = create_app(service)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        cookies={"a2_session": token},
    ) as client:
        for suffix in ["", "?include_progress=false"]:
            response = await client.get("/api/v1/jobs/" + claim.job.job_id + suffix)
            assert response.status_code == 200 and "progress" not in response.json()
        response = await client.get("/api/v1/jobs/" + claim.job.job_id + "?include_progress=true")
        assert response.status_code == 200 and response.json()["progress"] == {
            "completed_batches": 0,
            "total_batches": 2,
            "phase": "routing",
        }
        response = await client.post(
            "/api/v1/jobs/" + claim.job.job_id + "/cancel?include_progress=true"
        )
        assert (
            response.status_code == 200
            and response.json()["status"] == "cancelled"
            and "progress" not in response.json()
        )
        client.cookies.clear()
        client.cookies.set("a2_session", "b" * 43)
        foreign = await client.get("/api/v1/jobs/" + claim.job.job_id + "?include_progress=true")
        missing = await client.get("/api/v1/jobs/TEST_ONLY-missing?include_progress=true")
        assert foreign.status_code == missing.status_code == 404
        assert foreign.json()["error"]["message"] == missing.json()["error"]["message"]


@pytest.mark.asyncio
async def test_published_progress_requires_atomic_run_and_cache_hit_omits_counts(
    database, batch_context
):
    from batch_handlers import result

    repo, claim = await admitted(database, batch_context)
    progress = CheckpointRepository(repo)
    for index in range(len(claim.context.batch_execution.batches)):
        await progress.put(claim, checkpoint_for(claim.context.batch_execution, index))
    _, pending = await job_with_progress(repo, claim.job.job_id, claim.context.access_scope)
    assert (
        pending.phase == "finalizing" and await repo.pool.fetchval("SELECT count(*) FROM runs") == 0
    )
    await repo.succeed(claim, result(claim))
    view = await AnalysisService(repository=repo).job(
        claim.job.job_id, claim.context.access_scope, include_progress=True
    )
    assert view.status == "succeeded" and view.progress.phase == "published"
    cached = await repo.enqueue(
        "analysis",
        claim.request,
        claim.context,
        "TEST_ONLY-cache-progress",
        "TEST_ONLY",
        scope=claim.context.access_scope,
    )
    assert cached.status == "succeeded"
    cached_view = await AnalysisService(repository=repo).job(
        cached.job_id, claim.context.access_scope, include_progress=True
    )
    assert cached_view.status == "succeeded" and "progress" not in cached_view.model_dump(
        mode="json"
    )


@pytest.mark.asyncio
async def test_queued_and_not_admitted_running_job_do_not_claim_saved_progress(
    database, batch_context
):
    repo = JobRepository(database[0])
    request, context = planned_context(batch_context, 2, 1)
    accepted = await repo.enqueue(
        "analysis", request, context, "TEST_ONLY-not-begun", "TEST_ONLY", scope=context.access_scope
    )
    queued, counts = await job_with_progress(repo, accepted.job_id, context.access_scope)
    assert queued.status == "queued" and counts is None
    claim = await repo.claim("TEST_ONLY-not-begun")
    running, counts = await job_with_progress(repo, claim.job.job_id, context.access_scope)
    assert running.status == "running" and counts is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["nonbatch", "scenario"])
async def test_other_job_kinds_cannot_borrow_batch_counts(database, batch_context, kind):
    repo, claim = await admitted(database, batch_context)
    if kind == "nonbatch":
        await repo.pool.execute("UPDATE jobs SET payload=payload#-'{context,batch_execution}'")
    else:
        await repo.pool.execute("UPDATE jobs SET kind='scenario'")
    job, counts = await job_with_progress(repo, claim.job.job_id, claim.context.access_scope)
    assert job.status == "running" and counts is None
