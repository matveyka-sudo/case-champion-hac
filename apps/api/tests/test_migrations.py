import asyncio

import pytest

from apps.api.tests.migration_support import migrate


@pytest.mark.asyncio
async def test_migration_upgrade_twice_and_downgrade_preserve_unrelated_tables(database):
    pool, schema, url = database
    await pool.execute("CREATE TABLE unrelated_table(note text)")
    await asyncio.to_thread(migrate, url, schema)
    assert await pool.fetchval("SELECT version_num FROM alembic_version") == "0005_od_checkpoints"
    await asyncio.to_thread(migrate, url, schema, "base", True)
    assert await pool.fetchval("SELECT to_regclass('jobs')") is None
    assert await pool.fetchval("SELECT to_regclass('runs')") is None
    assert await pool.fetchval("SELECT to_regclass('assistant_budget')") is None
    assert await pool.fetchval("SELECT to_regclass('od_checkpoints')") is None
    assert await pool.fetchval("SELECT to_regclass('od_attempts')") is None
    assert await pool.fetchval("SELECT to_regclass('od_executions')") is None
    assert await pool.fetchval("SELECT to_regclass('unrelated_table')") == "unrelated_table"
    await asyncio.to_thread(migrate, url, schema)
    assert await pool.fetchval("SELECT count(*) FROM jobs") == 0
    assert await pool.fetchval("SELECT count(*) FROM assistant_budget") == 0


@pytest.mark.asyncio
async def test_api_export_includes_accepted_request_and_immutable_context(database):
    from a2_api.api.legacy import create_app
    from a2_api.db.repositories.jobs import JobRepository
    from a2_api.services.jobs import QueuedServices
    from a2_api.workers.runner import Worker
    from httpx import ASGITransport, AsyncClient
    from test_job_services import synthetic_preflight
    from test_storage import request

    pool, _, _ = database
    repo = JobRepository(pool)
    accepted = await QueuedServices(
        repo, prepare=synthetic_preflight, code_version="demo-code"
    ).analyse(request(), "key", "trace")
    await Worker(
        repo, code_version="demo-code", handler="worker_handlers:success", timeout_seconds=5
    ).run_once()
    run_id = (await repo.get_job(accepted.job_id)).run_id
    async with AsyncClient(
        transport=ASGITransport(app=create_app(QueuedServices(repo))), base_url="http://test"
    ) as api:
        exported = await api.get(f"/api/v1/runs/{run_id}/export")
        assert exported.status_code == 200
        assert exported.json()["parameters"]["request"]["bundle_id"] == "demo-synthetic-bundle"
        assert exported.json()["parameters"]["context"]["code_version"] == "demo-code"
        csv = await api.get(f"/api/v1/runs/{run_id}/export", params={"format": "csv"})
        assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
        assert "points_0_100" in csv.text
        assert (
            await api.get(f"/api/v1/runs/{run_id}/export", params={"format": "xls"})
        ).status_code == 422
