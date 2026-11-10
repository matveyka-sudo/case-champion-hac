"""Canonical production HTTP boundary. Calculations run only in the worker."""

from a2_contracts.v2.assistant import Explanation, ExplanationRequest
from fastapi import APIRouter, Request

from a2_api.api.dependencies import backend

router = APIRouter()


@router.post("/analyses/{analysis_id}/explanation")
async def explanation(
    analysis_id: str, request: Request, body: ExplanationRequest | None = None
) -> Explanation:
    return await backend(request).explain(
        analysis_id, request.state.access_scope, request.state.request_id, body
    )
