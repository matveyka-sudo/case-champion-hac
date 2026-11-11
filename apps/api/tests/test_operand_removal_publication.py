"""Required operands cannot disappear between calculation and publication."""

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
    "kind,handler",
    [
        ("intercity_passenger", "control"),
        ("urban_passenger", "control"),
        ("intercity_passenger", "stripped_reference"),
        ("intercity_passenger", "stripped_journey"),
        ("intercity_passenger", "missing_reference"),
        ("urban_passenger", "missing_basis"),
        ("urban_passenger", "wrong_basis"),
    ],
)
async def test_missing_required_operands_spawn_publication_api_cache(database, kind, handler):
    pool, _, _ = database
    repository = JobRepository(pool)
    token = "A" * 43
    owner = sha256(token.encode()).hexdigest()
    await repository.enqueue(
        "analysis", request(), context(scope=owner), "baseline", "trace", scope=owner
    )
    await repository.succeed(await repository.claim("baseline-token"), result())
    baseline = await repository.get_run("synthetic-analysis", scope=owner)
    accepted = await repository.enqueue(
        "analysis",
        request(kind),
        context(kind, analysis_id="candidate", scope=owner, snapshot_hash="operand-test-input"),
        "candidate",
        "trace",
        scope=owner,
    )
    await Worker(
        repository,
        handler="operand_removal_handlers:" + handler,
        code_version=context().code_version,
        timeout_seconds=5,
    ).run_once()
    job = await repository.get_job(accepted.job_id, scope=owner)
    good = handler == "control"
    run_count = await pool.fetchval("SELECT count(*) FROM runs")
    retried = await repository.enqueue(
        "analysis",
        request(kind),
        context(kind, analysis_id="retry", scope=owner, snapshot_hash="operand-test-input"),
        "retry",
        "trace",
        scope=owner,
    )
    unchanged = (
        await repository.get_run("synthetic-analysis", scope=owner)
    ).model_dump() == baseline.model_dump()
    async with AsyncClient(
        transport=ASGITransport(app=create_app(AnalysisService(repository=repository))),
        base_url="http://test",
        cookies={"a2_session": token},
    ) as client:
        response = await client.get("/api/v1/analyses/candidate")
        observations = dict(
            job=job.status,
            run_count=run_count,
            api=response.status_code,
            cache=retried.status,
            baseline_unchanged=unchanged,
        )
        assert job.status == ("succeeded" if good else "failed"), observations
        assert (job.run_id is not None) == good
        assert run_count == 1 + good
        assert retried.status == ("succeeded" if good else "queued")
        assert unchanged
        assert response.status_code == (200 if good else 404)
        assert "Traceback" not in response.text


@pytest.mark.asyncio
async def test_historical_full_intercity_without_semantics_is_refused_without_rewriting(database):
    import json

    from a2_api.services import DomainError

    pool, _, _ = database
    repository = JobRepository(pool)
    token = "A" * 43
    owner = sha256(token.encode()).hexdigest()
    accepted = await repository.enqueue(
        "analysis",
        request("intercity_passenger"),
        context("intercity_passenger", analysis_id="historic", scope=owner),
        "historic",
        "trace",
        scope=owner,
    )
    await Worker(
        repository,
        handler="operand_removal_handlers:control",
        code_version=context().code_version,
        timeout_seconds=5,
    ).run_once()
    assert (await repository.get_job(accepted.job_id, scope=owner)).status == "succeeded"
    row = await pool.fetchrow("SELECT payload,result_hash FROM runs WHERE run_id='historic'")
    payload = json.loads(row["payload"])
    for score in payload["scores"]:
        score.pop("calculation_kind", None)
    # This dedicated test schema simulates the previous wire format, not a production migration.
    await pool.execute(
        "UPDATE runs SET payload=$1::jsonb WHERE run_id='historic'", json.dumps(payload)
    )
    before = await pool.fetchrow("SELECT payload,result_hash FROM runs WHERE run_id='historic'")
    async with AsyncClient(
        transport=ASGITransport(app=create_app(AnalysisService(repository=repository))),
        base_url="http://test",
        cookies={"a2_session": token},
    ) as client:
        response = await client.get("/api/v1/analyses/historic")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "stored_result_incompatible"
        assert "ValidationError" not in response.text
    with pytest.raises(DomainError) as rejected:
        await repository.enqueue(
            "analysis",
            request("intercity_passenger"),
            context("intercity_passenger", analysis_id="retry", scope=owner),
            "retry",
            "trace",
            scope=owner,
        )
    assert rejected.value.code == "stored_result_incompatible"
    assert await pool.fetchval("SELECT count(*) FROM jobs") == 1
    assert (
        await pool.fetchrow("SELECT payload,result_hash FROM runs WHERE run_id='historic'")
        == before
    )
    assert before["result_hash"] == row["result_hash"]
