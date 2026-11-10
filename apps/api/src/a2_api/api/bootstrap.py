"""Operator-configured API database lifespan and the separate worker entrypoint."""

import importlib
import inspect
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import cast

from fastapi import FastAPI

from a2_api.core.catalog import load_catalog
from a2_api.core.config import RuntimeSettings
from a2_api.db.connection import open_pool
from a2_api.db.repositories.jobs import JobRepository
from a2_api.services.catalog import CatalogServices
from a2_api.services.jobs import Prepare, QueuedServices
from a2_api.services.legacy import Services


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
    from a2_api.api.legacy import create_app

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

    from a2_api.api.application import create_app
    from a2_api.core.registry import ProductRegistry
    from a2_api.services.analyses import AnalysisService, Preflight

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


def create_configured_app() -> FastAPI:
    """ASGI factory for Uvicorn and installed-package deployments."""
    return build_app()
