"""Canonical production HTTP boundary. Calculations run only in the worker."""

from a2_contracts.v2.api import (
    JobAccepted,
)
from a2_contracts.v2.scenarios import ScenarioProposal, ScenarioResult, ScenarioRunOptions
from fastapi import APIRouter, Request

from a2_api.api.dependencies import IdempotencyKey, backend

router = APIRouter()


@router.get("/analyses/{analysis_id}/scenarios")
async def proposals(analysis_id: str, request: Request) -> list[ScenarioProposal]:
    return await backend(request).proposals(analysis_id, request.state.access_scope)


@router.post("/scenarios/{scenario_id}/runs", status_code=202)
async def scenario(
    scenario_id: str,
    body: ScenarioRunOptions,
    request: Request,
    idempotency_key: IdempotencyKey,
) -> JobAccepted:
    return await backend(request).scenario(
        scenario_id, body, idempotency_key, request.state.request_id, request.state.access_scope
    )


@router.get("/scenario-runs/{run_id}")
async def scenario_result(run_id: str, request: Request) -> ScenarioResult:
    return await backend(request).scenario_result(run_id, request.state.access_scope)
