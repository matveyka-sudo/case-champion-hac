"""Spawned synthetic outputs must not publish concealed connectivity losses."""

import httpx
import pytest
from a2_api.analysis_service import AnalysisService
from a2_api.api_v2 import create_app
from a2_api.storage import JobRepository
from a2_api.worker import Worker
from test_v2_integration import request_body
from v2_handlers import registry


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,accepted",
    [("control", True), ("deleted", False), ("no-route", False), ("loss", True), ("unknown", True)],
)
async def test_spawned_scenario_transition_publication_api_and_cache(
    database, monkeypatch, mode, accepted
):
    pool, _, _ = database
    repository = JobRepository(pool)
    service = AnalysisService(
        registry(),
        repository,
        code_version="demo-code",
        engine_enabled=True,
        registered_template_ids=frozenset({"coordinate-transfer-v1"}),
    )
    monkeypatch.setenv("A2_SYNTHETIC_TRANSITION", mode)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(service)), base_url="http://test"
    ) as client:
        submitted = await client.post(
            "/api/v1/analyses", json=request_body(), headers={"Idempotency-Key": "baseline"}
        )
        assert submitted.status_code == 202, submitted.text
        await Worker(
            repository,
            handler="scenario_transition_handlers:baseline_with_proposal",
            code_version="demo-code",
            timeout_seconds=5,
        ).run_once()
        job = (await client.get("/api/v1/jobs/" + submitted.json()["job_id"])).json()
        assert job["status"] == "succeeded", job
        analysis_id = job["analysis_id"]
        baseline = (await client.get(f"/api/v1/analyses/{analysis_id}")).json()
        commands = (await client.get(f"/api/v1/analyses/{analysis_id}/scenarios")).json()
        endpoint = f"/api/v1/scenarios/{commands[0]['scenario_id']}/runs"
        run = await client.post(
            endpoint, json={"schema_version": "2"}, headers={"Idempotency-Key": "scenario"}
        )
        assert run.status_code == 202, run.text
        await Worker(
            repository,
            handler="scenario_transition_handlers:scenario",
            code_version="demo-code",
            timeout_seconds=5,
        ).run_once()
        finished = (await client.get("/api/v1/jobs/" + run.json()["job_id"])).json()
        unchanged = (await client.get(f"/api/v1/analyses/{analysis_id}")).json() == baseline
        published_count = await pool.fetchval(
            "SELECT count(*) FROM runs WHERE result_kind='scenario_v2'"
        )
        lookup_id = finished.get("scenario_run_id") or "synthetic-scenario-run"
        lookup = await client.get("/api/v1/scenario-runs/" + lookup_id)
        fresh = await client.post(
            endpoint, json={"schema_version": "2"}, headers={"Idempotency-Key": "cache-check"}
        )
        assert fresh.status_code == 202, fresh.text
        observations = dict(
            job_status=finished["status"],
            published_count=published_count,
            api_status=lookup.status_code,
            cache_status=fresh.json()["status"],
            baseline_unchanged=unchanged,
        )
        assert finished["status"] == ("succeeded" if accepted else "failed"), observations
        assert unchanged
        assert published_count == int(accepted)
        assert fresh.json()["status"] == ("succeeded" if accepted else "queued")
        if accepted:
            response = await client.get("/api/v1/scenario-runs/" + finished["scenario_run_id"])
            assert response.status_code == 200
            if mode == "loss":
                assert response.json()["deltas"][0]["outcome"] == "lost_connection"
            if mode == "unknown":
                assert response.json()["status"] == "partial"
        else:
            assert finished.get("scenario_run_id") is None
            assert (
                await client.get("/api/v1/scenario-runs/synthetic-scenario-run")
            ).status_code == 404
