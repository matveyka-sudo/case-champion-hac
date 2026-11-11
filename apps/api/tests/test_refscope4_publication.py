"""Real spawned calculation -> PostgreSQL -> scoped API/cache reference checks."""

from hashlib import sha256

import pytest
from a2_api.api.application import create_app
from a2_api.core.jobs import V2JobContext
from a2_api.db.repositories.jobs import JobRepository
from a2_api.services.analyses import AnalysisService
from a2_api.workers.runner import Worker
from a2_contracts.v2.requests import parse_analysis_request
from httpx import ASGITransport, AsyncClient
from refscope4_handlers import raw_case
from test_v2_storage import context, request, result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    [
        "fixed_control",
        "journey_control",
        "city_ids_control",
        "fixed_broad_control",
        "singleton_journey_control",
        "foreign",
        "reverse",
        "superset",
        "partial",
        "missing_binding",
        "foreign_unfixed",
        "fixed_foreign_journey",
        "narrower",
        "unlinked_multi_scope",
        "missing_path",
    ],
)
async def test_refscope4_spawned_publication_api_cache(database, mode):
    pool, _, _ = database
    repository = JobRepository(pool)
    token = "A" * 43
    owner = sha256(token.encode()).hexdigest()
    await repository.enqueue(
        "analysis", request(), context(scope=owner), "baseline", "trace", scope=owner
    )
    await repository.succeed(await repository.claim("baseline-token"), result())
    baseline = await repository.get_run("synthetic-analysis", scope=owner)
    raw = raw_case(mode)
    normalized = parse_analysis_request(raw["request"])
    candidate_context = V2JobContext.model_validate(
        dict(
            schema_version="2",
            snapshot_id=raw["snapshot_id"],
            snapshot_hash=mode,
            versions=raw["versions"],
            analysis_id="candidate",
            normalized_request=normalized,
            access_scope=owner,
        )
    )
    submitted_request = normalized.model_copy(update={"snapshot_id": None, "methodology": None})
    accepted = await repository.enqueue(
        "analysis", submitted_request, candidate_context, "candidate", "trace", scope=owner
    )
    await Worker(
        repository,
        handler="refscope4_handlers:calculate",
        code_version=context().code_version,
        timeout_seconds=5,
    ).run_once()
    job = await repository.get_job(accepted.job_id, scope=owner)
    retried = await repository.enqueue(
        "analysis",
        submitted_request,
        candidate_context.model_copy(update={"analysis_id": "retry"}),
        "retry",
        "trace",
        scope=owner,
    )
    good = mode.endswith("control")
    async with AsyncClient(
        transport=ASGITransport(app=create_app(AnalysisService(repository=repository))),
        base_url="http://test",
        cookies={"a2_session": token},
    ) as api:
        response = await api.get("/api/v1/analyses/candidate")
    unchanged = (
        await repository.get_run("synthetic-analysis", scope=owner)
    ).model_dump() == baseline.model_dump()
    count = await pool.fetchval("SELECT count(*) FROM runs")
    observations = dict(
        job=job.status,
        published_runs=count,
        api=response.status_code,
        cache=retried.status,
        baseline_unchanged=unchanged,
    )
    assert job.status == ("succeeded" if good else "failed"), observations
    assert count == 1 + good
    assert response.status_code == (200 if good else 404)
    assert retried.status == ("succeeded" if good else "queued")
    assert unchanged
