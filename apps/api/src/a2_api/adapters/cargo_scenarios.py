"""Hash-pinned processing experiments over accepted cargo actions.

Only the public engine evaluates the changed graph. Urban truck accessibility
does not contain processing times and is deliberately unsupported here.
"""

from dataclasses import replace
from typing import Literal, cast
from uuid import NAMESPACE_URL, uuid5

from a2_contracts.common import canonical_hash
from a2_contracts.v2.base import ContractModel, Identifier, Timestamp
from a2_contracts.v2.requests import AnalysisRequest, IntercityCargoRequest
from a2_contracts.v2.results import AnalysisResult, EvidenceRef, Metric, Score
from a2_contracts.v2.scenarios import (
    CargoHandlingChange,
    FixedParameters,
    ScenarioDelta,
    ScenarioProposal,
    ScenarioResult,
    ScenarioRunRequest,
    ScenarioValues,
)
from a2_engine import (
    IntercityCargoAnalysis,
    analyze_intercity_cargo,
    compare_scenario_metrics,
    load_cargo_delivery_rules,
    load_freight_config,
)
from pydantic import Field, model_validator

from a2_api.adapters.demo_transport import DEMO_LIMITATIONS
from a2_api.adapters.product_inputs import DeliveryInputs, EngineInputs
from a2_api.adapters.product_projection import Projection, project_delivery
from a2_api.core.errors import DomainError
from a2_api.core.jobs import ClaimedJob, V2JobContext
from a2_api.core.registry import ProductRegistry


class CargoHandlingPlan(ContractModel):
    template_id: Identifier
    operation_type: Literal["reduce_handling_wait"]
    changes: tuple[CargoHandlingChange, ...] = Field(min_length=1, max_length=16)
    demand_evidence: tuple[Identifier, ...] = Field(min_length=1)
    resource_evidence: tuple[Identifier, ...] = Field(min_length=1)
    assumptions: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def unique_actions(self) -> "CargoHandlingPlan":
        if len({change.action_id for change in self.changes}) != len(self.changes):
            raise ValueError("CARGO_HANDLING_ACTION_DUPLICATE")
        return self


class CargoResourceFact(ContractModel):
    """Private resource inventory pinned by the scenario document's file hash."""

    resource_id: Identifier
    cargo_profile_id: Identifier
    compatible_action_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=64)
    available_units: int = Field(strict=True, ge=1)
    valid_from: Timestamp
    valid_to: Timestamp
    evidence_refs: tuple[Identifier, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unambiguous_resource(self) -> "CargoResourceFact":
        if self.valid_from >= self.valid_to:
            raise ValueError("CARGO_HANDLING_RESOURCE_FINITE_WINDOW_REQUIRED")
        if len(set(self.compatible_action_ids)) != len(self.compatible_action_ids):
            raise ValueError("CARGO_HANDLING_RESOURCE_ACTION_DUPLICATE")
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("CARGO_HANDLING_RESOURCE_EVIDENCE_DUPLICATE")
        return self


class CargoScenarioDocument(ContractModel):
    schema_version: Literal["waydex-product-cargo-scenarios/1"]
    profile_id: Identifier
    snapshot_id: Identifier
    snapshot_hash: Identifier
    request_hash: Identifier
    normalized_request: AnalysisRequest
    source_inputs_hash: Identifier
    resources: tuple[CargoResourceFact, ...] = Field(min_length=1, max_length=64)
    plans: tuple[CargoHandlingPlan, ...] = Field(min_length=1, max_length=10)
    evidence_refs: tuple[EvidenceRef, ...] = ()

    @model_validator(mode="after")
    def bound_request(self) -> "CargoScenarioDocument":
        request = self.normalized_request
        if (
            not isinstance(request, IntercityCargoRequest)
            or request.cargo_transport != "multimodal"
        ):
            raise ValueError("CARGO_HANDLING_MODEL_UNAVAILABLE")
        if (
            request.snapshot_id != self.snapshot_id
            or request.methodology is None
            or canonical_hash(request.model_dump(mode="json")) != self.request_hash
        ):
            raise ValueError("SCENARIO_NORMALIZED_REQUEST_BINDING_MISMATCH")
        if len({plan.template_id for plan in self.plans}) != len(self.plans):
            raise ValueError("DUPLICATE_SCENARIO_PLAN")
        if len({resource.resource_id for resource in self.resources}) != len(self.resources):
            raise ValueError("CARGO_HANDLING_RESOURCE_DUPLICATE")
        return self


def _refuse(reason: str, *, conflict: bool = False) -> DomainError:
    return DomainError(
        409 if conflict else 422,
        "incompatible_versions" if conflict else "unsupported_scenario",
        reason,
    )


def validate_source(
    document: CargoScenarioDocument,
    context: V2JobContext,
    inputs: EngineInputs,
    *,
    profile_id: str,
) -> DeliveryInputs:
    from a2_api.adapters.product_scenario_inputs import source_inputs_hash

    if not isinstance(inputs, DeliveryInputs):
        raise _refuse("CARGO_HANDLING_MODEL_UNAVAILABLE")
    if (
        document.profile_id != profile_id
        or document.snapshot_id != context.snapshot_id
        or document.snapshot_hash != context.snapshot_hash
        or document.normalized_request != context.normalized_request
        or document.source_inputs_hash != source_inputs_hash(inputs)
    ):
        raise _refuse("SCENARIO_SOURCE_BINDING_MISMATCH", conflict=True)
    validate_plan_inputs(document, inputs)
    return inputs


def validate_plan_inputs(document: CargoScenarioDocument, inputs: EngineInputs) -> None:
    """Cheap catalog checks; route/resource feasibility stays in the worker."""
    if not isinstance(inputs, DeliveryInputs):
        raise ValueError("CARGO_HANDLING_MODEL_UNAVAILABLE")
    actions = {action.action_id: action for action in inputs.actions}
    if len(actions) != len(inputs.actions):
        raise _refuse("CARGO_HANDLING_ACTION_DUPLICATE")
    resources = {resource.resource_id: resource for resource in document.resources}
    if (
        not isinstance(document.normalized_request, IntercityCargoRequest)
        or inputs.profile.profile_id != document.normalized_request.cargo_profile_id
    ):
        raise _refuse("CARGO_HANDLING_RESOURCE_PROFILE_MISMATCH")
    for plan in document.plans:
        for change in plan.changes:
            action = actions.get(change.action_id)
            if (
                action is None
                or action.kind != "handling"
                or action.necessity != "required"
                or action.seconds != change.before_seconds
                or action.compatibility.state != "confirmed"
            ):
                raise _refuse("CARGO_HANDLING_ACTION_BINDING_MISMATCH")
            resource = resources.get(change.resource_id)
            if resource is None:
                raise _refuse("CARGO_HANDLING_RESOURCE_FACT_REQUIRED")
            if (
                resource.cargo_profile_id != inputs.profile.profile_id
                or change.action_id not in resource.compatible_action_ids
                or change.resource_units_after > resource.available_units
                or change.valid_from < resource.valid_from
                or change.valid_to > resource.valid_to
            ):
                raise _refuse("CARGO_HANDLING_RESOURCE_BINDING_MISMATCH")
            if not set(resource.evidence_refs) <= set(plan.resource_evidence):
                raise _refuse("CARGO_HANDLING_RESOURCE_EVIDENCE_MISMATCH")


def _projection(
    context: V2JobContext,
    inputs: DeliveryInputs,
    baseline: AnalysisResult,
    document: CargoScenarioDocument,
    plan: CargoHandlingPlan | None = None,
) -> tuple[Projection, IntercityCargoAnalysis]:
    request = context.normalized_request
    assert isinstance(request, IntercityCargoRequest)
    scope = inputs.scope
    if request.origin_node_ids is not None:
        scope = replace(
            scope,
            origin_node_ids=tuple(request.origin_node_ids),
            destination_node_ids=tuple(request.destination_node_ids or ()),
        )
    if request.cargo_modes is not None:
        scope = replace(
            scope,
            allowed_modes=tuple(
                mode for mode in scope.allowed_modes if mode in request.cargo_modes
            ),
        )
    changes = {change.action_id: change for change in plan.changes} if plan else {}
    actions = tuple(
        replace(action, seconds=changes[action.action_id].after_seconds)
        if action.action_id in changes
        else action
        for action in inputs.actions
    )
    analysis = analyze_intercity_cargo(
        scope,
        request.departure_at,
        inputs.profile,
        inputs.states,
        actions,
        inputs.readiness,
        config=load_freight_config(),
        rules=load_cargo_delivery_rules(),
        resource_limit=inputs.resource_limit,
    )
    evidence = {row.evidence_id: row for row in baseline.evidence_refs}
    for row in document.evidence_refs:
        if row.evidence_id in evidence and evidence[row.evidence_id] != row:
            raise _refuse("SCENARIO_EVIDENCE_IDENTITY_COLLISION", conflict=True)
        evidence[row.evidence_id] = row
    out = Projection(context, list(evidence.values()))
    project_delivery(out, analysis)
    if DEMO_LIMITATIONS <= set(baseline.limitations):
        out.scores = [score.model_copy(update={"ranking_eligible": False}) for score in out.scores]
        out.limitations.update(DEMO_LIMITATIONS)
    return out, analysis


def _prepare(
    context: V2JobContext,
    inputs: EngineInputs,
    document: CargoScenarioDocument,
    baseline: AnalysisResult,
    *,
    profile_id: str,
) -> tuple[DeliveryInputs, Projection, IntercityCargoAnalysis]:
    from a2_api.adapters.product_scenarios import _values

    document = CargoScenarioDocument.model_validate(document.model_dump(mode="json"))
    baseline = AnalysisResult.model_validate(baseline.model_dump(mode="json"))
    current = ProductRegistry()
    if (
        baseline.analysis_id != context.analysis_id
        or baseline.request != context.normalized_request
        or baseline.versions != context.versions
        or baseline.snapshot_id != context.snapshot_id
        or baseline.result_hash
        != canonical_hash(baseline.model_dump(mode="json", exclude={"result_hash"}))
        or context.versions.config_hash != current.config_hash
        or context.versions.methodology_version != current.methodology.version
    ):
        raise _refuse("SCENARIO_SAVED_BASELINE_CHANGED", conflict=True)
    facts = validate_source(document, context, inputs, profile_id=profile_id)
    out, analysis = _projection(context, facts, baseline, document)
    saved = {
        **{("metric", row.metric_id): row for row in baseline.metrics},
        **{("score", row.score_id): row for row in baseline.scores},
    }
    calculated = _values(out)
    if saved.keys() != calculated.keys():
        raise _refuse("SCENARIO_BASELINE_ROSTER_UNREPRESENTABLE")
    for key, old in saved.items():
        excluded = {"source_refs", "journey_id"} if isinstance(old, Metric) else {"evidence_refs"}
        if old.model_dump(mode="json", exclude=excluded) != calculated[key].model_dump(
            mode="json", exclude=excluded
        ):
            raise _refuse("SCENARIO_BASELINE_VALUES_DIFFER", conflict=True)
    return facts, out, analysis


def _check_resources(
    document: CargoScenarioDocument,
    plan: CargoHandlingPlan,
    before: IntercityCargoAnalysis,
    after: IntercityCargoAnalysis,
    evidence: list[EvidenceRef],
) -> None:
    known = {row.evidence_id for row in evidence}
    if not set((*plan.demand_evidence, *plan.resource_evidence)) <= known:
        raise _refuse("SCENARIO_EVIDENCE_UNRESOLVED")
    resources = {resource.resource_id: resource for resource in document.resources}
    for change in plan.changes:
        resource = resources[change.resource_id]
        # The solver must actually traverse the changed operation; a resource
        # claim for an unused or incompatible action cannot publish a proposal.
        for calculation in (before, after):
            intervals = [
                interval
                for path in calculation.candidates
                for interval in path.intervals
                if interval.action_id == change.action_id and interval.kind == "handling"
            ]
            if not intervals or any(
                interval.start_at < change.valid_from
                or interval.end_at > change.valid_to
                or interval.start_at < resource.valid_from
                or interval.end_at > resource.valid_to
                for interval in intervals
            ):
                raise _refuse("CARGO_HANDLING_RESOURCE_WINDOW_UNAVAILABLE")


def _proposal(
    context: V2JobContext,
    baseline: AnalysisResult,
    document: CargoScenarioDocument,
    plan: CargoHandlingPlan,
    before: IntercityCargoAnalysis,
    inputs: DeliveryInputs,
) -> ScenarioProposal:
    after_out, after = _projection(context, inputs, baseline, document, plan)
    _check_resources(document, plan, before, after, after_out.evidence)
    proposal = ScenarioProposal(
        schema_version="2",
        scenario_id="pending:" + canonical_hash(plan.model_dump(mode="json")),
        baseline_analysis_id=baseline.analysis_id,
        template_id=plan.template_id,
        operation_type=plan.operation_type,
        fixed_parameters=FixedParameters(
            service_ids=[],
            shift_seconds=0,
            demand_evidence=list(plan.demand_evidence),
            resource_evidence=list(plan.resource_evidence),
            turnaround_evidence=[],
            resource_constraints=sorted({change.resource_id for change in plan.changes}),
            handling_changes=list(plan.changes),
        ),
        evidence_refs=after_out.evidence,
        evidence_groups=baseline.evidence_groups,
        assumptions=[
            *plan.assumptions,
            "scenario-document:" + canonical_hash(document.model_dump(mode="json")),
            "scenario-plan:" + canonical_hash(plan.model_dump(mode="json")),
        ],
        status="proposed",
        feasibility="verified",
        versions=context.versions,
        baseline_result_hash=baseline.result_hash,
        editable_local_graph=True,
        limitations=sorted(
            set(baseline.limitations)
            | {
                "MODELLED_CARGO_PROCESSING_RESOURCE",
                "CARGO_RESOURCE_CONTRACT_PENDING_APPROVAL",
            }
        ),
    )
    return proposal


def propose(
    context: V2JobContext,
    inputs: EngineInputs,
    document: CargoScenarioDocument,
    baseline: AnalysisResult,
    *,
    profile_id: str,
) -> list[ScenarioProposal]:
    facts, _, before = _prepare(context, inputs, document, baseline, profile_id=profile_id)
    return [_proposal(context, baseline, document, plan, before, facts) for plan in document.plans]


def calculate(
    claim: ClaimedJob,
    *,
    baseline: AnalysisResult,
    inputs: EngineInputs,
    document: CargoScenarioDocument,
    profile_id: str,
) -> ScenarioResult:
    from a2_api.adapters.product_scenarios import _absent, _canonical_measures, _values

    if (
        claim.kind != "scenario"
        or not isinstance(claim.context, V2JobContext)
        or claim.context.scenario_proposal is None
        or not isinstance(claim.request, ScenarioRunRequest)
        or claim.request.scenario_id != claim.context.scenario_proposal.scenario_id
        or claim.request.baseline_analysis_id != baseline.analysis_id
    ):
        raise _refuse("SAVED_SCENARIO_PROPOSAL_REQUIRED")
    context, command = claim.context, claim.context.scenario_proposal
    facts, before_out, before = _prepare(context, inputs, document, baseline, profile_id=profile_id)
    matches = [
        plan
        for plan in document.plans
        if "scenario-plan:" + canonical_hash(plan.model_dump(mode="json")) in command.assumptions
    ]
    if len(matches) != 1:
        raise _refuse("SAVED_SCENARIO_PLAN_UNAVAILABLE", conflict=True)
    plan = matches[0]
    expected = _proposal(context, baseline, document, plan, before, facts).model_copy(
        update={"scenario_id": command.scenario_id}
    )
    if expected != command:
        raise _refuse("SAVED_SCENARIO_PROPOSAL_CHANGED", conflict=True)
    after_out, _ = _projection(context, facts, baseline, document, plan)
    old, new = _values(before_out), _values(after_out)
    if old.keys() != new.keys():
        raise _refuse("SCENARIO_AFTER_ROSTER_UNREPRESENTABLE")
    effects = compare_scenario_metrics(
        _canonical_measures(old, peers=new), _canonical_measures(new, peers=old)
    )
    deltas = []
    for effect in effects:
        key = cast(tuple[str, str], effect.key)
        value = old[key]
        if effect.outcome == "unchanged" and _absent(value) and _absent(new[key]):
            continue
        deltas.append(
            ScenarioDelta(
                effect_id="effect:" + canonical_hash(effect.key),
                kind=cast(Literal["metric", "score"], key[0]),
                before_id=effect.key[1],
                after_id=effect.key[1],
                unit=value.unit if isinstance(value, Metric) else "points_0_100",
                delta=effect.delta.value,
                sign_convention="before_minus_after"
                if isinstance(value, Metric) and value.unit == "seconds"
                else "after_minus_before",
                outcome=effect.outcome,
                reason_code=effect.delta.reason_code,
                scope="transport",
            )
        )
    before_values = ScenarioValues(metrics=baseline.metrics, scores=baseline.scores)
    after_values = ScenarioValues(
        metrics=[cast(Metric, new[("metric", row.metric_id)]) for row in baseline.metrics],
        scores=[cast(Score, new[("score", row.score_id)]) for row in baseline.scores],
    )
    all_values: list[Metric | Score] = [
        *before_values.metrics,
        *before_values.scores,
        *after_values.metrics,
        *after_values.scores,
    ]
    complete = all(
        row.completeness == "full" and row.status in ("available", "not_applicable")
        for row in all_values
    ) and all(effect.outcome != "unknown" for effect in deltas)
    result = ScenarioResult(
        schema_version="2",
        run_id=str(uuid5(NAMESPACE_URL, "waydex-cargo-scenario-run:" + claim.job.job_id)),
        scenario_id=command.scenario_id,
        baseline_analysis_id=baseline.analysis_id,
        status="available" if complete else "partial",
        before=before_values,
        after=after_values,
        deltas=deltas,
        affected_population=None,
        affected_sites=None,
        worsened_metric_ids=sorted(
            effect.after_id
            for effect in deltas
            if effect.kind == "metric"
            and effect.outcome in {"worsened", "lost_connection"}
            and effect.after_id
        ),
        feasibility=command.feasibility,
        service_changes=[],
        handling_changes=list(plan.changes),
        versions=baseline.versions,
        overlay_graph_id="cargo-processing-overlay:" + canonical_hash(plan.model_dump(mode="json")),
        baseline_result_hash=baseline.result_hash,
        result_hash="pending-publication",
        params_hash=claim.cache_key,
        operations_hash=canonical_hash(
            {
                "operation_type": command.operation_type,
                "fixed_parameters": command.fixed_parameters.model_dump(mode="json"),
            }
        ),
        evidence_refs=after_out.evidence,
        evidence_groups=baseline.evidence_groups,
        turnaround_evidence=[],
        resource_evidence=command.fixed_parameters.resource_evidence,
        assumptions=command.assumptions,
        limitations=command.limitations,
        editable_local_graph=True,
    )
    from a2_api.adapters.evidence_groups import pack_scenario_evidence

    return pack_scenario_evidence(result, baseline)
