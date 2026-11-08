import pytest
from a2_api.runtime import RuntimeSettings, build_app, open_pool
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_storage import request


@pytest.mark.parametrize("schema", ["public;DROP TABLE x", "x.y", "UPPER", ""])
def test_runtime_rejects_unsafe_database_schema_before_connecting(schema):
    with pytest.raises(ValidationError):
        RuntimeSettings(database_schema=schema)


def test_runtime_configuration_does_not_expose_password():
    settings = RuntimeSettings(database_url="postgresql://test:private-value@localhost/test")
    assert "private-value" not in repr(settings)


def test_database_disabled_preserves_unavailable_default():
    with TestClient(build_app(RuntimeSettings())) as api:
        assert api.get("/health").status_code == 200
        assert api.get("/api/v1/capabilities").status_code == 200
        assert api.get("/api/v1/profiles").status_code == 404


def test_lifespan_opens_pool_and_persists_pollable_job(database_sync_config):
    url, schema = database_sync_config
    settings = RuntimeSettings(database_url=url, database_schema=schema)
    with TestClient(build_app(settings)) as api:
        assert api.get("/api/v1/jobs/missing").status_code == 404
        response = api.post(
            "/api/v1/analyses",
            json=request().model_dump(mode="json"),
            headers={"Idempotency-Key": "test"},
        )
        assert response.status_code == 422


@pytest.mark.asyncio
async def test_runtime_pool_uses_configured_schema_and_closes(database):
    _, schema, url = database
    pool = await open_pool(RuntimeSettings(database_url=url, database_schema=schema))
    try:
        assert await pool.fetchval("SELECT current_schema()") == schema
        assert await pool.fetchval("SELECT count(*) FROM jobs") == 0
    finally:
        await pool.close()


def test_legacy_runtime_requires_explicit_operator_selection():
    with TestClient(build_app(RuntimeSettings(contract_version="legacy"))) as api:
        assert api.get("/api/v1/profiles").status_code == 503
        assert api.get("/api/v1/capabilities").status_code == 404


@pytest.mark.asyncio
async def test_runtime_refuses_database_with_only_historical_migration(database):
    import asyncio

    from conftest import migrate

    _, schema, url = database
    await asyncio.to_thread(migrate, url, schema, "0001_jobs_runs", True)
    with pytest.raises(ValueError, match="migrations"):
        await open_pool(RuntimeSettings(database_url=url, database_schema=schema))
