"""Operator-configured API database lifespan and the separate worker entrypoint."""

import argparse
import asyncio
import signal

from a2_api.core.config import RuntimeSettings
from a2_api.db.connection import open_pool
from a2_api.db.repositories.jobs import JobRepository
from a2_api.workers.runner import Worker


async def run_worker(settings: RuntimeSettings, once: bool = False) -> None:
    if not settings.calculation_handler or not settings.code_version:
        raise ValueError("Worker requires A2_CALCULATION_HANDLER and A2_CODE_VERSION")
    pool = await open_pool(settings)
    service = None
    try:
        repository = JobRepository(pool)
        dispatch_groups = None
        if settings.contract_version != "legacy":
            from a2_api.api.bootstrap import build_analysis_service
            from a2_api.services.analysis_groups import AnalysisGroupService

            service = build_analysis_service(settings, repository)
            dispatch_groups = AnalysisGroupService(service).dispatch_once
        worker = Worker(
            repository,
            handler=settings.calculation_handler,
            code_version=settings.code_version,
            timeout_seconds=settings.job_timeout_seconds,
            decoded_result_bytes=settings.worker_result_decoded_mib * 1024 * 1024,
            dispatch_groups=dispatch_groups,
        )
        if once:
            await worker.run_once()
        else:
            await worker.run_forever(settings.worker_poll_seconds)
    finally:
        if service is not None:
            await service.close_admission()
        await pool.close()


async def worker_main(settings: RuntimeSettings, once: bool) -> None:
    task = asyncio.current_task()
    assert task is not None
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, task.cancel)
    try:
        await run_worker(settings, once)
    finally:
        loop.remove_signal_handler(signal.SIGTERM)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["worker"])
    parser.add_argument("--once", action="store_true", help="Process at most one queued job")
    args = parser.parse_args()
    try:
        asyncio.run(worker_main(RuntimeSettings(), args.once))
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    except Exception:
        parser.exit(1, "Worker failed; check database/migrations/handler configuration\n")


if __name__ == "__main__":
    main()
