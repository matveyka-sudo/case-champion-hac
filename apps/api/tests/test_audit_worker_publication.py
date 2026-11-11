"""The advisory-lock session must fence the entire publication transaction."""

from unittest.mock import patch

import pytest
from a2_api.core.jobs import CalculationOutput
from a2_api.db.repositories.jobs import JobRepository
from a2_api.workers.runner import Worker
from test_v2_storage import context, proposal, request, result


@pytest.mark.asyncio
@pytest.mark.parametrize("loss_point", ["before_transaction", "after_run_insert"])
async def test_advisory_connection_loss_fences_publication(database, loss_point):
    pool, schema, _ = database
    owner_pid = None
    publication_pid = None

    async def terminate_owner():
        nonlocal owner_pid
        owner_pid = await pool.fetchval(
            "SELECT pid FROM pg_locks WHERE locktype='advisory' AND classid=21702 "
            "AND objid=(hashtext($1)::bigint & 4294967295)::oid AND granted",
            schema,
        )
        assert owner_pid is not None
        assert await pool.fetchval("SELECT pg_terminate_backend($1)", owner_pid)

    class InterruptedRepository(JobRepository):
        async def succeed(self, claim, output, proposals=()):
            if loss_point == "before_transaction":
                await terminate_owner()
            return await super().succeed(claim, output, proposals)

        async def _save_proposals(self, connection, proposals, scope):
            nonlocal publication_pid
            # succeed has inserted the run inside its still-open transaction.
            if loss_point == "after_run_insert":
                assert await connection.fetchval("SELECT count(*) FROM runs") == 1
                publication_pid = await connection.fetchval("SELECT pg_backend_pid()")
                await terminate_owner()
            return await super()._save_proposals(connection, proposals, scope)

    repo = InterruptedRepository(pool)
    accepted = await repo.enqueue(
        "analysis", request(), context(), "publication", "trace", scope="owner"
    )

    async def calculate(*args):
        saved = result()
        return CalculationOutput(analysis=saved, proposals=[proposal(saved)])

    with patch("a2_api.workers.runner.calculate", calculate):
        assert await Worker(
            repo, handler="trusted:handler", code_version=context().code_version
        ).run_once()

    assert (await repo.get_job(accepted.job_id, scope="owner")).status == "failed"
    assert await pool.fetchval("SELECT count(*) FROM runs") == 0
    assert await pool.fetchval("SELECT count(*) FROM scenario_proposals") == 0
    if loss_point == "after_run_insert":
        assert publication_pid == owner_pid
    # Another worker can acquire the lock and perform normal restart recovery.
    async with repo.worker_lock() as acquired:
        assert acquired
        await repo.recover_orphans()
    assert await repo.claim("replacement-worker") is None
