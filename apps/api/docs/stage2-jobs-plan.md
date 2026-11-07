# Stage 2 — durable jobs, runs and one worker

Goal: complete the next backend layer without selecting cities, institutions or transport scenarios. Build on the approved architecture/implementation plan and contract 0.1.0. First production region remains Leningrad Oblast, all geography supplied as data/configuration.

## Decisions and files

- PostgreSQL 17, parameterized asyncpg runtime; Alembic/SQLAlchemy only for migrations. Unique configured schema; no changes to root manifests, Compose, CI or user IDE files.
- `apps/api/migrations/` and `alembic.ini`: schema-scoped runs/jobs, FK, unique operation-kind/idempotency key and state checks; upgrade twice and downgrade/upgrade on a new test schema.
- `apps/api/src/a2_api/storage.py`: pool, transactional enqueue, atomic claim, immutable result publication, failed jobs, polling, cache lookup and explicit worker ownership.
- `apps/api/src/a2_api/worker.py`: one advisory-locked worker; each CPU calculation runs in a spawned child process. Timeout kills child, persists failed state; restart fails orphaned work explicitly, never blindly duplicates it.
- `apps/api/src/a2_api/job_services.py`: configuration/context preflight, duplicate replay before revalidation, scoped cache fingerprint, async HTTP submission and run retrieval. No engine/data configured means 503.
- `apps/api/src/a2_api/runtime.py`: explicit database/handler settings, API pool lifespan and worker CLI entrypoint. Handler import is trusted operator configuration, never input from HTTP/tools/model.
- `apps/api/src/a2_api/exports.py`: JSON and CSV from saved immutable run, with versions, parameters/hashes and units. CSV neutralizes formula prefixes in textual cells.
- `apps/api/tests/`: real PostgreSQL schema-per-test integration, spawned synthetic handlers only under tests. No production fixture fallback, no paid API.

## Verification order

1. Run existing suite and preserve unrelated changes. Write tests for idempotent repeat/conflict and transaction visibility; observe RED before storage implementation.
2. Upgrade clean schema twice. Concurrent same key creates one job. Same key/different payload gives 409. Missing IDs give 404. DB state checks reject impossible transitions.
3. Claim once, publish typed version-linked result atomically with job success. An insert/state failure cannot leave a visible unlinked result. Existing run IDs cannot be overwritten.
4. Test one-worker exclusion, restart recovery, unknown result/error, hard timeout with actual child termination and nonblocking event loop. Test cancellation/shutdown leaves no child processes behind.
5. POST→202→GET job→GET run/export through actual API/DB. Different bundle/profile/task/code/operation versions cannot share a cache entry. No configured preflight/handler yields 503.
6. Full pytest against isolated PostgreSQL, ruff, format, strict mypy, deterministic schema check and real-process smoke. Document exact results and colleague entrypoints.

## Integration boundary

Queue processing is verifiable now with synthetic handlers. Subject calculation still depends on accepted bundles and the three ML1 functions. Do not claim real regional results or enable paid model calls. Keep test DB isolated at port 55433 in Compose project `a2-backend-stage2`; credentials are disposable local test credentials. Production URL is supplied via environment and never logged.

## Progress

- Baseline: 132 tests passed; existing `.idea` changes left untouched.
- Complete: schema-scoped migration; atomic idempotency/cache/claim/publication; bounded killable worker; runtime pool/CLI; persisted JSON/CSV exports and safe error handling.
- Ruling: no changes to DEVOPS-owned manifests/lock. Temporarily installed greenlet 3.5.6 only in .venv; exact sqlalchemy[asyncio]/lock handoff recorded in stage2-handoff.md. Clean frozen installs need that integration or the documented temporary command.
- Ruling: immutable execution context records bundle content hash, deployed code, per-profile hashes/revisions, fixed task hashes, exact origins, baselines and server-computed case count. Context is produced by a trusted adapter, not accepted from HTTP.
- Ruling: worker uses spawned processes and an advisory lock instead of threads/extra brokers. Timed-out/interrupted work is failed explicitly; a new key is required for a requested rerun. No automatic duplicate execution after recovery.
- Independent review reproduced code-version mismatch, child surviving killed parent, and child surviving lost PG lock session. Dedicated regression tests observed RED; deployed-version checking, parent watchdog, SIGTERM cancellation and lease-loss cancellation/reaping now pass.
- Additional regression tests bind scenario operations, versions, origins and before intervals to accepted command/saved baseline. No engine formulas were implemented in HTTP.
- Final verification: 194 passed with actual isolated PostgreSQL 17; zero skips. Ruff check/format passed; strict mypy passed for 22 source/migration files; pip check passed for 57 installed packages. Export --check matches regenerated OpenAPI 0.2.0.
- Real-process smoke: separate Uvicorn API + CLI worker + spawned synthetic handler produced 202→succeeded→saved run→export. All fixtures/handlers remain explicitly synthetic under tests.
- Signal/process checks ran on macOS; PostgreSQL ran in Docker. The tests do not assert a verified full Linux deployment or real Leningrad transport results.
- Remaining: DEVOPS dependency/packaging/deployment wiring; accepted №5 bundle and №4 calculation adapters; Stage 3 tools/agent and paid API access/budget.
