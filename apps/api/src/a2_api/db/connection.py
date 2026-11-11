"""Operator-configured API database lifespan and the separate worker entrypoint."""

import asyncpg  # type: ignore[import-untyped]

from a2_api.core.config import RuntimeSettings


async def open_pool(settings: RuntimeSettings) -> asyncpg.Pool:
    if settings.database_url is None:
        raise ValueError("DATABASE_URL is required")
    url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    pool = await asyncpg.create_pool(
        url,
        min_size=1,
        max_size=5,
        command_timeout=10,
        timeout=10,
        server_settings={"search_path": settings.database_schema, "application_name": "a2-backend"},
    )
    assert pool is not None
    try:
        if (
            await pool.fetchval("SELECT to_regclass('jobs')") is None
            or await pool.fetchval("SELECT to_regclass('scenario_proposals')") is None
            or await pool.fetchval("SELECT to_regclass('evidence_chunks')") is None
            or await pool.fetchval("SELECT to_regclass('assistant_budget')") is None
            or await pool.fetchval("SELECT to_regclass('assistant_reservations')") is None
        ):
            raise ValueError("Apply backend Alembic migrations before starting API/worker")
    except BaseException:
        await pool.close()
        raise
    return pool
