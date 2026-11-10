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
from typing import TYPE_CHECKING

from a2_contracts.api import ErrorDetail
from a2_contracts.results import ProfileResult
from a2_contracts.scenarios import Comparison
from a2_contracts.v2.results import AnalysisResult
from a2_contracts.v2.scenarios import ScenarioResult

from a2_api.core.batches import MAX_CHECKPOINT_BYTES, BatchCheckpoint
from a2_api.core.errors import DomainError
from a2_api.core.jobs import CalculationOutput, ClaimedJob, V2JobContext
from a2_api.db.rows import RunResult
from a2_api.workers.checkpoint_channel import CHECKPOINT_PREFIX, MAX_RESUME_BYTES, CheckpointChannel

if TYPE_CHECKING:
    from a2_api.db.repositories.checkpoints import CheckpointRepository

logger = logging.getLogger("a2_api.workers.calculation")
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
        execution = (
            claim.context.batch_execution if isinstance(claim.context, V2JobContext) else None
        )
        result = (
            handler(claim, CheckpointChannel(send, execution))
            if execution is not None
            else handler(claim)
        )
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
                "result": result.model_dump(mode="json", exclude_defaults=True),
            },
            allow_nan=False,
            separators=(",", ":"),
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
    return await _calculate(claim, handler, timeout_seconds, None)


async def calculate_batches(
    claim: ClaimedJob, handler: str, timeout_seconds: float, checkpoints: "CheckpointRepository"
) -> HandlerOutput:
    return await _calculate(claim, handler, timeout_seconds, checkpoints)


async def _calculate(
    claim: ClaimedJob,
    handler: str,
    timeout_seconds: float,
    checkpoints: "CheckpointRepository | None",
) -> HandlerOutput:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    completed = []
    execution = None
    if checkpoints is not None:
        execution = checkpoints.execution(claim)
        completed = await asyncio.wait_for(checkpoints.begin(claim), timeout=timeout_seconds)
    elif isinstance(claim.context, V2JobContext) and claim.context.batch_execution is not None:
        raise DomainError(
            503, "engine_unavailable", "Batch execution requires its fenced repository"
        )
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=True)
    process = context.Process(
        target=_child, args=(send, handler, claim.model_dump_json(), os.getpid()), daemon=True
    )
    process.start()
    send.close()
    done = {checkpoint.batch_id for checkpoint in completed}
    try:
        if execution is not None:
            resume = json.dumps(
                {
                    "ipc_version": 1,
                    "execution_identity": execution.identity,
                    "checkpoints": [checkpoint.model_dump(mode="json") for checkpoint in completed],
                }
            ).encode()
            if len(resume) > MAX_RESUME_BYTES:
                raise DomainError(
                    503, "engine_unavailable", "Resume transport exceeds its byte budget"
                )
            await asyncio.wait_for(
                asyncio.to_thread(receive.send_bytes, resume),
                timeout=max(0.001, deadline - loop.time()),
            )
        messages = 0
        while True:
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
            if not data.startswith(CHECKPOINT_PREFIX):
                break
            messages += 1
            if (
                execution is None
                or checkpoints is None
                or messages > 2 * len(execution.batches)
                or len(data) > MAX_CHECKPOINT_BYTES + len(CHECKPOINT_PREFIX)
            ):
                raise DomainError(
                    503, "engine_unavailable", "Checkpoint protocol or message budget rejected"
                )
            checkpoint = BatchCheckpoint.model_validate_json(data[len(CHECKPOINT_PREFIX) :])
            await asyncio.wait_for(
                checkpoints.put(claim, checkpoint), timeout=max(0.001, deadline - loop.time())
            )
            done.add(checkpoint.batch_id)
            acknowledgment = json.dumps(
                {
                    "ipc_version": 1,
                    "batch_id": checkpoint.batch_id,
                    "content_hash": checkpoint.content_hash,
                }
            ).encode()
            await asyncio.wait_for(
                asyncio.to_thread(receive.send_bytes, acknowledgment),
                timeout=max(0.001, deadline - loop.time()),
            )
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
        if execution is not None and done != {batch_id for batch_id, _ in execution.batches}:
            raise DomainError(503, "engine_unavailable", "Calculation omitted approved OD batches")
        if checkpoints is not None:
            await asyncio.wait_for(
                checkpoints.finalize(claim), timeout=max(0.001, deadline - loop.time())
            )
        return model.model_validate(output["result"])
    finally:
        await asyncio.shield(asyncio.to_thread(_stop, process))
        receive.close()
