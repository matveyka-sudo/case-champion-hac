"""Legacy runner adapter preserving patch('a2_api.worker.calculate', ...)."""

from a2_api.core.jobs import ClaimedJob
from a2_api.workers.calculation import MAX_RESULT_BYTES as MAX_RESULT_BYTES
from a2_api.workers.calculation import HandlerOutput as HandlerOutput
from a2_api.workers.calculation import _child as _child
from a2_api.workers.calculation import _stop as _stop
from a2_api.workers.calculation import calculate as calculate
from a2_api.workers.runner import Worker as _Worker


class Worker(_Worker):
    async def _calculate(
        self, claim: ClaimedJob, handler: str, timeout_seconds: float
    ) -> HandlerOutput:
        return await calculate(claim, handler, timeout_seconds)
