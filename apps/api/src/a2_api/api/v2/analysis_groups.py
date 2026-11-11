"""Category groups share the session, worker and canonical analysis endpoints."""

from a2_contracts.v2.api import AnalysisGroupView
from a2_contracts.v2.requests import UrbanPassengerRequest
from fastapi import APIRouter, Request

from a2_api.api.dependencies import IdempotencyKey, backend
from a2_api.services.analysis_groups import AnalysisGroupService

router = APIRouter()


@router.post("/analysis-groups", status_code=202)
async def create(
    body: UrbanPassengerRequest, request: Request, idempotency_key: IdempotencyKey
) -> AnalysisGroupView:
    return await AnalysisGroupService(backend(request)).create(
        body, idempotency_key, request.state.access_scope, request.state.request_id
    )


@router.get("/analysis-groups/{group_id}")
async def get(group_id: str, request: Request) -> AnalysisGroupView:
    return await AnalysisGroupService(backend(request)).get(
        group_id, request.state.access_scope, request.state.request_id
    )


@router.post("/analysis-groups/{group_id}/cancel")
async def cancel(group_id: str, request: Request) -> AnalysisGroupView:
    return await AnalysisGroupService(backend(request)).cancel(
        group_id, request.state.access_scope, request.state.request_id
    )


@router.post("/analysis-groups/{group_id}/retry", status_code=202)
async def retry(
    group_id: str, request: Request, idempotency_key: IdempotencyKey
) -> AnalysisGroupView:
    return await AnalysisGroupService(backend(request)).retry(
        group_id, idempotency_key, request.state.access_scope, request.state.request_id
    )
