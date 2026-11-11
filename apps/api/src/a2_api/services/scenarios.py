"""Scoped orchestration for canonical analyses. No formulas or routing in HTTP."""

from __future__ import annotations

from typing import TYPE_CHECKING

from a2_contracts.common import canonical_hash
from a2_contracts.v2.api import JobAccepted
from a2_contracts.v2.requests import UrbanPassengerRequest
from a2_contracts.v2.scenarios import (
    ScenarioProposal,
    ScenarioResult,
    ScenarioRunOptions,
    ScenarioRunRequest,
)

from a2_api.core.errors import DomainError
from a2_api.core.jobs import V2JobContext
from a2_api.core.scenario_scope import build_scenario_endpoint_scope

if TYPE_CHECKING:
    from a2_api.services.analyses import AnalysisService


async def proposals(
    service: AnalysisService, analysis_id: str, scope: str
) -> list[ScenarioProposal]:
    await service.analysis(analysis_id, scope)
    return [
        p
        for p in await service._repository().get_proposals(analysis_id, scope)
        if p.template_id in service.registered_template_ids
    ]


async def scenario(
    service: AnalysisService,
    scenario_id: str,
    options: ScenarioRunOptions,
    key: str,
    request_id: str,
    scope: str,
) -> JobAccepted:
    repository = service._repository()
    proposal = await repository.get_proposal(scenario_id, scope)
    baseline = await service.analysis(proposal.baseline_analysis_id, scope)
    request = ScenarioRunRequest(
        schema_version=options.schema_version,
        scenario_id=scenario_id,
        baseline_analysis_id=baseline.analysis_id,
    )
    scoped_key = service._key(key, scope)
    replay = await repository.replay("scenario", request, scoped_key, scope=scope)
    if replay is not None:
        return JobAccepted(job_id=replay.job_id, status=replay.status, request_id=request_id)
    if proposal.template_id not in service.registered_template_ids:
        raise DomainError(422, "unsupported_scenario", "Scenario template is not enabled")
    if not proposal.editable_local_graph:
        raise DomainError(409, "unsupported_scenario", "Scenario requires an editable local graph")
    if (
        proposal.versions != baseline.versions
        or proposal.baseline_result_hash != baseline.result_hash
    ):
        raise DomainError(409, "incompatible_versions", "Scenario baseline has changed")
    service._engine(baseline.analysis_kind)
    if baseline.versions.code_version != service.code_version:
        raise DomainError(
            409, "incompatible_versions", "Scenario requires its baseline engine revision"
        )
    parameters = await repository.get_run_parameters(baseline.analysis_id, scope=scope)
    context = V2JobContext.model_validate(parameters["context"])
    if context.scenario_proposal is not None:
        raise DomainError(409, "incompatible_versions", "Scenario requires an analysis baseline")
    endpoint_scope = None
    if isinstance(baseline.request, UrbanPassengerRequest):
        snapshot = await service.accepted_snapshot(baseline, scope)
        if (
            snapshot is None
            or canonical_hash(snapshot.model_dump(mode="json")) != context.snapshot_hash
        ):
            raise DomainError(
                409, "incompatible_versions", "Scenario accepted snapshot is unavailable or changed"
            )
        endpoint_scope = build_scenario_endpoint_scope(snapshot, baseline.request)
    payload = context.model_dump(mode="json")
    payload.update(
        scenario_proposal=proposal.model_dump(mode="json"),
        scenario_endpoint_scope=endpoint_scope.model_dump(mode="json")
        if endpoint_scope is not None
        else None,
    )
    if "batch_execution" in V2JobContext.model_fields:
        payload["batch_execution"] = None
    context = V2JobContext.model_validate(payload)
    accepted = await repository.enqueue(
        "scenario", request, context, scoped_key, request_id, scope=scope
    )
    return JobAccepted(job_id=accepted.job_id, status=accepted.status, request_id=request_id)


async def scenario_result(service: AnalysisService, run_id: str, scope: str) -> ScenarioResult:
    result = await service._repository().get_run(run_id, scope=scope)
    if not isinstance(result, ScenarioResult):
        raise DomainError(404, "unknown_id", "Scenario run not found")
    return result
