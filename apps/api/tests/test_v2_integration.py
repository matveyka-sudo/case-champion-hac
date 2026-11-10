"""Synthetic API → PostgreSQL → spawned worker → scoped saved result."""

import httpx
import pytest
from a2_api.api.application import create_app
from a2_api.api.bootstrap import build_app
from a2_api.core.config import RuntimeSettings
from a2_api.db.repositories.jobs import JobRepository
from a2_api.services.analyses import AnalysisService
from a2_api.workers.runner import Worker
from fastapi.testclient import TestClient
from test_v2_http import body
from v2_handlers import registry


def request_body():
    return body() | {"city_id": "city-1"}


@pytest.mark.asyncio
async def test_scoped_synthetic_full_path_cache_idempotency_and_explanation(database):
    pool, _, _ = database
    repository = JobRepository(pool)
    service = AnalysisService(registry(), repository, code_version="demo-code", engine_enabled=True)
    app = create_app(service)
    async with (
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as owner,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as stranger,
    ):
        headers = {"Idempotency-Key": "synthetic-run"}
        submitted = await owner.post("/api/v1/analyses", json=request_body(), headers=headers)
        assert submitted.status_code == 202, submitted.text
        job_id = submitted.json()["job_id"]
        assert submitted.json()["status"] == "queued"
        assert (await stranger.get(f"/api/v1/jobs/{job_id}")).status_code == 404
        duplicate = await owner.post("/api/v1/analyses", json=request_body(), headers=headers)
        assert duplicate.json()["job_id"] == job_id
        conflict = await owner.post(
            "/api/v1/analyses",
            json=request_body() | {"passenger_mode": "private_car"},
            headers=headers,
        )
        assert conflict.status_code == 409
        worker = Worker(
            repository, handler="v2_handlers:success", code_version="demo-code", timeout_seconds=5
        )
        assert await worker.run_once()
        finished = (await owner.get(f"/api/v1/jobs/{job_id}")).json()
        assert finished["status"] == "succeeded", finished
        analysis_id = finished["analysis_id"]
        result = (await owner.get(f"/api/v1/analyses/{analysis_id}")).json()
        assert result["request"]["city_id"] == "city-1"
        assert result["result_hash"] != "demo-hash"
        assert "synthetic_only" in result["limitations"]
        for path, method in (
            (f"/api/v1/analyses/{analysis_id}", "GET"),
            (f"/api/v1/analyses/{analysis_id}/explanation", "POST"),
            (f"/api/v1/analyses/{analysis_id}/scenarios", "GET"),
            (f"/api/v1/jobs/{job_id}/cancel", "POST"),
        ):
            assert (await stranger.request(method, path)).status_code == 404
        explanation = await owner.post(f"/api/v1/analyses/{analysis_id}/explanation")
        assert explanation.status_code == 200
        assert explanation.json()["status"] == "template"
        assert explanation.json()["analysis_result_hash"] == result["result_hash"]
        assert (await owner.get(f"/api/v1/analyses/{analysis_id}/scenarios")).json() == []
        cache = await owner.post(
            "/api/v1/analyses", json=request_body(), headers={"Idempotency-Key": "cache"}
        )
        assert cache.json()["status"] == "succeeded"
        cached_job = (await owner.get(f"/api/v1/jobs/{cache.json()['job_id']}")).json()
        assert cached_job["analysis_id"] == analysis_id
        other = await stranger.post("/api/v1/analyses", json=request_body(), headers=headers)
        assert other.json()["status"] == "queued"
        assert other.json()["job_id"] != job_id


@pytest.mark.asyncio
async def test_cancellation_is_scoped_and_terminal_before_worker_claim(database):
    pool, _, _ = database
    app = create_app(
        AnalysisService(
            registry(), JobRepository(pool), code_version="demo-code", engine_enabled=True
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        job = await client.post(
            "/api/v1/analyses", json=request_body(), headers={"Idempotency-Key": "cancel"}
        )
        job_id = job.json()["job_id"]
        for _ in range(2):
            response = await client.post(f"/api/v1/jobs/{job_id}/cancel")
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "cancelled"
            assert response.json()["finished_at"] is not None
        assert await JobRepository(pool).claim("test-token") is None


def test_configured_v2_runtime_selects_registry_and_wires_real_repository(database_sync_config):
    url, schema = database_sync_config
    app = build_app(
        RuntimeSettings(
            database_url=url,
            database_schema=schema,
            registry_hook="v2_handlers:registry",
            code_version="demo-code",
            calculation_handler="v2_handlers:success",
        )
    )
    with TestClient(app) as client:
        assert (
            client.get("/api/v1/capabilities").json()["analyses"][0]["calculation_available"]
            is True
        )
        submitted = client.post(
            "/api/v1/analyses", json=request_body(), headers={"Idempotency-Key": "runtime"}
        )
        assert submitted.status_code == 202, submitted.text
        assert client.get(f"/api/v1/jobs/{submitted.json()['job_id']}").status_code == 200


@pytest.mark.asyncio
async def test_saved_server_scenario_runs_through_http_worker_and_cannot_change_baseline(database):
    pool, _, _ = database
    repository = JobRepository(pool)
    service = AnalysisService(
        registry(),
        repository,
        code_version="demo-code",
        engine_enabled=True,
        registered_template_ids=frozenset({"coordinate-transfer-v1"}),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(service)), base_url="http://test"
    ) as client:
        accepted = await client.post(
            "/api/v1/analyses", json=request_body(), headers={"Idempotency-Key": "baseline"}
        )
        assert accepted.status_code == 202, accepted.text
        await Worker(
            repository,
            handler="v2_handlers:with_proposals",
            code_version="demo-code",
            timeout_seconds=5,
        ).run_once()
        job = (await client.get(f"/api/v1/jobs/{accepted.json()['job_id']}")).json()
        assert job["status"] == "succeeded", job
        analysis_id = job["analysis_id"]
        baseline = (await client.get(f"/api/v1/analyses/{analysis_id}")).json()
        proposals = (await client.get(f"/api/v1/analyses/{analysis_id}/scenarios")).json()
        assert len(proposals) == 1
        assert proposals[0]["scenario_id"] != "provisional-scenario"
        assert proposals[0]["baseline_result_hash"] == baseline["result_hash"]
        scenario_id = proposals[0]["scenario_id"]
        run = await client.post(
            f"/api/v1/scenarios/{scenario_id}/runs",
            json={"schema_version": "2"},
            headers={"Idempotency-Key": "scenario"},
        )
        assert run.status_code == 202, run.text
        await Worker(
            repository,
            handler="v2_handlers:scenario_success",
            code_version="demo-code",
            timeout_seconds=5,
        ).run_once()
        finished = (await client.get(f"/api/v1/jobs/{run.json()['job_id']}")).json()
        assert finished["status"] == "succeeded", finished
        result = await client.get(f"/api/v1/scenario-runs/{finished['scenario_run_id']}")
        assert result.status_code == 200, result.text
        assert result.json()["baseline_result_hash"] == baseline["result_hash"]
        assert result.json()["feasibility"] == "unverified"
        assert (await client.get(f"/api/v1/analyses/{analysis_id}")).json() == baseline


@pytest.mark.asyncio
async def test_http_chat_accepts_question_and_only_entities_from_saved_result(database):
    pool, _, _ = database
    repo = JobRepository(pool)
    service = AnalysisService(registry(), repo, code_version="demo-code", engine_enabled=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(service)), base_url="http://test"
    ) as client:
        accepted = await client.post(
            "/api/v1/analyses", json=request_body(), headers={"Idempotency-Key": "chat-http"}
        )
        await Worker(
            repo, handler="v2_handlers:success", code_version="demo-code", timeout_seconds=5
        ).run_once()
        job = (await client.get("/api/v1/jobs/" + accepted.json()["job_id"])).json()
        endpoint = "/api/v1/analyses/" + job["analysis_id"] + "/explanation"
        reply = await client.post(
            endpoint,
            json={
                "question": "Почему здесь низкая доступность?",
                "selection": {"entity_kind": "sample_point", "entity_id": "demo-point"},
            },
        )
        assert reply.status_code == 200, reply.text
        assert reply.json()["status"] == "template"
        denied = await client.post(
            endpoint,
            json={
                "question": "Другой объект",
                "selection": {"entity_kind": "sample_point", "entity_id": "foreign"},
            },
        )
        assert denied.status_code == 404
        assert await pool.fetchval("SELECT count(*) FROM jobs") == 1


@pytest.mark.asyncio
async def test_saved_map_entities_and_unknown_population_reach_http_and_chat(database):
    pool, _, _ = database
    repo = JobRepository(pool)
    service = AnalysisService(registry(), repo, code_version="demo-code", engine_enabled=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(service)), base_url="http://test"
    ) as client:
        accepted = await client.post(
            "/api/v1/analyses", json=request_body(), headers={"Idempotency-Key": "map-http"}
        )
        await Worker(
            repo, handler="v2_handlers:with_map", code_version="demo-code", timeout_seconds=5
        ).run_once()
        job = (await client.get("/api/v1/jobs/" + accepted.json()["job_id"])).json()
        assert job["status"] == "succeeded", job
        analysis_id = job["analysis_id"]
        result = (await client.get("/api/v1/analyses/" + analysis_id)).json()
        assert result["map_data"]["features"][0]["population"] is None
        assert result["map_data"]["features"][0]["geometry"]["type"] == "Polygon"
        assert (
            result["metrics"][0]["subject"]["point_id"]
            == result["map_data"]["features"][1]["entity_id"]
        )
        explanation = await client.post(
            "/api/v1/analyses/" + analysis_id + "/explanation",
            json={
                "question": "Что известно о населении?",
                "selection": {"entity_kind": "cell", "entity_id": "synthetic-cell"},
            },
        )
        assert explanation.status_code == 200, explanation.text
        assert (await client.get("/api/v1/analyses/" + analysis_id)).json() == result
