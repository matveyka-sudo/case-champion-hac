"""Canonical production HTTP boundary. Calculations run only in the worker."""

import hashlib
import logging
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from typing import Annotated, Literal, cast
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]
from a2_contracts.api import FieldError, Health
from a2_contracts.v2.api import (
    Capabilities,
    ErrorDetail,
    ErrorEnvelope,
    JobAccepted,
    JobView,
    TerritoryOption,
)
from a2_contracts.v2.assistant import Explanation
from a2_contracts.v2.requests import AnalysisRequest
from a2_contracts.v2.results import AnalysisResult
from a2_contracts.v2.scenarios import ScenarioProposal, ScenarioResult, ScenarioRunOptions
from fastapi import APIRouter, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.types import Lifespan

from .analysis_service import AnalysisService
from .services import DomainError

API_CONTRACT_VERSION = "2.0.0"
IdempotencyKey = Annotated[str, Header(min_length=1, max_length=200)]
logger = logging.getLogger("a2_api")


def error_response(request: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorEnvelope(
            error=ErrorDetail(
                code=exc.code,
                message=exc.message,
                field_errors=exc.field_errors,
                request_id=request.state.request_id,
                retryable=exc.retryable,
            )
        ).model_dump(mode="json"),
    )


def create_app(
    services: AnalysisService | None = None,
    *,
    lifespan: Lifespan[FastAPI] | None = None,
    secure_cookies: bool = False,
) -> FastAPI:
    app = FastAPI(title="A2 accessibility API", version=API_CONTRACT_VERSION, lifespan=lifespan)
    app.state.services = services or AnalysisService()

    def backend() -> AnalysisService:
        return cast(AnalysisService, app.state.services)

    @app.middleware("http")
    async def boundary(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.request_id = str(uuid4())
        started = time.monotonic()
        token = request.cookies.get("a2_session", "")
        fresh = re.fullmatch(r"[A-Za-z0-9_-]{43}", token) is None
        if fresh:
            token = secrets.token_urlsafe(32)
        request.state.access_scope = hashlib.sha256(token.encode()).hexdigest()
        # Mutations use same-origin session cookies. A cross-origin Origin is never accepted.
        origin = request.headers.get("origin")
        if (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and origin is not None
            and origin.rstrip("/") != str(request.base_url).rstrip("/")
        ):
            response: Response = error_response(
                request, DomainError(403, "invalid_request", "Cross-origin request rejected")
            )
        else:
            try:
                response = await call_next(request)
            except (asyncpg.PostgresConnectionError, ConnectionError, TimeoutError):
                response = error_response(
                    request,
                    DomainError(
                        503, "engine_unavailable", "Backend temporarily unavailable", retryable=True
                    ),
                )
            except Exception:
                logger.error("request_failed request_id=%s", request.state.request_id)
                response = error_response(
                    request, DomainError(500, "internal_error", "Internal service error")
                )
        if fresh:
            response.set_cookie(
                "a2_session",
                token,
                httponly=True,
                secure=secure_cookies,
                samesite="strict",
                path="/",
                max_age=30 * 24 * 60 * 60,
            )
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["Cache-Control"] = "no-store"
        logger.info(
            "request request_id=%s method=%s status=%s elapsed_ms=%s",
            request.state.request_id,
            request.method,
            response.status_code,
            round((time.monotonic() - started) * 1000),
        )
        return response

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError) -> JSONResponse:
        return error_response(request, exc)

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [
            FieldError(
                location=[str(p) for p in item["loc"]],
                code=item["type"],
                message="Invalid request field",
            )
            for item in exc.errors()
        ]
        return error_response(
            request,
            DomainError(422, "invalid_request", "Request validation failed", field_errors=fields),
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        code: Literal["unknown_id", "method_not_allowed", "invalid_request"] = (
            "unknown_id"
            if exc.status_code == 404
            else "method_not_allowed"
            if exc.status_code == 405
            else "invalid_request"
        )
        return error_response(request, DomainError(exc.status_code, code, "HTTP request rejected"))

    @app.get("/health")
    async def health() -> Health:
        return Health(status="ok")

    router = APIRouter(
        prefix="/api/v1",
        responses={code: {"model": ErrorEnvelope} for code in (403, 404, 409, 422, 429, 500, 503)},
    )

    @router.get("/capabilities")
    async def capabilities() -> Capabilities:
        return backend().capabilities()

    @router.get("/territories")
    async def territories(
        level: Literal["city", "region"] | None = None,
        query: Annotated[str | None, Query(max_length=200)] = None,
    ) -> list[TerritoryOption]:
        return backend().registry.territories(level, query)

    @router.post("/analyses", status_code=202)
    async def analyse(
        body: AnalysisRequest, request: Request, idempotency_key: IdempotencyKey
    ) -> JobAccepted:
        return await backend().analyse(
            body, idempotency_key, request.state.request_id, request.state.access_scope
        )

    @router.get("/jobs/{job_id}")
    async def job(job_id: str, request: Request) -> JobView:
        return await backend().job(job_id, request.state.access_scope)

    @router.post("/jobs/{job_id}/cancel")
    async def cancel(job_id: str, request: Request) -> JobView:
        return await backend().cancel(job_id, request.state.access_scope)

    @router.get("/analyses/{analysis_id}")
    async def analysis(analysis_id: str, request: Request) -> AnalysisResult:
        return await backend().analysis(analysis_id, request.state.access_scope)

    @router.post("/analyses/{analysis_id}/explanation")
    async def explanation(analysis_id: str, request: Request) -> Explanation:
        return await backend().explain(
            analysis_id, request.state.access_scope, request.state.request_id
        )

    @router.get("/analyses/{analysis_id}/scenarios")
    async def proposals(analysis_id: str, request: Request) -> list[ScenarioProposal]:
        return await backend().proposals(analysis_id, request.state.access_scope)

    @router.post("/scenarios/{scenario_id}/runs", status_code=202)
    async def scenario(
        scenario_id: str,
        body: ScenarioRunOptions,
        request: Request,
        idempotency_key: IdempotencyKey,
    ) -> JobAccepted:
        return await backend().scenario(
            scenario_id, body, idempotency_key, request.state.request_id, request.state.access_scope
        )

    @router.get("/scenario-runs/{run_id}")
    async def scenario_result(run_id: str, request: Request) -> ScenarioResult:
        return await backend().scenario_result(run_id, request.state.access_scope)

    app.include_router(router)
    return app
