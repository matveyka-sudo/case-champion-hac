"""Immutable baseline → overlay → real recomputation → complete effects."""

from a2_engine.intercity.analysis.identity import normalize_physical_services
from a2_engine.routing.timetable_models import TimetableRun
from a2_engine.scenario.bindings import (
    TimetableExecutionKey,
    stop_pattern,
    timetable_execution_key,
    timetable_physical_ids,
)
from a2_engine.scenario.effects import compare_scenario_metrics
from a2_engine.scenario.feasibility import check_scenario_feasibility
from a2_engine.scenario.identity import collect_evidence, scenario_hash
from a2_engine.scenario.impact import scenario_impact
from a2_engine.scenario.models import (
    ScenarioBaseline,
    ScenarioFeasibilityInputs,
    ScenarioInputs,
    ScenarioOperation,
    ScenarioResult,
)
from a2_engine.scenario.overlay import apply_scenario_overlay
from a2_engine.scenario.recompute import recompute


def create_scenario_baseline(inputs: ScenarioInputs) -> ScenarioBaseline:
    if not isinstance(inputs, ScenarioInputs):
        raise ValueError("NORMALIZED_SCENARIO_INPUTS_REQUIRED")
    runs_seen: dict[str, TimetableRun] = {}
    physical_runs: dict[TimetableExecutionKey, TimetableRun] = {}
    physical_ids = timetable_physical_ids(inputs.timetables)
    for dataset in inputs.timetables:
        for run in dataset.timetable.runs:
            run_id = run.run_id
            other = runs_seen.setdefault(run_id, run)
            if other is not run and scenario_hash(other) != scenario_hash(run):
                raise ValueError("INCONSISTENT_SCENARIO_EXECUTION_COPIES")
            if run_id in physical_ids:
                key = timetable_execution_key(run, physical_ids)
                other = physical_runs.setdefault(key, run)
                if other is not run and (
                    other.active != run.active or stop_pattern(other) != stop_pattern(run)
                ):
                    raise ValueError("INCONSISTENT_SCENARIO_PHYSICAL_EXECUTION_COPIES")
        normalize_physical_services(
            dataset.timetable, dataset.transport_modes, dataset.physical_services
        )
    for job in inputs.cargo:
        executions = {e.execution_id: e for e in job.executions}
        if len(executions) != len(job.executions):
            raise ValueError("DUPLICATE_CARGO_EXECUTION_BINDING")
        actions = {a.action_id: a for a in job.actions}
        used: set[str] = set()
        for execution in job.executions:
            if set(execution.action_ids) - actions.keys() or used & set(execution.action_ids):
                raise ValueError("CARGO_SCENARIO_EXECUTION_ACTION_CONFLICT")
            if any(actions[a].service_id != execution.execution_id for a in execution.action_ids):
                raise ValueError("CARGO_SCENARIO_EXECUTION_ID_MISMATCH")
            used.update(execution.action_ids)
    fingerprint = scenario_hash(inputs)
    before = recompute(inputs, fingerprint)
    return ScenarioBaseline(
        "baseline:" + fingerprint, fingerprint, scenario_hash(before), inputs, before
    )


def simulate_scenario(
    baseline: ScenarioBaseline,
    operation: ScenarioOperation,
    *,
    feasibility: ScenarioFeasibilityInputs | None = None,
) -> ScenarioResult:
    if not isinstance(baseline, ScenarioBaseline):
        raise ValueError("IMMUTABLE_SCENARIO_BASELINE_REQUIRED")
    before_hash = scenario_hash(baseline.inputs)
    if (
        baseline.baseline_id != "baseline:" + before_hash
        or baseline.input_hash != before_hash
        or baseline.result_hash != scenario_hash(baseline.before)
    ):
        raise ValueError("IMMUTABLE_SCENARIO_BASELINE_CHANGED")
    # An externally constructed baseline with a self-consistent hash is still not
    # allowed to replace computed before values with optimistic/fabricated data.
    verified_before = recompute(baseline.inputs, before_hash)
    if verified_before != baseline.before:
        raise ValueError("BASELINE_RESULT_DOES_NOT_MATCH_TRANSPORT_INPUTS")
    overlay = apply_scenario_overlay(baseline, operation)
    feasibility_result = check_scenario_feasibility(baseline, operation, overlay, feasibility)
    if operation.template_id == "additional_departure" and feasibility_result.state != "feasible":
        raise ValueError("ADDITIONAL_DEPARTURE_REQUIRES_VERIFIED_RESOURCE_TURNAROUND")
    after = recompute(overlay.inputs, before_hash)
    effects = compare_scenario_metrics(baseline.before.metrics, after.metrics)
    if len({effect.metric_id for effect in effects}) != len(effects):
        raise ValueError("SCENARIO_EFFECTS_INCOMPLETE_OR_SUBSTITUTED")
    worsened = tuple(
        sorted(e.metric_id for e in effects if e.outcome in ("worsened", "lost_connection"))
    )
    impact = scenario_impact(baseline.inputs, effects)
    complete = all(effect.outcome != "unknown" for effect in effects)
    diagnostics = set(feasibility_result.reasons)
    if not complete:
        diagnostics.add("SCENARIO_EFFECT_COVERAGE_PARTIAL")
    if not impact.population_complete:
        diagnostics.add("AFFECTED_POPULATION_LOWER_BOUND")
    if not impact.sites_complete:
        diagnostics.add("AFFECTED_SITES_LOWER_BOUND")
    if scenario_hash(baseline.inputs) != before_hash:
        raise ValueError("SCENARIO_MUTATED_BASELINE")
    label = (
        "Модельный эксперимент — реализуемость не подтверждена"
        if feasibility_result.state == "unverified"
        else "Подтверждённое нарушение ограничений — сценарий нереализуем"
        if feasibility_result.state == "infeasible"
        else "Реализуемость подтверждена в принятой области ограничений"
    )
    identity = scenario_hash(
        (baseline.baseline_id, operation, feasibility, overlay.overlay_id, after, effects, impact)
    )
    evidence = set(
        collect_evidence(baseline.inputs)
        + operation.evidence_refs
        + operation.demand_evidence_refs
        + feasibility_result.evidence_refs
    )
    for dataset in baseline.inputs.timetables:
        evidence.update(dataset.timetable.scope.evidence_refs)
    for job in baseline.inputs.cargo:
        evidence.update(job.scope.evidence_refs)
    return ScenarioResult(
        "scenario:" + identity,
        baseline.baseline_id,
        operation,
        overlay,
        baseline.before,
        after,
        effects,
        worsened,
        feasibility_result,
        impact,
        complete,
        label,
        tuple(sorted(diagnostics)),
        tuple(sorted(evidence)),
    )


def validate_scenario_result(
    baseline: ScenarioBaseline,
    operation: ScenarioOperation,
    result: ScenarioResult,
    *,
    feasibility: ScenarioFeasibilityInputs | None = None,
) -> None:
    """Publication guard: reconstruct after and effects from saved authorized inputs.

    This intentionally re-executes the pure calculation. Matching deltas alone
    cannot certify a result whose after values have been replaced with unknown.
    """
    expected = simulate_scenario(baseline, operation, feasibility=feasibility)
    if result != expected:
        raise ValueError("SCENARIO_RESULT_SUBSTITUTED_OR_WORSENING_HIDDEN")
