"""Transactional PostgreSQL jobs and immutable runs; no ORM at runtime."""

from a2_contracts.api import AnalysisRequest
from a2_contracts.common import canonical_hash
from a2_contracts.results import ProfileResult
from a2_contracts.scenarios import Comparison, ScenarioRequest
from a2_contracts.v2.requests import RequestBase
from a2_contracts.v2.results import (
    AnalysisResult,
    validate_reference_applicability,
    validate_score_context,
)
from a2_contracts.v2.scenarios import (
    ScenarioProposal,
    ScenarioResult,
    ScenarioRunRequest,
    _measure_key,
)

from a2_api.core.errors import DomainError
from a2_api.core.jobs import (
    ClaimedJob,
    JobContext,
    JobContextUnion,
    JobKind,
    JobRequest,
)
from a2_api.db.rows import RunResult


def _check_result_context(claim: ClaimedJob, result: RunResult) -> None:
    if not isinstance(claim.context, JobContext) or not isinstance(
        result, (ProfileResult, Comparison)
    ):
        raise DomainError(409, "incompatible_versions", "Legacy result type differs from job")
    if (
        result.bundle_id != claim.context.bundle_id
        or result.code_version != claim.context.code_version
    ):
        raise DomainError(
            409, "incompatible_versions", "Result bundle/code differs from accepted input"
        )
    if isinstance(claim.request, AnalysisRequest):
        request = claim.request
        if not isinstance(result, ProfileResult) or (
            result.profile_id != request.profile_id
            or result.profile_version != request.profile_version
            or result.task_set_hash != request.task_set_hash
        ):
            raise DomainError(
                409, "incompatible_versions", "Result profile/task differs from request"
            )
        if result.status != "insufficient_data" and {c.origin_id for c in result.cities} != set(
            request.origin_ids
        ):
            raise DomainError(409, "incompatible_versions", "Result origins differ from request")
    elif isinstance(claim.request, ScenarioRequest):
        if not isinstance(result, Comparison) or result.base_run_ids != claim.request.base_run_ids:
            raise DomainError(
                409, "incompatible_versions", "Scenario result baselines differ from request"
            )
        operations = [op.model_dump(mode="json") for op in claim.request.operations]
        if [
            op.model_dump(mode="json") for op in result.operations
        ] != operations or result.operations_hash != canonical_hash(operations):
            raise DomainError(
                409, "incompatible_versions", "Scenario result operations differ from request"
            )
        for entry in result.comparisons:
            if entry.profile_version != claim.context.profile_versions.get(
                entry.profile_id
            ) or entry.task_set_hash != claim.context.task_hashes.get(entry.profile_id):
                raise DomainError(
                    409,
                    "incompatible_versions",
                    "Scenario result versions differ from accepted context",
                )
        for profile in claim.request.evaluate_profile_ids:
            origins = {
                entry.origin_id
                for entry in result.comparisons
                if entry.profile_id == profile and entry.origin_id is not None
            }
            if origins != set(claim.context.origin_ids[profile]):
                raise DomainError(
                    409, "incompatible_versions", "Scenario result origins differ from baseline"
                )
    else:
        raise DomainError(409, "incompatible_versions", "Legacy request type differs from job")


def _check_submission(
    kind: JobKind, request: JobRequest, context: JobContextUnion, scope: str | None
) -> None:
    if isinstance(context, JobContext):
        if scope is not None:
            raise DomainError(404, "unknown_id", "Legacy operation not found")
        if not isinstance(request, (AnalysisRequest, ScenarioRequest)):
            raise DomainError(422, "invalid_request", "Legacy context requires a legacy request")
        return
    if scope is not None and scope != context.access_scope:
        raise DomainError(404, "unknown_id", "Resource not found")
    if kind == "scenario":
        if not isinstance(request, ScenarioRunRequest) or context.scenario_proposal is None:
            raise DomainError(422, "invalid_request", "Scenario requires its saved proposal")
        if (
            request.baseline_analysis_id != context.analysis_id
            or request.scenario_id != context.scenario_proposal.scenario_id
        ):
            raise DomainError(409, "incompatible_versions", "Scenario command differs from context")
    elif not isinstance(request, RequestBase) or context.scenario_proposal is not None:
        raise DomainError(422, "invalid_request", "Analysis requires an analysis request")
    else:
        normalized = context.normalized_request
        if (
            request.model_dump(mode="json", exclude={"snapshot_id", "methodology"})
            != normalized.model_dump(mode="json", exclude={"snapshot_id", "methodology"})
            or (request.snapshot_id is not None and request.snapshot_id != normalized.snapshot_id)
            or (request.methodology is not None and request.methodology != normalized.methodology)
        ):
            raise DomainError(
                409, "incompatible_versions", "Normalized request changes user parameters"
            )


def _validate_scenario_semantics(
    result: ScenarioResult, baseline: AnalysisResult, command: ScenarioProposal
) -> None:
    if (
        result.versions != baseline.versions
        or command.versions != baseline.versions
        or command.baseline_analysis_id != baseline.analysis_id
        or command.baseline_result_hash != baseline.result_hash
    ):
        raise DomainError(409, "incompatible_versions", "Scenario baseline versions/hash differ")
    try:
        validate_score_context(result.after.metrics, result.after.scores, baseline.analysis_kind)
    except ValueError:
        raise DomainError(
            409, "incompatible_versions", "Scenario index weights are incomplete"
        ) from None
    references = {item.reference_id: item for item in baseline.references}
    baseline_metrics = {item.metric_id: item for item in baseline.metrics}
    after_metrics = {item.metric_id: item for item in result.after.metrics}
    baseline_scores = {item.score_id: item for item in baseline.scores}
    journeys = {item.journey_id: item for item in baseline.journeys}
    renamed = {
        effect.after_id: effect.before_id
        for effect in result.deltas
        if effect.kind == "metric" and effect.before_id and effect.after_id
    }
    renamed_scores = {
        effect.after_id: effect.before_id
        for effect in result.deltas
        if effect.kind == "score" and effect.before_id and effect.after_id
    }
    after_scores = {item.score_id: item for item in result.after.scores}
    evaluated_metrics: dict[str, set[str]] = {}

    def operand_metric_ids(score_id: str) -> set[str]:
        # ScenarioValues already checked that the component graph is resolved
        # and acyclic. Resolve the same transitive operands for every score.
        if score_id not in evaluated_metrics:
            linked: set[str] = set()
            for component in after_scores[score_id].components:
                if component.kind == "metric":
                    linked.add(component.id)
                else:
                    linked.update(operand_metric_ids(component.id))
            evaluated_metrics[score_id] = linked
        return evaluated_metrics[score_id]

    for score in result.after.scores:
        full = score.status == "available" and score.completeness == "full"
        required_references = set(score.reference_ids)
        previous_score = baseline_scores.get(score.score_id) or baseline_scores.get(
            renamed_scores.get(score.score_id, "")
        )
        if previous_score is None:
            matches_scores = [
                item
                for item in baseline.scores
                if _measure_key(
                    item.model_copy(
                        update={
                            "completeness": score.completeness,
                            "calculation_kind": score.calculation_kind,
                        }
                    )
                )
                == _measure_key(score)
            ]
            previous_score = matches_scores[0] if len(matches_scores) == 1 else None
        if previous_score is not None and _measure_key(
            previous_score.model_copy(
                update={
                    "completeness": score.completeness,
                    "calculation_kind": score.calculation_kind,
                }
            )
        ) == _measure_key(score):
            # A new declaration must not erase the frozen operands of this task.
            required_references.update(previous_score.reference_ids)
        try:
            score.validate_calculation(baseline.analysis_kind)
        except ValueError:
            raise DomainError(
                409, "incompatible_versions", "Scenario index semantics are unavailable"
            ) from None
        applicability_journeys = []
        for metric_id in sorted(operand_metric_ids(score.score_id)):
            metric = after_metrics[metric_id]
            previous = baseline_metrics.get(metric.metric_id) or baseline_metrics.get(
                renamed.get(metric.metric_id, "")
            )
            if previous is None:
                matches = [
                    item for item in baseline.metrics if _measure_key(item) == _measure_key(metric)
                ]
                previous = matches[0] if len(matches) == 1 else None
            supplied_journey = journeys.get(metric.journey_id or "")
            frozen_journey = (
                journeys.get(previous.journey_id or "") if previous is not None else None
            )
            if supplied_journey is not None and frozen_journey is not None:
                frozen_reference = references.get(frozen_journey.reference_id or "")
                origins = (
                    frozen_reference.boundary_node_sets.origin_node_ids
                    if frozen_reference
                    else [frozen_journey.origin_node_id]
                )
                destinations = (
                    frozen_reference.boundary_node_sets.destination_node_ids
                    if frozen_reference
                    else [frozen_journey.destination_node_id]
                )
                if (
                    supplied_journey.origin_node_id not in origins
                    or supplied_journey.destination_node_id not in destinations
                ):
                    raise DomainError(
                        409, "incompatible_versions", "Scenario journey belongs to another task"
                    )
            # Check both: a changed journey ID cannot replace the original
            # task's frozen reference, even with another complete baseline journey.
            dependency_journeys = [
                item for item in (frozen_journey, supplied_journey) if item is not None
            ]
            if full and score.calculation_kind == "initial_wait" and not dependency_journeys:
                raise DomainError(
                    409, "incompatible_versions", "Scenario wait operand is unavailable"
                )
            applicability_journeys.extend(dependency_journeys)
            for journey in dependency_journeys:
                if full and (journey.status != "available" or journey.completeness != "full"):
                    raise DomainError(
                        409,
                        "incompatible_versions",
                        "Scenario journey dependency is unavailable",
                    )
                if score.calculation_kind == "reference_ratio" and journey.reference_id is not None:
                    required_references.add(journey.reference_id)
        if full and score.calculation_kind == "reference_ratio" and not required_references:
            raise DomainError(
                409, "incompatible_versions", "Scenario reference operand is unavailable"
            )
        if (
            full
            and score.calculation_kind == "delivery_deadline"
            and not any(
                component.kind == "metric"
                and after_metrics[component.id].time_kind == "complete_chain"
                for component in score.components
            )
        ):
            raise DomainError(
                409, "incompatible_versions", "Scenario delivery operand is incomplete"
            )
        for identifier in required_references:
            reference = references.get(identifier)
            if reference is None or (
                full and (reference.status != "available" or reference.completeness != "full")
            ):
                raise DomainError(
                    409, "incompatible_versions", "Scenario index reference is unavailable"
                )
            if full:
                try:
                    validate_reference_applicability(
                        reference, baseline.request, applicability_journeys
                    )
                except ValueError:
                    raise DomainError(
                        409,
                        "incompatible_versions",
                        "Scenario reference does not apply to the task",
                    ) from None
    if (
        result.baseline_analysis_id != baseline.analysis_id
        or result.scenario_id != command.scenario_id
        or result.baseline_result_hash != baseline.result_hash
    ):
        raise DomainError(409, "incompatible_versions", "Scenario result baseline identity differs")
    before_metrics = {item.metric_id: item for item in result.before.metrics}
    before_scores = {item.score_id: item for item in result.before.scores}
    if before_metrics != {item.metric_id: item for item in baseline.metrics} or before_scores != {
        item.score_id: item for item in baseline.scores
    }:
        raise DomainError(
            409, "incompatible_versions", "Scenario before differs from saved baseline"
        )
    expected = {
        service_id: command.fixed_parameters.shift_seconds
        for service_id in command.fixed_parameters.service_ids
    }
    actual = {change.service_id: change.shift_seconds for change in result.service_changes}
    if (
        actual != expected
        or result.feasibility != command.feasibility
        or result.editable_local_graph != command.editable_local_graph
        or result.turnaround_evidence != command.fixed_parameters.turnaround_evidence
        or result.resource_evidence != command.fixed_parameters.resource_evidence
        or not set(command.assumptions) <= set(result.assumptions)
        or not set(command.limitations) <= set(result.limitations)
    ):
        raise DomainError(
            409, "incompatible_versions", "Scenario result changes frozen operation/constraints"
        )


def _operation_hash(proposal: ScenarioProposal) -> str:
    return canonical_hash(
        {
            "operation_type": proposal.operation_type,
            "fixed_parameters": proposal.fixed_parameters.model_dump(mode="json"),
        }
    )
