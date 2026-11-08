"""Single-worker calculation runner with killable spawned CPU processes."""

import asyncio
import importlib
import json
import logging
import multiprocessing
import os
import threading
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from uuid import uuid4

from a2_contracts.api import ErrorDetail
from a2_contracts.results import ProfileResult
from a2_contracts.scenarios import Comparison
from a2_contracts.v2.results import AnalysisResult
from a2_contracts.v2.scenarios import ScenarioResult

from .jobs import CalculationOutput, ClaimedJob, V2JobContext
from .services import DomainError
from .storage import JobRepository, RunResult

logger = logging.getLogger("a2_api.worker")
MAX_RESULT_BYTES = 16 * 1024 * 1024
HandlerOutput = RunResult | CalculationOutput


def _child(send: Connection, handler_path: str, claim_json: str, parent_pid: int) -> None:
    """Handler path is trusted startup configuration, never a request parameter."""
    stop = threading.Event()

    def watch_parent() -> None:
        while not stop.is_set():
            if os.getppid() != parent_pid:
                os._exit(70)
            stop.wait(0.05)

    threading.Thread(target=watch_parent, daemon=True).start()
    try:
        module, name = handler_path.split(":", 1)
        handler = getattr(importlib.import_module(module), name)
        claim = ClaimedJob.model_validate_json(claim_json)
        result = handler(claim)
        if not isinstance(
            result, (ProfileResult, Comparison, AnalysisResult, ScenarioResult, CalculationOutput)
        ):
            raise TypeError("Handler must return a canonical run result")
        output = json.dumps(
            {
                "result_kind": (
                    "profile"
                    if isinstance(result, ProfileResult)
                    else "comparison"
                    if isinstance(result, Comparison)
                    else "analysis_v2"
                    if isinstance(result, AnalysisResult)
                    else "scenario_v2"
                    if isinstance(result, ScenarioResult)
                    else "analysis_output"
                ),
                "result": result.model_dump(mode="json"),
            },
            allow_nan=False,
        ).encode()
        if len(output) > MAX_RESULT_BYTES:
            raise ValueError("Calculation output exceeds configured transport limit")
    except DomainError as exc:
        output = json.dumps(
            {
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "field_errors": [f.model_dump() for f in exc.field_errors],
                    "retryable": exc.retryable,
                }
            }
        ).encode()
    except BaseException:
        output = json.dumps(
            {
                "error": {
                    "code": "engine_unavailable",
                    "message": "Calculation failed",
                    "field_errors": [],
                    "retryable": False,
                }
            }
        ).encode()
    try:
        send.send_bytes(output)
    finally:
        send.close()
        stop.set()


def _stop(process: BaseProcess) -> None:
    if process.is_alive():
        process.terminate()
    process.join(timeout=1)
    if process.is_alive():
        process.kill()
        process.join(timeout=1)
    process.close()


async def calculate(claim: ClaimedJob, handler: str, timeout_seconds: float) -> HandlerOutput:
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(
        target=_child, args=(send, handler, claim.model_dump_json(), os.getpid()), daemon=True
    )
    process.start()
    send.close()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    try:
        while not receive.poll():
            if not process.is_alive():
                raise DomainError(503, "engine_unavailable", "Calculation process stopped")
            if loop.time() >= deadline:
                raise DomainError(503, "job_timeout", "Calculation exceeded hard timeout")
            await asyncio.sleep(0.02)
        try:
            data = await asyncio.wait_for(
                asyncio.to_thread(receive.recv_bytes, MAX_RESULT_BYTES),
                timeout=max(0.001, deadline - loop.time()),
            )
        except TimeoutError as exc:
            raise DomainError(503, "job_timeout", "Calculation exceeded hard timeout") from exc
        except (EOFError, OSError) as exc:
            raise DomainError(503, "engine_unavailable", "Calculation process stopped") from exc
        output = json.loads(data)
        if "error" in output:
            error = ErrorDetail.model_validate(output["error"] | {"trace_id": claim.job.trace_id})
            raise DomainError(
                503,
                error.code,
                error.message,
                retryable=error.retryable,
                field_errors=error.field_errors,
            )
        models: dict[
            str,
            type[ProfileResult]
            | type[Comparison]
            | type[AnalysisResult]
            | type[ScenarioResult]
            | type[CalculationOutput],
        ] = {
            "profile": ProfileResult,
            "comparison": Comparison,
            "analysis_v2": AnalysisResult,
            "scenario_v2": ScenarioResult,
            "analysis_output": CalculationOutput,
        }
        model = models[output["result_kind"]]
        return model.model_validate(output["result"])
    finally:
        await asyncio.shield(asyncio.to_thread(_stop, process))
        receive.close()


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
        calculation = asyncio.create_task(calculate(claim, self.handler, self.timeout_seconds))
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
