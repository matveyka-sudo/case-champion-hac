import pytest
from a2_api.app import create_app
from a2_api.job_services import QueuedServices
from a2_api.services import DomainError
from a2_api.storage import JobRepository
from a2_api.worker import Worker
from httpx import ASGITransport, AsyncClient
from test_storage import context, request


async def synthetic_preflight(kind, payload):
    assert kind == "analysis"
    if payload.bundle_id != "demo-synthetic-bundle":
        raise DomainError(404, "unknown_id", "Synthetic bundle not found")
    return context()


@pytest.mark.asyncio
async def test_real_database_http_job_run_and_idempotent_replay(database):
    pool, _, _ = database
    repo = JobRepository(pool)
    services = QueuedServices(repo, prepare=synthetic_preflight, code_version="demo-code")
    app = create_app(services)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api:
        headers = {"Idempotency-Key": "synthetic-test-key"}
        response = await api.post(
            "/api/v1/analyses", json=request().model_dump(mode="json"), headers=headers
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        assert response.json()["status"] == "queued"
        trace = response.headers["x-trace-id"]
        job = (await api.get("/api/v1/jobs/" + job_id)).json()
        assert job["trace_id"] == trace and job["run_id"] is None
        await Worker(
            repo, code_version="demo-code", handler="worker_handlers:success", timeout_seconds=5
        ).run_once()
        job = (await api.get("/api/v1/jobs/" + job_id)).json()
        assert job["status"] == "succeeded"
        result = (await api.get("/api/v1/runs/" + job["run_id"])).json()
        assert result["bundle_id"] == "demo-synthetic-bundle"
        assert result["warnings"] == ["synthetic_example_not_city_results"]
        response = await api.post(
            "/api/v1/analyses", json=request().model_dump(mode="json"), headers=headers
        )
        assert response.status_code == 202
        assert response.json() == {"job_id": job_id, "status": "succeeded"}
        changed = request().model_dump(mode="json") | {"include_case_results": True}
        response = await api.post("/api/v1/analyses", json=changed, headers=headers)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "idempotency_conflict"


@pytest.mark.asyncio
async def test_no_preflight_means_no_fake_accepted_job(database):
    pool, _, _ = database
    services = QueuedServices(JobRepository(pool))
    with pytest.raises(DomainError) as exc:
        await services.analyse(request(), "key", "trace")
    assert exc.value.status_code == 503
    assert await pool.fetchval("SELECT count(*) FROM jobs") == 0


@pytest.mark.asyncio
async def test_context_must_match_request_and_deployed_code(database):
    pool, _, _ = database

    async def wrong_context(kind, payload):
        return context().model_copy(update={"bundle_id": "other"})

    for prepare, code in [(wrong_context, "demo-code"), (synthetic_preflight, "new-code")]:
        with pytest.raises(DomainError) as exc:
            await QueuedServices(JobRepository(pool), prepare=prepare, code_version=code).analyse(
                request(), "key", "trace"
            )
        assert exc.value.status_code == 409
    assert await pool.fetchval("SELECT count(*) FROM jobs") == 0


@pytest.mark.asyncio
async def test_completed_replay_does_not_depend_on_current_preflight(database):
    pool, _, _ = database
    repo = JobRepository(pool)
    first = await QueuedServices(
        repo, prepare=synthetic_preflight, code_version="demo-code"
    ).analyse(request(), "key", "trace")
    await Worker(
        repo, code_version="demo-code", handler="worker_handlers:success", timeout_seconds=5
    ).run_once()
    replay = await QueuedServices(repo).analyse(request(), "key", "trace")
    assert replay.job_id == first.job_id and replay.status == "succeeded"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "update,status",
    [
        ({"task_count": 3501}, 422),
        ({"profile_versions": {"regular": 2}}, 409),
    ],
)
async def test_preflight_limits_and_versions_are_enforced(database, update, status):
    pool, _, _ = database

    async def prepared(kind, payload):
        return context().model_copy(update=update)

    with pytest.raises(DomainError) as exc:
        await QueuedServices(
            JobRepository(pool), prepare=prepared, code_version="demo-code"
        ).analyse(request(), "key", "trace")
    assert exc.value.status_code == status
    assert await pool.fetchval("SELECT count(*) FROM jobs") == 0
