import asyncio
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest
from a2_api.storage import JobRepository
from a2_api.worker import Worker
from test_storage import context, request


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_signal", [signal.SIGTERM, signal.SIGKILL])
async def test_worker_parent_loss_cannot_leave_a_calculation_orphan(
    database, tmp_path, stop_signal
):
    pool, schema, url = database
    repo = JobRepository(pool)
    accepted = await repo.enqueue("analysis", request(), context(), "key", "trace")
    pid_file = tmp_path / "child.pid"
    root = Path(__file__).resolve().parents[3]
    env = os.environ | {
        "A2_DATABASE_URL": url,
        "A2_DATABASE_SCHEMA": schema,
        "A2_CODE_VERSION": "demo-code",
        "A2_CALCULATION_HANDLER": "worker_handlers:slow",
        "A2_TEST_CHILD_PID": str(pid_file),
        "PYTHONPATH": os.pathsep.join(
            str(root / p) for p in ("packages/contracts/src", "apps/api/src", "apps/api/tests")
        ),
    }
    parent = subprocess.Popen(
        [sys.executable, "-m", "a2_api.runtime", "worker", "--once"],
        cwd=root,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    child_pid = None
    try:
        for _ in range(150):
            if pid_file.exists():
                child_pid = int(pid_file.read_text())
                break
            await asyncio.sleep(0.02)
        assert child_pid is not None, "Calculation handler must start"
        parent.send_signal(stop_signal)
        await asyncio.to_thread(parent.wait, 5)
        for _ in range(60):
            if not alive(child_pid):
                break
            await asyncio.sleep(0.02)
        assert not alive(child_pid), "Calculation survived the worker process"
        await Worker(repo, code_version="demo-code", handler="worker_handlers:success").run_once()
        assert (await repo.get_job(accepted.job_id)).status == "failed"
        assert await pool.fetchval("SELECT count(*) FROM runs") == 0
    finally:
        if parent.poll() is None:
            parent.kill()
            await asyncio.to_thread(parent.wait, 5)
        if child_pid is not None and alive(child_pid):
            os.kill(child_pid, signal.SIGKILL)


@pytest.mark.asyncio
async def test_database_lease_loss_stops_active_calculation(database, tmp_path, monkeypatch):
    pool, schema, _ = database
    repo = JobRepository(pool)
    accepted = await repo.enqueue("analysis", request(), context(), "key", "trace")
    pid_file = tmp_path / "lease-child.pid"
    monkeypatch.setenv("A2_TEST_CHILD_PID", str(pid_file))
    work = asyncio.create_task(
        Worker(repo, code_version="demo-code", handler="worker_handlers:slow").run_once()
    )
    child_pid = None
    try:
        for _ in range(150):
            if pid_file.exists():
                child_pid = int(pid_file.read_text())
                break
            await asyncio.sleep(0.02)
        assert child_pid is not None
        lock_pid = await pool.fetchval(
            "SELECT pid FROM pg_locks WHERE locktype='advisory' AND classid=21702 "
            "AND objid=(hashtext($1)::bigint & 4294967295)::oid AND granted",
            schema,
        )
        assert lock_pid is not None
        await pool.execute("SELECT pg_terminate_backend($1)", lock_pid)
        await asyncio.wait_for(asyncio.shield(work), timeout=2)
        assert not alive(child_pid)
        assert (await repo.get_job(accepted.job_id)).status == "failed"
    finally:
        if not work.done():
            work.cancel()
        await asyncio.gather(work, return_exceptions=True)
        if child_pid is not None and alive(child_pid):
            os.kill(child_pid, signal.SIGKILL)
