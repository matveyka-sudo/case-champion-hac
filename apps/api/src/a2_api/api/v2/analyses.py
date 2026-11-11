"""Canonical production HTTP boundary. Calculations run only in the worker."""

from a2_contracts.v2.api import (
    JobAccepted,
)
from a2_contracts.v2.requests import AnalysisRequest
from a2_contracts.v2.results import AnalysisResult
from fastapi import APIRouter, Request

from a2_api.api.dependencies import IdempotencyKey, backend

router = APIRouter()


@router.post("/analyses", status_code=202)
async def analyse(
    body: AnalysisRequest, request: Request, idempotency_key: IdempotencyKey
) -> JobAccepted:
    return await backend(request).analyse(
        body, idempotency_key, request.state.request_id, request.state.access_scope
    )


@router.get("/analyses/{analysis_id}")
async def analysis(analysis_id: str, request: Request) -> AnalysisResult:
    return await backend(request).analysis(analysis_id, request.state.access_scope)
