"""Canonical v2 routers, retaining /api/v1 URLs and shared error responses."""

from a2_contracts.v2.api import ErrorEnvelope
from fastapi import APIRouter

from a2_api.api.v2 import (
    address_access,
    analyses,
    analysis_groups,
    catalog,
    consultant,
    explanations,
    jobs,
    passenger_schedules,
    scenarios,
)
from a2_api.api.v2.examples import ERROR_EXAMPLES


def build_router() -> APIRouter:
    router = APIRouter(
        prefix="/api/v1",
        responses={
            code: {
                "model": ErrorEnvelope,
                **(
                    {"content": {"application/json": {"examples": ERROR_EXAMPLES[code]}}}
                    if code in ERROR_EXAMPLES
                    else {}
                ),
            }
            for code in (403, 404, 409, 422, 429, 500, 503)
        },
    )
    router.include_router(catalog.router)
    router.include_router(address_access.router)
    router.include_router(analyses.router)
    router.include_router(analysis_groups.router)
    router.include_router(jobs.router)
    router.include_router(explanations.router)
    router.include_router(consultant.router)
    router.include_router(scenarios.router)
    router.include_router(passenger_schedules.router)
    return router
