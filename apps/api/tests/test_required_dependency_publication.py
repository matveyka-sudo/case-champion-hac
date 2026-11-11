"""Real spawned worker -> PostgreSQL -> HTTP/cache rejection of inconsistent indices."""

from hashlib import sha256

import pytest
from a2_api.api.application import create_app
from a2_api.db.repositories.jobs import JobRepository
from a2_api.services.analyses import AnalysisService
from a2_api.workers.runner import Worker
from httpx import ASGITransport, AsyncClient
from test_v2_storage import context, request, result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler", ["partial_reference", "unknown_reference", "partial_journey", "partial_population"]
)
async def test_incomplete_index_dependencies_cannot_publish_or_cache(database, handler):
    pool, _, _ = database
    repository = JobRepository(pool)
    token = "A" * 43
    scope = sha256(token.encode()).hexdigest()
    # A previously accepted independent baseline survives every rejected calculation.
    await repository.enqueue(
        "analysis", request(), context(scope=scope), "baseline", "trace", scope=scope
    )
    claim = await repository.claim("baseline-token")
    await repository.succeed(claim, result())
    baseline = await repository.get_run("synthetic-analysis", scope=scope)
    invalid_id = "invalid-index"
    accepted = await repository.enqueue(
        "analysis",
        request("intercity_passenger"),
        context("intercity_passenger", analysis_id=invalid_id, scope=scope),
        "invalid",
        "trace",
        scope=scope,
    )
    await Worker(
        repository,
        handler="dependency_publication_handlers:" + handler,
        code_version=context().code_version,
        timeout_seconds=5,
    ).run_once()
    job = await repository.get_job(accepted.job_id, scope=scope)
    assert job.status == "failed"
    assert job.run_id is None
    assert await pool.fetchval("SELECT count(*) FROM runs") == 1
    retried = await repository.enqueue(
        "analysis",
        request("intercity_passenger"),
        context("intercity_passenger", analysis_id="retry", scope=scope),
        "retry",
        "trace",
        scope=scope,
    )
    assert retried.status == "queued"
    assert (
        await repository.get_run("synthetic-analysis", scope=scope)
    ).result_hash == baseline.result_hash
    async with AsyncClient(
        transport=ASGITransport(app=create_app(AnalysisService(repository=repository))),
        base_url="http://test",
        cookies={"a2_session": token},
    ) as api:
        response = await api.get("/api/v1/analyses/" + invalid_id)
        assert response.status_code == 404
        assert "Traceback" not in response.text
        saved = await api.get("/api/v1/analyses/synthetic-analysis")
        assert saved.status_code == 200
        assert saved.json()["result_hash"] == baseline.result_hash


@pytest.mark.asyncio
async def test_complete_dependency_chain_survives_spawn_publication_http_and_cache(database):
    pool, _, _ = database
    repository = JobRepository(pool)
    token = "A" * 43
    scope = sha256(token.encode()).hexdigest()
    accepted = await repository.enqueue(
        "analysis",
        request("intercity_passenger"),
        context("intercity_passenger", analysis_id="full-control", scope=scope),
        "control",
        "trace",
        scope=scope,
    )
    await Worker(
        repository,
        handler="dependency_publication_handlers:result",
        code_version=context().code_version,
        timeout_seconds=5,
    ).run_once()
    job = await repository.get_job(accepted.job_id, scope=scope)
    assert job.status == "succeeded"
    cached = await repository.enqueue(
        "analysis",
        request("intercity_passenger"),
        context("intercity_passenger", analysis_id="cached", scope=scope),
        "cached",
        "trace",
        scope=scope,
    )
    assert cached.status == "succeeded"
    assert (await repository.get_job(cached.job_id, scope=scope)).run_id == "full-control"
    async with AsyncClient(
        transport=ASGITransport(app=create_app(AnalysisService(repository=repository))),
        base_url="http://test",
        cookies={"a2_session": token},
    ) as api:
        response = await api.get("/api/v1/analyses/full-control")
        assert response.status_code == 200
        assert response.json()["scores"][-1]["ranking_eligible"] is True
