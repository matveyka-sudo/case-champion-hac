import asyncio
import json
from pathlib import Path

import asyncpg
import pytest
from a2_api.jobs import JobContext
from a2_api.services import DomainError
from a2_api.storage import JobRepository
from a2_contracts.api import AnalysisRequest, ErrorDetail
from a2_contracts.results import ProfileResult


def request():
    return AnalysisRequest(
        bundle_id="demo-synthetic-bundle",
        profile_id="regular",
        profile_version=1,
        task_set_hash="demo-task-hash",
        origin_ids=["demo-city-1"],
        include_case_results=False,
    )


def context(code="demo-code"):
    return JobContext(
        bundle_id="demo-synthetic-bundle",
        bundle_hash="demo-bundle-hash",
        code_version=code,
        profile_hashes={"regular": "demo-profile-hash"},
        profile_versions={"regular": 1},
        origin_ids={"regular": ["demo-city-1"]},
        task_hashes={"regular": "demo-task-hash"},
        baseline_run_ids={},
        task_count=2,
    )


def result(run_id="demo-run", code="demo-code"):
    path = Path(__file__).resolve().parents[3] / "packages/contracts/fixtures/success.json"
    payload = json.loads(path.read_text())["payload"]
    payload.update(run_id=run_id, code_version=code)
    payload["cities"] = payload["cities"][:1]
    payload["provenance"]["code_version"] = code
    return ProfileResult.model_validate(payload)


@pytest.mark.asyncio
async def test_concurrent_idempotency_creates_exactly_one_job(database):
    pool, _, _ = database
    repo = JobRepository(pool)
    jobs = await asyncio.gather(
        *[
            repo.enqueue("analysis", request(), context(), "same-key", "demo-trace")
            for _ in range(20)
        ]
    )
    assert len({job.job_id for job in jobs}) == 1
    assert await pool.fetchval("SELECT count(*) FROM jobs") == 1
    assert all(job.status == "queued" for job in jobs)


@pytest.mark.asyncio
async def test_key_reuse_with_changed_payload_is_409(database):
    pool, _, _ = database
    repo = JobRepository(pool)
    first = await repo.enqueue("analysis", request(), context(), "key", "trace")
    changed = request().model_copy(update={"include_case_results": True})
    with pytest.raises(DomainError) as exc:
        await repo.enqueue("analysis", changed, context(), "key", "trace")
    assert (exc.value.status_code, exc.value.code) == (409, "idempotency_conflict")
    assert (await repo.replay("analysis", request(), "key")).job_id == first.job_id


@pytest.mark.asyncio
async def test_claim_once_and_atomic_run_publication(database):
    pool, _, _ = database
    repo = JobRepository(pool)
    job = await repo.enqueue("analysis", request(), context(), "key", "trace")
    claim = await repo.claim("worker-token")
    assert claim.job.job_id == job.job_id
    assert await repo.claim("other-worker-token") is None
    assert (await repo.get_job(job.job_id)).status == "running"
    await repo.succeed(claim, result())
    ready = await repo.get_job(job.job_id)
    assert ready.status == "succeeded" and ready.run_id == "demo-run"
    assert (await repo.get_run("demo-run")).unit == "points_0_100"
    assert (await repo.replay("analysis", request(), "key")).status == "succeeded"


@pytest.mark.asyncio
async def test_cache_includes_all_context_versions(database):
    pool, _, _ = database
    repo = JobRepository(pool)
    await repo.enqueue("analysis", request(), context(), "first", "trace")
    claim = await repo.claim("token")
    await repo.succeed(claim, result())
    reused = await repo.enqueue("analysis", request(), context(), "second", "trace")
    assert reused.status == "succeeded"
    assert (await repo.get_job(reused.job_id)).run_id == "demo-run"
    for field, value in [
        ("code_version", "new-code"),
        ("bundle_hash", "new-bundle"),
        ("profile_hashes", {"regular": "new-profile"}),
        ("task_hashes", {"regular": "new-tasks"}),
    ]:
        changed = context().model_copy(update={field: value})
        job = await repo.enqueue("analysis", request(), changed, field, "trace")
        assert job.status == "queued"


@pytest.mark.asyncio
async def test_run_ids_are_immutable_and_failed_publication_rolls_back(database):
    pool, _, _ = database
    repo = JobRepository(pool)
    await repo.enqueue("analysis", request(), context(), "a", "trace")
    first = await repo.claim("token")
    await repo.succeed(first, result())
    await repo.enqueue("analysis", request(), context("new-code"), "b", "trace")
    second = await repo.claim("token")
    with pytest.raises(DomainError):
        await repo.succeed(second, result(code="new-code"))
    assert (await repo.get_job(second.job.job_id)).status == "running"
    assert (await repo.get_run("demo-run")).code_version == "demo-code"
    assert await pool.fetchval("SELECT count(*) FROM runs") == 1


@pytest.mark.asyncio
async def test_stale_claim_cannot_publish_or_fail_someone_elses_job(database):
    pool, _, _ = database
    repo = JobRepository(pool)
    await repo.enqueue("analysis", request(), context(), "a", "trace")
    claim = await repo.claim("token")
    await repo.recover_orphans()
    with pytest.raises(DomainError):
        await repo.succeed(claim, result())
    assert await pool.fetchval("SELECT count(*) FROM runs") == 0
    assert (await repo.get_job(claim.job.job_id)).status == "failed"


@pytest.mark.asyncio
async def test_job_failure_is_persistent_and_unknown_ids_are_404(database):
    pool, _, _ = database
    repo = JobRepository(pool)
    await repo.enqueue("analysis", request(), context(), "a", "trace")
    claim = await repo.claim("token")
    await repo.fail(
        claim,
        ErrorDetail(
            code="job_timeout",
            message="Calculation timed out",
            field_errors=[],
            retryable=False,
            trace_id="trace",
        ),
    )
    assert (await repo.get_job(claim.job.job_id)).error.code == "job_timeout"
    for getter in (repo.get_job, repo.get_run):
        with pytest.raises(DomainError) as exc:
            await getter("missing")
        assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_database_rejects_impossible_job_transition(database):
    pool, _, _ = database
    repo = JobRepository(pool)
    job = await repo.enqueue("analysis", request(), context(), "a", "trace")
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute("UPDATE jobs SET status='succeeded' WHERE job_id=$1", job.job_id)
