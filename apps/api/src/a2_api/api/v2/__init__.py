"""Canonical v2 routers, retaining /api/v1 URLs and shared error responses."""

from a2_contracts.v2.api import ErrorEnvelope
from fastapi import APIRouter

from a2_api.api.v2 import analyses, catalog, explanations, jobs, scenarios


def build_router() -> APIRouter:
    router = APIRouter(
        prefix="/api/v1",
        responses={code: {"model": ErrorEnvelope} for code in (403, 404, 409, 422, 429, 500, 503)},
    )
    router.include_router(catalog.router)
    router.include_router(analyses.router)
    router.include_router(jobs.router)
    router.include_router(explanations.router)
    router.include_router(scenarios.router)
    return router
