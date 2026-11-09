"""Schema-isolated PostgreSQL migrations, using asyncpg only for the connection."""

import asyncio
import os
import re

from alembic import context
from sqlalchemy import Connection, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.pool import NullPool

config = context.config
schema = config.attributes.get("database_schema", os.getenv("A2_DATABASE_SCHEMA", "a2_backend"))
if not isinstance(schema, str) or re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema) is None:
    raise ValueError("Database schema must match ^[a-z][a-z0-9_]{0,62}$")
config.attributes["database_schema"] = schema

raw_url = config.attributes.get("database_url", os.getenv("A2_DATABASE_URL"))
if not raw_url:
    raise ValueError("Configure database_url or A2_DATABASE_URL for PostgreSQL")
try:
    url = make_url(raw_url)
    if url.drivername not in {"postgres", "postgresql", "postgresql+asyncpg"}:
        raise ValueError("Unsupported PostgreSQL driver")
    url = url.set(drivername="postgresql+asyncpg")
except (ArgumentError, TypeError, ValueError):
    raise ValueError("Database URL must identify PostgreSQL with asyncpg") from None


def bootstrap_schema() -> None:
    """The schema is validated before becoming an SQL identifier."""
    context.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
    context.execute(text(f'SET LOCAL search_path TO "{schema}"'))


def run_migrations_offline() -> None:
    context.configure(
        url=url,
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema=schema,
        transactional_ddl=True,
    )
    with context.begin_transaction():
        bootstrap_schema()
        context.run_migrations()


def run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=None,
        version_table_schema=schema,
        transactional_ddl=True,
    )
    with context.begin_transaction():
        bootstrap_schema()
        context.run_migrations()


async def run_online() -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(url, poolclass=NullPool, echo=False)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_online())
