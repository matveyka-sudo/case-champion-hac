# BACKEND

Own HTTP, migrations, API validation, cache, the single job worker and the trace log here. Own the canonical contracts and the product agent in the separate BACKEND paths. REST and tools share validators and use the public engine functions. Keep calculation work outside HTTP request processing. Use parameterized asyncpg SQL at runtime and SQLAlchemy only for Alembic. Coordinate domain semantics with ML1 and accepted data with ML2. DEVOPS owns every Python manifest and the root lock.
