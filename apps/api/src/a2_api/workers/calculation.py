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
from a2_contracts.v2.requests import UrbanPassengerRequest
from a2_contracts.v2.results import AnalysisResult
from a2_contracts.v2.scenarios import ScenarioResult, ScenarioRunRequest

from a2_api.core.batches import MAX_CHECKPOINT_BYTES, BatchCheckpoint
from a2_api.core.errors import DomainError
from a2_api.core.jobs import CalculationOutput, ClaimedJob, V2JobContext
from a2_api.db.rows import RunResult
from a2_api.workers.checkpoint_channel import CHECKPOINT_PREFIX, MAX_RESUME_BYTES, CheckpointChannel

if TYPE_CHECKING:
    from a2_api.db.repositories.checkpoints import CheckpointRepository

logger = logging.getLogger("a2_api.workers.calculation")
MAX_RESULT_BYTES = 16 * 1024 * 1024
MAX_DEMO_SCENARIO_BYTES = 32 * 1024 * 1024
_DEMO_LABELS = frozenset({"DEMO_SYNTHETIC_TRANSPORT_MODEL", "NOT_A_REAL_CITY_RANKING"})
HandlerOutput = RunResult | CalculationOutput


def _result_byte_limit(claim: ClaimedJob) -> int:
    """Only a server-frozen, admitted DEMO scenario receives the larger IPC bound."""
    context = claim.context
    if (
        os.environ.get("A2_DEMO_TRANSPORT_ENABLED", "").strip().lower() != "true"
        or claim.kind != "scenario"
        or not isinstance(context, V2JobContext)
        or not context.snapshot_id.startswith("demo-moscow-")
        or not isinstance(context.normalized_request, UrbanPassengerRequest)
        or context.normalized_request.city_id != "moscow"
        or not isinstance(claim.request, ScenarioRunRequest)
    ):
        return MAX_RESULT_BYTES
    proposal, scope = context.scenario_proposal, context.scenario_endpoint_scope
    if (
        proposal is None
        or scope is None
        or scope.snapshot_id != context.snapshot_id
        or scope.snapshot_hash != context.snapshot_hash
        or scope.city_id != "moscow"
        or not scope.point_origins
        or not scope.destination_nodes
        or not _DEMO_LABELS <= set(proposal.assumptions)
        or not _DEMO_LABELS <= set(proposal.limitations)
        or proposal.versions != context.versions
        or proposal.baseline_analysis_id != context.analysis_id
        or claim.request.scenario_id != proposal.scenario_id
        or claim.request.baseline_analysis_id != context.analysis_id
    ):
        return MAX_RESULT_BYTES
    return MAX_DEMO_SCENARIO_BYTES


def _validate_extended_result(claim: ClaimedJob, result: HandlerOutput) -> None:
    if _result_byte_limit(claim) == MAX_RESULT_BYTES:
        return
    assert isinstance(claim.context, V2JobContext)
    proposal = claim.context.scenario_proposal
    assert proposal is not None
    if (
        not isinstance(result, ScenarioResult)
        or result.versions != claim.context.versions
        or result.scenario_id != proposal.scenario_id
        or result.baseline_analysis_id != claim.context.analysis_id
        or result.baseline_result_hash != proposal.baseline_result_hash
        or not _DEMO_LABELS <= set(result.assumptions)
        or not _DEMO_LABELS <= set(result.limitations)
    ):
        raise ValueError("Extended DEMO transport requires its exact labelled scenario result")


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
        _validate_extended_result(claim, result)
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
        if len(output) > _result_byte_limit(claim):
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
    result_byte_limit = _result_byte_limit(claim)
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
                    asyncio.to_thread(receive.recv_bytes, result_byte_limit),
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
        result = model.model_validate(output["result"])
        _validate_extended_result(claim, result)
        return result
    finally:
        await asyncio.shield(asyncio.to_thread(_stop, process))
        receive.close()
