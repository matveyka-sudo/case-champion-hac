# BACKEND

Own `apps/api/`, its migrations, tests, and local documentation. Own canonical contracts and their exports in the separate BACKEND paths. Use parameterized asyncpg SQL at runtime and SQLAlchemy only for Alembic. Run CPU-heavy calculation outside the event loop with bounded concurrency. Coordinate engine semantics with ML1 and data and assistant semantics with ML2. All `pyproject.toml` files and the root `uv.lock` belong to DEVOPS.
