"""Operator-configured API database lifespan and the separate worker entrypoint."""

import argparse
import asyncio
import importlib
import inspect
import signal
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal, cast

import asyncpg  # type: ignore[import-untyped]
from fastapi import FastAPI
from pydantic import AliasChoices, Field
from pydantic_settings import SettingsConfigDict

from .configuration import CatalogServices, load_catalog
from .env_settings import WaydexSettings
from .job_services import Prepare, QueuedServices
from .services import Services
from .storage import JobRepository
from .worker import Worker


class RuntimeSettings(WaydexSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="A2_",
        extra="ignore",
        hide_input_in_errors=True,
        populate_by_name=True,
    )
    alias_pairs = (
        ("DATABASE_URL", "A2_DATABASE_URL"),
        ("DATABASE_SCHEMA", "A2_DATABASE_SCHEMA"),
        ("ASSISTANT_HOOK", "A2_ASSISTANT_HOOK"),
    )
    contract_version: Literal["2", "legacy"] = "2"
    registry_hook: str | None = None
    assistant_hook: str | None = Field(
        default=None, validation_alias=AliasChoices("ASSISTANT_HOOK", "A2_ASSISTANT_HOOK")
    )
    secure_cookies: bool = False
    database_url: str | None = Field(
        default=None, repr=False, validation_alias=AliasChoices("DATABASE_URL", "A2_DATABASE_URL")
    )
    database_schema: Annotated[
        str,
        Field(
            pattern=r"^[a-z][a-z0-9_]{0,62}$",
            validation_alias=AliasChoices("DATABASE_SCHEMA", "A2_DATABASE_SCHEMA"),
        ),
    ] = "a2_backend"
    catalog_path: Path | None = None
    code_version: str | None = None
    preflight_hook: str | None = None
    calculation_handler: str | None = None
    job_timeout_seconds: Annotated[float, Field(gt=0, le=600)] = 600
    worker_poll_seconds: Annotated[float, Field(gt=0, le=60)] = 0.5


async def open_pool(settings: RuntimeSettings) -> asyncpg.Pool:
    if settings.database_url is None:
        raise ValueError("DATABASE_URL is required")
    url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    pool = await asyncpg.create_pool(
        url,
        min_size=1,
        max_size=5,
        command_timeout=10,
        timeout=10,
        server_settings={"search_path": settings.database_schema, "application_name": "a2-backend"},
    )
    assert pool is not None
    try:
        if (
            await pool.fetchval("SELECT to_regclass('jobs')") is None
            or await pool.fetchval("SELECT to_regclass('scenario_proposals')") is None
            or await pool.fetchval("SELECT to_regclass('evidence_chunks')") is None
            or await pool.fetchval("SELECT to_regclass('assistant_budget')") is None
            or await pool.fetchval("SELECT to_regclass('assistant_reservations')") is None
        ):
            raise ValueError("Apply backend Alembic migrations before starting API/worker")
    except BaseException:
        await pool.close()
        raise
    return pool


def load_prepare(settings: RuntimeSettings) -> Prepare | None:
    if not settings.preflight_hook and not settings.calculation_handler:
        return None
    if not settings.preflight_hook or not settings.calculation_handler or not settings.code_version:
        raise ValueError(
            "Enable calculations with preflight hook, handler and code version together"
        )
    module, name = settings.preflight_hook.split(":", 1)
    hook = getattr(importlib.import_module(module), name)
    if not inspect.iscoroutinefunction(hook):
        raise ValueError("Preflight must be an async server-side function")
    return cast(Prepare, hook)


def build_legacy_app(settings: RuntimeSettings | None = None) -> FastAPI:
    from .app import create_app

    config = settings if settings is not None else RuntimeSettings()
    catalog = load_catalog(config.catalog_path) if config.catalog_path else None
    metadata: Services = CatalogServices(catalog) if catalog else Services()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if config.database_url is None:
            yield
            return
        try:
            pool = await open_pool(config)
        except Exception:
            raise RuntimeError(
                "Backend database startup failed; check migrations/configuration"
            ) from None
        try:
            app.state.services = QueuedServices(
                JobRepository(pool),
                catalog=catalog,
                prepare=load_prepare(config),
                code_version=config.code_version,
            )
            yield
        finally:
            await pool.close()

    return create_app(metadata, lifespan=lifespan)


def build_app(settings: RuntimeSettings | None = None) -> FastAPI:
    """Canonical default; legacy deployment requires explicit operator selection."""
    from a2_assistant import FlashAssistant

    from .analysis_service import AnalysisService, Preflight
    from .api_v2 import create_app
    from .product_registry import ProductRegistry

    config = settings if settings is not None else RuntimeSettings()
    if config.contract_version == "legacy":
        return build_legacy_app(config)

    def configured_hook(path: str) -> object:
        module, name = path.split(":", 1)
        return getattr(importlib.import_module(module), name)

    registry = ProductRegistry()
    if config.registry_hook:
        factory = cast(Callable[[], ProductRegistry], configured_hook(config.registry_hook))
        registry = factory()
        if not isinstance(registry, ProductRegistry):
            raise ValueError("Registry hook must return ProductRegistry")
    preflight: Preflight | None = None
    if config.preflight_hook:
        hook = configured_hook(config.preflight_hook)
        if not inspect.iscoroutinefunction(hook):
            raise ValueError("Preflight must be an async server-side function")
        preflight = cast(Preflight, hook)
    if bool(config.calculation_handler) != bool(config.code_version):
        raise ValueError("Configure calculation handler and code version together")

    def service(
        repository: JobRepository | None = None, *, configure_assistant: bool = True
    ) -> AnalysisService:
        result = AnalysisService(
            registry,
            repository,
            code_version=config.code_version,
            engine_enabled=bool(config.calculation_handler),
            preflight=preflight,
        )
        if config.assistant_hook and configure_assistant:
            factory = cast(
                Callable[[AnalysisService], FlashAssistant], configured_hook(config.assistant_hook)
            )
            result.assistant = factory(result)
            if not isinstance(result.assistant, FlashAssistant):
                raise ValueError("Assistant hook must return FlashAssistant")
        return result

    async def close_assistant(result: AnalysisService) -> None:
        transport = result.assistant.transport if result.assistant is not None else None
        close = getattr(transport, "aclose", None)
        if callable(close):
            await cast(Callable[[], Awaitable[None]], close)()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if config.database_url is None:
            yield
            return
        try:
            pool = await open_pool(config)
        except Exception:
            raise RuntimeError(
                "Backend database startup failed; check migrations/configuration"
            ) from None
        try:
            active_service = service(JobRepository(pool))
            app.state.services = active_service
            try:
                yield
            finally:
                await close_assistant(active_service)
        finally:
            await pool.close()

    return create_app(
        service(configure_assistant=False), lifespan=lifespan, secure_cookies=config.secure_cookies
    )


async def run_worker(settings: RuntimeSettings, once: bool = False) -> None:
    if not settings.calculation_handler or not settings.code_version:
        raise ValueError("Worker requires A2_CALCULATION_HANDLER and A2_CODE_VERSION")
    pool = await open_pool(settings)
    try:
        worker = Worker(
            JobRepository(pool),
            handler=settings.calculation_handler,
            code_version=settings.code_version,
            timeout_seconds=settings.job_timeout_seconds,
        )
        if once:
            await worker.run_once()
        else:
            await worker.run_forever(settings.worker_poll_seconds)
    finally:
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
