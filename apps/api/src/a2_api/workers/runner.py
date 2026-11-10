"""Single-worker calculation runner with killable spawned CPU processes."""

import asyncio
import logging
from uuid import uuid4

from a2_contracts.api import ErrorDetail

from a2_api.core.errors import DomainError
from a2_api.core.jobs import CalculationOutput, ClaimedJob, V2JobContext
from a2_api.db.repositories.checkpoints import CheckpointRepository
from a2_api.db.repositories.jobs import JobRepository
from a2_api.workers.calculation import HandlerOutput, calculate, calculate_batches

logger = logging.getLogger("a2_api.workers.runner")


class Worker:
    def __init__(
        self,
        repository: JobRepository,
        *,
        handler: str,
        code_version: str,
        timeout_seconds: float = 600,
    ) -> None:
        if not 0 < timeout_seconds <= 600:
            raise ValueError("Job hard timeout must be positive and <=600 seconds")
        if ":" not in handler:
            raise ValueError("Handler must be an operator-configured module:function")
        self.repository = repository
        self.handler = handler
        self.timeout_seconds = timeout_seconds
        self.code_version = code_version

    async def run_once(self) -> bool:
        lease_lost = asyncio.Event()
        async with self.repository.worker_lock(lease_lost) as acquired:
            if not acquired:
                raise DomainError(
                    503, "engine_unavailable", "Another worker owns this database schema"
                )
            await self.repository.recover_orphans()
            claim = await self.repository.claim(str(uuid4()))
            if claim is None:
                return False
            logger.info("job_started job_id=%s trace_id=%s", claim.job.job_id, claim.job.trace_id)
            try:
                if claim.context.code_version != self.code_version:
                    raise DomainError(
                        409, "incompatible_versions", "Queued job requires another code version"
                    )
                result = await self._calculate_with_lease(claim, lease_lost)
                if isinstance(result, CalculationOutput):
                    await self.repository.succeed(
                        claim, result.analysis, proposals=result.proposals
                    )
                else:
                    await self.repository.succeed(claim, result)
            except asyncio.CancelledError:
                error = ErrorDetail(
                    code="engine_unavailable",
                    message="Worker shut down; explicit retry required",
                    field_errors=[],
                    retryable=True,
                    trace_id=claim.job.trace_id,
                )
                await asyncio.shield(self.repository.fail(claim, error))
                raise
            except Exception as exc:
                domain = (
                    exc
                    if isinstance(exc, DomainError)
                    else DomainError(503, "engine_unavailable", "Calculation failed")
                )
                await self.repository.fail(
                    claim,
                    ErrorDetail(
                        code=domain.code,
                        message=domain.message,
                        field_errors=domain.field_errors,
                        retryable=domain.retryable,
                        trace_id=claim.job.trace_id,
                    ),
                )
            scope = claim.context.access_scope if isinstance(claim.context, V2JobContext) else None
            final = await self.repository.get_job(claim.job.job_id, scope=scope)
            logger.info(
                "job_finished job_id=%s trace_id=%s status=%s",
                final.job_id,
                final.trace_id,
                final.status,
            )
            return True

    async def _monitor_claim(self, claim: ClaimedJob) -> None:
        while True:
            if not await self.repository.heartbeat(claim):
                scope = (
                    claim.context.access_scope if isinstance(claim.context, V2JobContext) else None
                )
                job = await self.repository.get_job(claim.job.job_id, scope=scope)
                if job.status == "cancelled":
                    raise DomainError(503, "job_cancelled", "Calculation was cancelled")
                raise DomainError(503, "engine_unavailable", "Calculation claim lease expired")
            await asyncio.sleep(min(1, self.repository.lease_seconds / 3))

    async def _calculate_with_lease(self, claim: ClaimedJob, lost: asyncio.Event) -> HandlerOutput:
        calculation = asyncio.create_task(
            self._calculate(claim, self.handler, self.timeout_seconds)
        )
        lease = asyncio.create_task(lost.wait())
        heartbeat = asyncio.create_task(self._monitor_claim(claim))
        try:
            done, _ = await asyncio.wait(
                (calculation, lease, heartbeat), return_when=asyncio.FIRST_COMPLETED
            )
            if lease in done:
                raise DomainError(503, "engine_unavailable", "Worker database lease was lost")
            if heartbeat in done:
                await heartbeat
            return await calculation
        finally:
            lease.cancel()
            heartbeat.cancel()
            if not calculation.done():
                calculation.cancel()
            await asyncio.gather(lease, heartbeat, calculation, return_exceptions=True)

    async def run_forever(self, poll_seconds: float = 0.5) -> None:
        if poll_seconds <= 0:
            raise ValueError("Poll interval must be positive")
        while True:
            if not await self.run_once():
                await asyncio.sleep(poll_seconds)

    async def _calculate(
        self, claim: ClaimedJob, handler: str, timeout_seconds: float
    ) -> HandlerOutput:
        if (
            isinstance(claim, ClaimedJob)
            and isinstance(claim.context, V2JobContext)
            and claim.context.batch_execution is not None
        ):
            return await calculate_batches(
                claim, handler, timeout_seconds, CheckpointRepository(self.repository)
            )
        return await calculate(claim, handler, timeout_seconds)
