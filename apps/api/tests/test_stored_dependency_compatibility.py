"""Historical contradictory v2 payloads receive a safe refusal, without mutation."""

import json
from hashlib import sha256

import pytest
from a2_api.analysis_service import AnalysisService
from a2_api.api_v2 import create_app
from a2_api.services import DomainError
from a2_api.storage import JobRepository
from a2_api.worker import Worker
from httpx import ASGITransport, AsyncClient
from test_v2_storage import context, request


@pytest.mark.asyncio
async def test_historical_partial_reference_full_index_receives_safe_immutable_refusal(database):
    pool, _, _ = database
    repository = JobRepository(pool)
    token = "A" * 43
    scope = sha256(token.encode()).hexdigest()
    accepted = await repository.enqueue(
        "analysis",
        request("intercity_passenger"),
        context("intercity_passenger", analysis_id="historical-index", scope=scope),
        "historical",
        "trace",
        scope=scope,
    )
    await Worker(
        repository,
        handler="dependency_publication_handlers:result",
        code_version=context().code_version,
        timeout_seconds=5,
    ).run_once()
    assert (await repository.get_job(accepted.job_id, scope=scope)).status == "succeeded"
    # Dedicated test schema only: simulate a result accepted by the previous validator.
    row = await pool.fetchrow(
        "SELECT payload,result_hash FROM runs WHERE run_id=$1", "historical-index"
    )
    payload = json.loads(row["payload"])
    payload["references"][0]["completeness"] = "partial"
    await pool.execute(
        "UPDATE runs SET payload=$1::jsonb WHERE run_id=$2", json.dumps(payload), "historical-index"
    )
    before = await pool.fetchrow(
        "SELECT payload,result_hash FROM runs WHERE run_id=$1", "historical-index"
    )
    async with AsyncClient(
        transport=ASGITransport(app=create_app(AnalysisService(repository=repository))),
        base_url="http://test",
        cookies={"a2_session": token},
    ) as api:
        response = await api.get("/api/v1/analyses/historical-index")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "stored_result_incompatible"
        assert response.json()["error"]["message"] == "Stored result requires recalculation"
        for private in ("Traceback", "ValidationError", "references", "partial", "demo-evidence"):
            assert private not in response.text
    job_count = await pool.fetchval("SELECT count(*) FROM jobs")
    with pytest.raises(DomainError) as refusal:
        await repository.enqueue(
            "analysis",
            request("intercity_passenger"),
            context("intercity_passenger", analysis_id="historic-cache-retry", scope=scope),
            "historic-cache-retry",
            "trace",
            scope=scope,
        )
    assert refusal.value.status_code == 409
    assert refusal.value.code == "stored_result_incompatible"
    assert await pool.fetchval("SELECT count(*) FROM jobs") == job_count
    after = await pool.fetchrow(
        "SELECT payload,result_hash FROM runs WHERE run_id=$1", "historical-index"
    )
    assert after == before
    assert before["result_hash"] == row["result_hash"]
