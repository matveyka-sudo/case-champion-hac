import asyncio
import os

import pytest
from a2_api.db.repositories.jobs import JobRepository
from a2_api.workers.runner import Worker
from test_storage import context, request


@pytest.mark.asyncio
async def test_spawned_worker_publishes_run_and_keeps_event_loop_responsive(database):
    pool, _, _ = database
    repo = JobRepository(pool)
    accepted = await repo.enqueue("analysis", request(), context(), "key", "trace")
    worker = Worker(
        repo, code_version="demo-code", handler="worker_handlers:success", timeout_seconds=5
    )
    ticks = 0
    done = False

    async def tick():
        nonlocal ticks
        while not done:
            ticks += 1
            await asyncio.sleep(0.005)

    ticker = asyncio.create_task(tick())
    try:
        assert await worker.run_once() is True
    finally:
        done = True
        await ticker
    job = await repo.get_job(accepted.job_id)
    assert job.status == "succeeded"
    assert (await repo.get_run(job.run_id)).run_id == job.run_id
    assert ticks > 2
    assert await worker.run_once() is False


@pytest.mark.asyncio
async def test_timeout_terminates_child_and_persists_failure(database, tmp_path, monkeypatch):
    pool, _, _ = database
    repo = JobRepository(pool)
    accepted = await repo.enqueue("analysis", request(), context(), "key", "trace")
    pid_file = tmp_path / "synthetic-child.pid"
    monkeypatch.setenv("A2_TEST_CHILD_PID", str(pid_file))
    worker = Worker(
        repo, code_version="demo-code", handler="worker_handlers:slow", timeout_seconds=1
    )
    await worker.run_once()
    job = await repo.get_job(accepted.job_id)
    assert job.status == "failed" and job.error.code == "job_timeout"
    assert pid_file.exists(), "Test must reach the child handler before its timeout"
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)
    assert await pool.fetchval("SELECT count(*) FROM runs") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("handler", ["crash", "abrupt_exit", "domain_failure"])
async def test_engine_failure_is_explicit_and_sanitized(database, handler):
    pool, _, _ = database
    repo = JobRepository(pool)
    accepted = await repo.enqueue("analysis", request(), context(), "key", "trace")
    await Worker(
        repo, code_version="demo-code", handler="worker_handlers:" + handler, timeout_seconds=5
    ).run_once()
    job = await repo.get_job(accepted.job_id)
    assert job.status == "failed" and job.error.code == "engine_unavailable"
    assert any(field.location == ["worker", "calculation"] for field in job.error.field_errors)
    assert "secret_test_database_password" not in job.model_dump_json()


@pytest.mark.asyncio
async def test_single_worker_lock_is_schema_scoped_and_exclusive(database):
    pool, _, _ = database
    repo = JobRepository(pool)
    async with repo.worker_lock() as acquired:
        assert acquired is True
        async with repo.worker_lock() as second:
            assert second is False
    async with repo.worker_lock() as after_release:
        assert after_release is True


@pytest.mark.asyncio
async def test_shutdown_kills_child_and_leaves_terminal_failure(database, tmp_path, monkeypatch):
    pool, _, _ = database
    repo = JobRepository(pool)
    accepted = await repo.enqueue("analysis", request(), context(), "key", "trace")
    pid_file = tmp_path / "child.pid"
    monkeypatch.setenv("A2_TEST_CHILD_PID", str(pid_file))
    work = asyncio.create_task(
        Worker(repo, code_version="demo-code", handler="worker_handlers:slow").run_once()
    )
    for _ in range(100):
        if pid_file.exists():
            break
        await asyncio.sleep(0.02)
    assert pid_file.exists()
    work.cancel()
    with pytest.raises(asyncio.CancelledError):
        await work
    assert (await repo.get_job(accepted.job_id)).status == "failed"
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)


@pytest.mark.asyncio
async def test_worker_cannot_run_old_job_as_a_new_code_version(database):
    pool, _, _ = database
    repo = JobRepository(pool)
    accepted = await repo.enqueue("analysis", request(), context(), "key", "trace")
    await Worker(repo, handler="worker_handlers:success", code_version="new-code").run_once()
    job = await repo.get_job(accepted.job_id)
    assert job.status == "failed" and job.error.code == "incompatible_versions"
    assert any(field.location == ["worker", "preflight"] for field in job.error.field_errors)
    assert "Recalculate" in job.error.message
    assert await pool.fetchval("SELECT count(*) FROM runs") == 0
