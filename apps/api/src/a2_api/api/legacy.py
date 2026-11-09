"""FastAPI boundary. Use create_app(services) to connect real domain adapters."""

import logging
from collections.abc import Awaitable, Callable
from typing import Annotated, Literal, cast
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]
from a2_contracts.api import (
    AnalysisRequest,
    ErrorDetail,
    ErrorEnvelope,
    FieldError,
    Health,
    Job,
    JobAccepted,
    Place,
)
from a2_contracts.dataset import Coverage, Destination, Evidence, Source
from a2_contracts.journeys import JourneyRequest, JourneyResult
from a2_contracts.profiles import ProfileConfig
from a2_contracts.regions import StudyArea
from a2_contracts.results import ProfileResult
from a2_contracts.scenarios import Comparison, ScenarioRequest
from fastapi import APIRouter, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.types import Lifespan

from a2_api.core.errors import DomainError
from a2_api.services.legacy import Services

BundleQuery = Annotated[str, Query(min_length=1, max_length=200)]
IdempotencyKey = Annotated[str, Header(min_length=1, max_length=200)]
logger = logging.getLogger("a2_api")
API_CONTRACT_VERSION = "0.2.0"


def error_response(request: Request, error: DomainError) -> JSONResponse:
    trace_id = request.state.trace_id
    body = ErrorEnvelope(
        error=ErrorDetail(
            code=error.code,
            message=error.message,
            field_errors=error.field_errors,
            retryable=error.retryable,
            trace_id=trace_id,
        )
    )
    return JSONResponse(
        status_code=error.status_code,
        content=body.model_dump(mode="json"),
        headers={"X-Trace-ID": trace_id},
    )


def create_app(
    services: Services | None = None, *, lifespan: Lifespan[FastAPI] | None = None
) -> FastAPI:
    app = FastAPI(title="A2 accessibility API", version=API_CONTRACT_VERSION, lifespan=lifespan)
    app.state.services = services if services is not None else Services()

    def backend() -> Services:
        return cast(Services, app.state.services)

    @app.middleware("http")
    async def trace(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.trace_id = str(uuid4())
        try:
            response = await call_next(request)
        except (asyncpg.PostgresConnectionError, ConnectionError, TimeoutError):
            return error_response(
                request,
                DomainError(
                    503,
                    "engine_unavailable",
                    "Backend database temporarily unavailable",
                    retryable=True,
                ),
            )
        except Exception:
            logger.error("request_failed trace_id=%s", request.state.trace_id)
            return error_response(
                request, DomainError(500, "internal_error", "Internal service error")
            )
        response.headers["X-Trace-ID"] = request.state.trace_id
        return response

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError) -> JSONResponse:
        return error_response(request, exc)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [
            FieldError(
                location=[str(part) for part in item["loc"]],
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
        if exc.status_code == 404:
            error = DomainError(404, "unknown_id", "Resource not found")
        elif exc.status_code == 405:
            error = DomainError(405, "method_not_allowed", "Method not allowed")
        else:
            error = DomainError(exc.status_code, "invalid_request", "HTTP request rejected")
        return error_response(request, error)

    @app.get("/health", response_model=Health)
    async def health() -> Health:
        return Health(status="ok")

    router = APIRouter(
        prefix="/api/v1",
        responses={
            404: {"model": ErrorEnvelope},
            409: {"model": ErrorEnvelope},
            422: {"model": ErrorEnvelope},
            429: {"model": ErrorEnvelope},
            500: {"model": ErrorEnvelope},
            503: {"model": ErrorEnvelope},
        },
    )

    @router.get("/profiles")
    async def profiles() -> list[ProfileConfig]:
        return await backend().profiles()

    @router.get("/study-areas")
    async def study_areas() -> list[StudyArea]:
        return await backend().study_areas()

    @router.get("/places")
    async def places(
        bundle_id: BundleQuery, query: Annotated[str | None, Query(max_length=200)] = None
    ) -> list[Place]:
        return await backend().places(bundle_id, query)

    @router.get("/destinations")
    async def destinations(bundle_id: BundleQuery) -> list[Destination]:
        return await backend().destinations(bundle_id)

    @router.get("/coverage")
    async def coverage(bundle_id: BundleQuery) -> Coverage:
        return await backend().coverage(bundle_id)

    @router.get("/sources")
    async def sources(bundle_id: BundleQuery) -> list[Source]:
        return await backend().sources(bundle_id)

    @router.get("/evidence")
    async def evidence(bundle_id: BundleQuery) -> list[Evidence]:
        return await backend().evidence(bundle_id)

    @router.get("/evidence/{evidence_id}")
    async def evidence_record(evidence_id: str, bundle_id: BundleQuery) -> Evidence:
        return await backend().evidence_record(evidence_id, bundle_id)

    @router.post("/journeys")
    async def journeys(body: JourneyRequest) -> JourneyResult:
        return await backend().journeys(body)

    @router.post("/analyses", status_code=202)
    async def analyses(
        body: AnalysisRequest, request: Request, idempotency_key: IdempotencyKey
    ) -> JobAccepted:
        return await backend().analyse(body, idempotency_key, request.state.trace_id)

    @router.post("/scenario-analyses", status_code=202)
    async def scenarios(
        body: ScenarioRequest, request: Request, idempotency_key: IdempotencyKey
    ) -> JobAccepted:
        return await backend().scenario(body, idempotency_key, request.state.trace_id)

    @router.get("/jobs/{job_id}")
    async def job(job_id: str) -> Job:
        return await backend().job(job_id)

    @router.get("/runs/{run_id}")
    async def run(run_id: str) -> ProfileResult | Comparison:
        return await backend().run(run_id)

    @router.get(
        "/runs/{run_id}/export",
        response_class=Response,
        responses={
            200: {"content": {"application/json": {}, "text/csv": {}}},
        },
    )
    async def export(run_id: str, format: Literal["json", "csv"] = "json") -> Response:
        media_type, content = await backend().export(run_id, format)
        # Generated fixed filename avoids echoing user-controlled IDs into HTTP headers.
        return Response(
            content,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="a2-result.{format}"'},
        )

    app.include_router(router)
    return app
