"""Saved proposals → immutable public-engine scenarios → canonical v2 effects.

Callers supply the exact scoped, persisted baseline and a hash-verified private
ScenarioDocument selected by the runtime. No network, database or client-selected
operation is evaluated here. Unsupported representations fail before publication.
"""

from dataclasses import dataclass
from typing import Literal, cast
from zoneinfo import ZoneInfo

from a2_contracts.common import canonical_hash
from a2_contracts.v2.results import AnalysisResult, EvidenceRef, Metric, Score
from a2_contracts.v2.scenarios import (
    FixedParameters,
    ScenarioDelta,
    ScenarioProposal,
    ScenarioResult,
    ScenarioRunRequest,
    ScenarioValues,
    ServiceChange,
)
from a2_engine import (
    ScenarioBaseline,
    ScenarioComputation,
    ScenarioMeasure,
    apply_scenario_overlay,
    check_scenario_feasibility,
    compare_scenario_metrics,
    create_scenario_baseline,
    scenario_hash,
    simulate_scenario,
    validate_scenario_result,
)

from a2_api.adapters.demo_transport import DEMO_LIMITATIONS
from a2_api.adapters.product_inputs import EngineInputs
from a2_api.adapters.product_projection import (
    Projection,
    project_delivery,
    project_intercity,
    project_urban,
)
from a2_api.adapters.product_scenario_inputs import ScenarioDocument, ScenarioPlan, validate_source
from a2_api.core.errors import DomainError
from a2_api.core.jobs import ClaimedJob, V2JobContext
from a2_api.core.registry import ProductRegistry


def _refuse(reason: str, *, conflict: bool = False) -> DomainError:
    return DomainError(
        409 if conflict else 422,
        "incompatible_versions" if conflict else "unsupported_scenario",
        reason,
    )


def _evidence(baseline: AnalysisResult, document: ScenarioDocument) -> list[EvidenceRef]:
    refs = {row.evidence_id: row for row in baseline.evidence_refs}
    for row in document.evidence_refs:
        if row.evidence_id in refs and refs[row.evidence_id] != row:
            raise _refuse("SCENARIO_EVIDENCE_IDENTITY_COLLISION", conflict=True)
        refs[row.evidence_id] = row
    return list(refs.values())


def _project(
    context: V2JobContext,
    baseline: AnalysisResult,
    document: ScenarioDocument,
    computation: ScenarioComputation,
) -> Projection:
    out = Projection(context, _evidence(baseline, document))
    for row in computation.urban:
        project_urban(out, row.analysis)
    jobs = {row.job_id: row for row in document.inputs.intercity}
    for identity, intercity_result in computation.intercity:
        job = jobs[identity]
        project_intercity(out, intercity_result, job.reference, job.car)
    for _, cargo_result in computation.cargo:
        project_delivery(out, cargo_result)
    # The admitted synthetic model uses the same numerical projection, but its
    # saved scores must remain excluded from real-city rankings on both sides.
    # A prefix alone cannot select this policy: the immutable saved result and
    # every hash-pinned server plan must carry the explicit demo declarations.
    if (
        baseline.snapshot_id.startswith("demo-moscow-")
        and DEMO_LIMITATIONS <= set(baseline.limitations)
        and all(DEMO_LIMITATIONS <= set(plan.assumptions) for plan in document.plans)
    ):
        out.scores = [score.model_copy(update={"ranking_eligible": False}) for score in out.scores]
        out.limitations.update(DEMO_LIMITATIONS)
    return out


def _semantic(value: Metric | Score) -> str:
    excluded = (
        {"value", "status", "reason_code", "source_refs", "journey_id"}
        if isinstance(value, Metric)
        else {
            "value",
            "status",
            "reason_code",
            "evidence_refs",
            "components",
            "reference_ids",
            "ranking_eligible",
        }
    )
    return canonical_hash(value.model_dump(mode="json", exclude=excluded))


def _measure_id(measure: ScenarioMeasure) -> tuple[str, str] | None:
    key = measure.key
    if key[1] == "territory" and key[-1] == "index" and key[3] == "all":
        return "score", "urban-territory-" + key[4]
    if key[1] == "point":
        if key[3] == "all" and key[-1] == "index":
            return "score", "urban-point-" + key[2]
        suffix = canonical_hash([key[2], key[3], key[4]])[:24]
        return {
            "time": ("metric", "urban-time-" + suffix),
            "count": ("metric", "urban-count-" + suffix),
            "index": ("score", "urban-category-" + suffix),
        }.get(key[-1])
    if key[1] == "pair":
        prefix = "intercity-" + key[4]
        return {
            "index": ("score", prefix),
            "path_seconds": ("metric", prefix + "-path"),
            "initial_wait_seconds": ("metric", prefix + "-wait"),
            "total_seconds": ("metric", prefix + "-total"),
            "departures": ("metric", "departures-" + key[4]),
        }.get(key[-1])
    if key[1] == "cargo_pair":
        return {
            "index": ("score", "delivery-" + key[4]),
            "delivery_time": ("metric", "delivery-" + key[4] + "-time"),
        }.get(key[-1])
    return None


def _values(out: Projection) -> dict[tuple[str, str], Metric | Score]:
    return {
        **{("metric", row.metric_id): row for row in out.metrics},
        **{("score", row.score_id): row for row in out.scores},
    }


def _absent(value: Metric | Score) -> bool:
    return (
        value.completeness == "full"
        and value.reason_code == "NO_FEASIBLE_ROUTE"
        and (
            isinstance(value, Metric)
            and value.status == "not_applicable"
            and value.value is None
            and bool(value.source_refs)
            or isinstance(value, Score)
            and value.status == "available"
            and value.value == 0
        )
    )


def _check_measure(measure: ScenarioMeasure, value: Metric | Score) -> None:
    if measure.state == "absent":
        if not _absent(value):
            raise _refuse("SCENARIO_BASELINE_ABSENCE_UNREPRESENTABLE")
    elif measure.value != value.value or (measure.state == "known" and value.status != "available"):
        raise _refuse("SCENARIO_BASELINE_MEASURE_DIFFERS", conflict=True)


@dataclass(frozen=True)
class Prepared:
    baseline: ScenarioBaseline
    before: Projection
    evidence: list[EvidenceRef]


def _prepare(
    context: V2JobContext,
    inputs: EngineInputs,
    document: ScenarioDocument,
    baseline: AnalysisResult,
    *,
    profile_id: str,
) -> Prepared:
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
    try:
        document = ScenarioDocument.model_validate(document.model_dump(mode="json"))
        validate_source(document, context, inputs, profile_id=profile_id)
        frozen = create_scenario_baseline(document.inputs)
    except (ValueError, TypeError) as exc:
        raise _refuse(str(exc), conflict=True) from exc
    before = _project(context, baseline, document, frozen.before)
    saved: dict[tuple[str, str], Metric | Score] = {
        **{("metric", row.metric_id): row for row in baseline.metrics},
        **{("score", row.score_id): row for row in baseline.scores},
    }
    calculated = _values(before)
    # Runtime's diagnostic run count is source-bound, rather than an engine score.
    if ("metric", "accepted-dated-service-runs") in saved and document.inputs.timetables:
        row = saved[("metric", "accepted-dated-service-runs")]
        table = document.inputs.timetables[0].timetable
        local_day = context.normalized_request.departure_at.astimezone(
            ZoneInfo(table.scope.timezone)
        ).date()
        if row.value != sum(run.service_date == local_day for run in table.runs):
            raise _refuse("SCENARIO_SAVED_SERVICE_COUNT_DIFFERS", conflict=True)
        before.metrics.insert(0, cast(Metric, row))
        calculated = _values(before)
    if set(saved) != set(calculated):
        raise _refuse("SCENARIO_BASELINE_ROSTER_UNREPRESENTABLE")
    for key, value in saved.items():
        other = calculated[key]
        excluded = {"source_refs", "journey_id"} if isinstance(value, Metric) else {"evidence_refs"}
        if value.model_dump(mode="json", exclude=excluded) != other.model_dump(
            mode="json", exclude=excluded
        ):
            raise _refuse("SCENARIO_RECOMPUTED_BASELINE_DIFFERS", conflict=True)
    for measure in frozen.before.metrics:
        measure_key = _measure_id(measure)
        if measure_key is None or measure_key not in saved:
            raise _refuse("SCENARIO_MEASURE_UNREPRESENTABLE:" + ":".join(measure.key))
        _check_measure(measure, saved[measure_key])
    return Prepared(frozen, before, _evidence(baseline, document))


def _proposal(
    context: V2JobContext,
    baseline: AnalysisResult,
    document: ScenarioDocument,
    plan: ScenarioPlan,
    prepared: Prepared,
) -> ScenarioProposal:
    try:
        overlay = apply_scenario_overlay(prepared.baseline, plan.operation)
        feasibility = check_scenario_feasibility(
            prepared.baseline, plan.operation, overlay, plan.feasibility
        )
    except (ValueError, TypeError, StopIteration) as exc:
        raise _refuse(str(exc), conflict=True) from exc
    if feasibility.state == "infeasible":
        raise _refuse("SCENARIO_INFEASIBLE:" + ",".join(feasibility.reasons))
    if plan.operation.template_id == "additional_departure" and feasibility.state != "feasible":
        raise _refuse("ADDITIONAL_DEPARTURE_REQUIRES_VERIFIED_RESOURCE_TURNAROUND")
    resource_refs = (
        sorted({ref for block in plan.feasibility.blocks for ref in block.evidence_refs})
        if plan.feasibility
        else []
    )
    turnaround_refs = (
        sorted(
            {
                ref
                for block in plan.feasibility.blocks
                for ref in (
                    *block.evidence_refs,
                    *(ref for link in block.links for ref in link.evidence_refs),
                )
            }
        )
        if plan.feasibility
        else []
    )
    limitations = set(feasibility.reasons)
    if DEMO_LIMITATIONS <= prepared.before.limitations:
        limitations.update(DEMO_LIMITATIONS)
    if feasibility.state == "unverified" and not turnaround_refs:
        limitations.add("TURNAROUND_UNVERIFIED")
    proposal = ScenarioProposal(
        schema_version="2",
        scenario_id="pending:" + scenario_hash(plan.operation),
        baseline_analysis_id=baseline.analysis_id,
        template_id=plan.template_id,
        operation_type=cast(
            Literal["coordinate_transfer", "additional_departure"], plan.operation.template_id
        ),
        fixed_parameters=FixedParameters(
            service_ids=list(overlay.affected_execution_ids),
            shift_seconds=plan.operation.shift_seconds,
            demand_evidence=list(plan.operation.demand_evidence_refs),
            resource_evidence=resource_refs,
            turnaround_evidence=turnaround_refs,
            resource_constraints=list(plan.resource_constraints),
        ),
        evidence_refs=prepared.evidence,
        assumptions=[
            *plan.assumptions,
            "scenario-document:" + canonical_hash(document.model_dump(mode="json")),
            "scenario-plan:" + canonical_hash(plan.model_dump(mode="json")),
        ],
        status="proposed",
        feasibility="verified" if feasibility.state == "feasible" else "unverified",
        versions=context.versions,
        baseline_result_hash=baseline.result_hash,
        editable_local_graph=True,
        limitations=sorted(limitations),
    )
    referenced = set(plan.operation.evidence_refs) | set(feasibility.evidence_refs)
    if not referenced <= {row.evidence_id for row in prepared.evidence}:
        raise _refuse("SCENARIO_EVIDENCE_UNRESOLVED")
    return proposal


def propose(
    context: V2JobContext,
    inputs: EngineInputs,
    document: ScenarioDocument,
    baseline: AnalysisResult,
    *,
    profile_id: str,
) -> list[ScenarioProposal]:
    """Generate fixed proposals in the analysis worker; no effects are claimed."""
    prepared = _prepare(context, inputs, document, baseline, profile_id=profile_id)
    return [_proposal(context, baseline, document, plan, prepared) for plan in document.plans]


def _canonical_measures(
    values: dict[tuple[str, str], Metric | Score], *, peers: dict[tuple[str, str], Metric | Score]
) -> tuple[ScenarioMeasure, ...]:
    result = []
    for key, value in sorted(values.items()):
        peer = peers[key]
        state: Literal["known", "absent", "unknown"] = (
            "absent"
            if _absent(value)
            else "known"
            if value.status == "available" and value.value is not None
            else "unknown"
        )
        result.append(
            ScenarioMeasure(
                key,
                "index"
                if isinstance(value, Score)
                else "seconds"
                if value.unit == "seconds"
                else "count",
                isinstance(value, Score) or isinstance(value, Metric) and value.unit != "seconds",
                value.value,
                state,
                state != "unknown" and _semantic(value) == _semantic(peer),
                "canonical-v2",
                reason_code=value.reason_code,
            )
        )
    return tuple(result)


def calculate(
    claim: ClaimedJob,
    *,
    baseline: AnalysisResult,
    inputs: EngineInputs,
    document: ScenarioDocument,
    profile_id: str,
) -> ScenarioResult:
    """Run the exact saved operation; publication must retain repository fencing."""
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
    prepared = _prepare(context, inputs, document, baseline, profile_id=profile_id)
    matches = []
    for plan in document.plans:
        expected = _proposal(context, baseline, document, plan, prepared)
        if expected.model_copy(update={"scenario_id": command.scenario_id}) == command:
            matches.append(plan)
    if len(matches) != 1:
        raise _refuse("SCENARIO_SAVED_OPERATION_CHANGED", conflict=True)
    plan = matches[0]
    try:
        engine = simulate_scenario(prepared.baseline, plan.operation, feasibility=plan.feasibility)
        validate_scenario_result(
            prepared.baseline, plan.operation, engine, feasibility=plan.feasibility
        )
    except (ValueError, TypeError) as exc:
        raise _refuse(str(exc), conflict=True) from exc
    if engine.feasibility.state == "infeasible":
        raise _refuse("SCENARIO_INFEASIBLE:" + ",".join(engine.feasibility.reasons))
    if not set(engine.evidence_refs) <= {row.evidence_id for row in prepared.evidence}:
        raise _refuse("SCENARIO_EVIDENCE_UNRESOLVED")
    after = _project(context, baseline, document, engine.after)
    old: dict[tuple[str, str], Metric | Score] = {
        **{("metric", row.metric_id): row for row in baseline.metrics},
        **{("score", row.score_id): row for row in baseline.scores},
    }
    new = _values(after)
    # Explicit proven losses retain their original scoped raw task and basis.
    for measure in engine.after.metrics:
        key = _measure_id(measure)
        if key is None or key not in old:
            raise _refuse("SCENARIO_MEASURE_UNREPRESENTABLE:" + ":".join(measure.key))
        if measure.state == "absent":
            previous = old[key] if isinstance(old[key], Metric) else new.get(key, old[key])
            new[key] = previous.model_copy(
                update={
                    "value": None if isinstance(previous, Metric) else 0.0,
                    "status": "not_applicable" if isinstance(previous, Metric) else "available",
                    "reason_code": "NO_FEASIBLE_ROUTE",
                    "completeness": "full",
                    **({"journey_id": None} if isinstance(previous, Metric) else {}),
                }
            )
    if ("metric", "accepted-dated-service-runs") in old:
        # Only timetable service count changes; it is not a fabricated index.
        table = engine.overlay.inputs.timetables[0].timetable
        day = context.normalized_request.departure_at.astimezone(
            ZoneInfo(table.scope.timezone)
        ).date()
        new[("metric", "accepted-dated-service-runs")] = old[
            ("metric", "accepted-dated-service-runs")
        ].model_copy(update={"value": float(sum(run.service_date == day for run in table.runs))})
    if set(old) != set(new):
        raise _refuse("SCENARIO_AFTER_ROSTER_UNREPRESENTABLE")
    canonical = compare_scenario_metrics(
        _canonical_measures(old, peers=new), _canonical_measures(new, peers=old)
    )
    canonical_by_key = {effect.key: effect for effect in canonical}
    # Never omit a changed/unknown public-engine quantity or upgrade uncertainty.
    for effect in engine.effects:
        measure = next(row for row in engine.after.metrics if row.key == effect.key)
        key = _measure_id(measure)
        if key is None or key not in canonical_by_key:
            raise _refuse("SCENARIO_MEASURE_UNREPRESENTABLE:" + ":".join(effect.key))
        mapped = canonical_by_key[key]
        if mapped.outcome != effect.outcome or mapped.delta.value != effect.delta.value:
            raise _refuse("SCENARIO_EFFECT_UNREPRESENTABLE:" + ":".join(effect.key))
    deltas = []
    for effect in canonical:
        key = cast(tuple[str, str], effect.key)
        value = old[key]
        if effect.outcome == "unchanged" and _absent(value) and _absent(new[key]):
            continue
        deltas.append(
            ScenarioDelta(
                effect_id="effect:" + canonical_hash(key),
                kind=cast(Literal["metric", "score"], key[0]),
                before_id=key[1],
                after_id=key[1],
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
    try:
        before_values = ScenarioValues(metrics=baseline.metrics, scores=baseline.scores)
        after_values = ScenarioValues(
            metrics=[cast(Metric, new[("metric", row.metric_id)]) for row in baseline.metrics],
            scores=[cast(Score, new[("score", row.score_id)]) for row in baseline.scores],
        )
    except ValueError as exc:
        raise _refuse("SCENARIO_CANONICAL_VALUES_UNREPRESENTABLE:" + str(exc)) from exc
    all_values: list[Metric | Score] = [
        *before_values.metrics,
        *before_values.scores,
        *after_values.metrics,
        *after_values.scores,
    ]
    known = any(row.value is not None for row in all_values)
    complete = (
        engine.complete
        and not any(effect.outcome == "unknown" for effect in deltas)
        and all(
            row.completeness == "full" and row.status in ("available", "not_applicable")
            for row in all_values
        )
    )
    limitations = set(command.limitations) | set(engine.diagnostics)
    if not engine.impact.population_complete:
        limitations.add("AFFECTED_POPULATION_LOWER_BOUND")
    if not engine.impact.sites_complete:
        limitations.add("AFFECTED_SITES_LOWER_BOUND")
    journey_ids = {metric.journey_id for metric in after_values.metrics}
    after_journeys = [journey for journey in after.journeys if journey.journey_id in journey_ids]
    try:
        result = ScenarioResult(
            schema_version="2",
            run_id="pending:" + engine.scenario_id,
            scenario_id=command.scenario_id,
            baseline_analysis_id=baseline.analysis_id,
            status="available" if complete else "partial" if known else "unknown",
            before=before_values,
            after=after_values,
            after_journeys=after_journeys,
            deltas=deltas,
            affected_population=engine.impact.known_affected_population
            if document.inputs.urban and engine.impact.population_complete
            else None,
            affected_sites=len(engine.impact.affected_site_ids)
            if document.inputs.cargo and engine.impact.sites_complete
            else None,
            worsened_metric_ids=sorted(
                effect.after_id
                for effect in deltas
                if effect.kind == "metric"
                and effect.outcome in ("worsened", "lost_connection")
                and effect.after_id is not None
            ),
            feasibility=command.feasibility,
            service_changes=[
                ServiceChange(
                    service_id=identity,
                    shift_seconds=command.fixed_parameters.shift_seconds,
                    whole_trip=True,
                    subsequent_stops_updated=True,
                )
                for identity in command.fixed_parameters.service_ids
            ],
            versions=baseline.versions,
            overlay_graph_id=engine.overlay.overlay_id,
            baseline_result_hash=baseline.result_hash,
            result_hash="pending-publication",
            params_hash=claim.cache_key,
            operations_hash=canonical_hash(
                {
                    "operation_type": command.operation_type,
                    "fixed_parameters": command.fixed_parameters.model_dump(mode="json"),
                }
            ),
            evidence_refs=prepared.evidence,
            turnaround_evidence=command.fixed_parameters.turnaround_evidence,
            resource_evidence=command.fixed_parameters.resource_evidence,
            assumptions=command.assumptions,
            limitations=sorted(limitations),
            editable_local_graph=True,
        )
    except ValueError as exc:
        raise _refuse("SCENARIO_CANONICAL_EFFECTS_UNREPRESENTABLE:" + str(exc)) from exc
    return result.model_copy(
        update={
            "result_hash": canonical_hash(result.model_dump(mode="json", exclude={"result_hash"}))
        }
    )
