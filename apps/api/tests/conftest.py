import asyncio
import os
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[3]


def migrate(url, schema, revision="head", downgrade=False):
    config = Config(str(ROOT / "apps/api/alembic.ini"))
    config.attributes["database_url"] = url
    config.attributes["database_schema"] = schema
    (command.downgrade if downgrade else command.upgrade)(config, revision)


@pytest.fixture
def database_sync_config():
    url = os.environ.get("A2_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set A2_TEST_DATABASE_URL to a dedicated PostgreSQL test database")
    schema = "a2_test_" + uuid4().hex
    migrate(url, schema)
    try:
        yield url, schema
    finally:

        async def cleanup():
            connection = await asyncpg.connect(url)
            try:
                await connection.execute(f'DROP SCHEMA "{schema}" CASCADE')
            finally:
                await connection.close()

        asyncio.run(cleanup())


@pytest_asyncio.fixture
async def database():
    url = os.environ.get("A2_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set A2_TEST_DATABASE_URL to a dedicated PostgreSQL test database")
    schema = "a2_test_" + uuid4().hex
    await asyncio.to_thread(migrate, url, schema)
    pool = await asyncpg.create_pool(
        url, min_size=1, max_size=5, server_settings={"search_path": schema}
    )
    try:
        yield pool, schema, url
    finally:
        await pool.close()
        connection = await asyncpg.connect(url)
        try:
            assert schema.startswith("a2_test_") and schema.replace("_", "").isalnum()
            await connection.execute(f'DROP SCHEMA "{schema}" CASCADE')
        finally:
            await connection.close()
