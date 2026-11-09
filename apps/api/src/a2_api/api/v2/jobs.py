"""Canonical production HTTP boundary. Calculations run only in the worker."""

from a2_contracts.v2.api import (
    JobView,
)
from fastapi import APIRouter, Request

from a2_api.api.dependencies import backend

router = APIRouter()


@router.get("/jobs/{job_id}")
async def job(job_id: str, request: Request) -> JobView:
    return await backend(request).job(job_id, request.state.access_scope)


@router.post("/jobs/{job_id}/cancel")
async def cancel(job_id: str, request: Request) -> JobView:
    return await backend(request).cancel(job_id, request.state.access_scope)
