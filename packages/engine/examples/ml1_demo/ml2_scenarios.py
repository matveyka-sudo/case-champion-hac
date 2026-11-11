"""Bind external ML2 transit operation controls to the existing Scenario Engine."""

from dataclasses import replace
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from a2_engine.scenario.identity import scenario_hash
from a2_engine.scenario.models import (
    ScenarioBaseline,
    ScenarioFeasibilityInputs,
    ScenarioOperation,
    ScenarioResourceBlock,
    UrbanScenarioResult,
)
from a2_engine.scenario.overlay import execution_hash

from ml1_demo.matrix import Row, run_comparison
from ml1_demo.model import identity


def transit_control_run(baseline: ScenarioBaseline, control: Row) -> Row:
    table = baseline.inputs.timetables[0]
    runs = {r.trip_id: r for r in table.timetable.runs}
    source_control = control["model_inputs"]["problem_transfer"]
    incoming = runs[source_control["arriving_trip_id"]]
    outgoing = runs[source_control["planned_trip_id"]]
    before_event = next(e for e in incoming.events if e.node_id == source_control["from_stop"])
    after_event = next(e for e in outgoing.events if e.node_id == source_control["to_stop"])
    fixed = control["fixed_parameters"]
    shift = fixed["shift_seconds"]
    operation = ScenarioOperation(
        baseline.baseline_id,
        control["comparison_id"],
        control["operation_type"],
        "timetable",
        table.network_id,
        outgoing.run_id,
        outgoing.service_date,
        str(after_event.sequence),
        execution_hash(baseline, "timetable", table.network_id, outgoing.run_id),
        shift,
        incoming.run_id,
        str(before_event.sequence),
        table.timetable.scope.evidence_refs,
    )
    proof = None
    if control["operation_type"] == "additional_departure":
        extra = control["model_inputs"]["extra_departure_slot"]
        if extra["template_trip_id"] != outgoing.trip_id or extra["shift_s"] != shift:
            raise ValueError("ML2_EXTRA_EXECUTION_TEMPLATE_MISMATCH")
        resource = control["model_inputs"]["resource"]
        turnaround = control["model_inputs"]["turnaround"]
        if resource["assigned_trip_ids"]:
            raise ValueError("ML2_EXPLICIT_RESOURCE_LINKS_REQUIRED")
        evidence = tuple(fixed["resource_evidence"] + fixed["turnaround_evidence"])
        new = replace(
            outgoing,
            trip_id=extra["new_trip_id"],
            events=tuple(
                replace(
                    e,
                    arrival_at=e.arrival_at + timedelta(seconds=shift),
                    departure_at=e.departure_at + timedelta(seconds=shift),
                )
                for e in outgoing.events
            ),
        )
        operation = replace(
            operation,
            new_run=new,
            new_physical_service=identity(
                new, table.timetable.scope.evidence_refs + tuple(fixed["demand_evidence"])
            ),
            demand_evidence_refs=tuple(fixed["demand_evidence"]),
        )
        start, end = (
            datetime.combine(
                outgoing.service_date, time.fromisoformat(hhmm), ZoneInfo(outgoing.timezone)
            )
            for hhmm in resource["working_window_local"]
        )
        block = ScenarioResourceBlock(
            resource["vehicle_id"],
            table.network_id,
            (new.run_id,),
            start,
            end,
            (),
            evidence,
            turnaround["layover_s"],
            turnaround["turnaround_s"] - turnaround["run_s"],
        )
        proof = ScenarioFeasibilityInputs(
            baseline.baseline_id,
            scenario_hash(operation),
            (block,),
            (new.run_id,),
            True,
            True,
            True,
            start,
            end,
            evidence,
        )
    point_id, destination_id, *_ = control["ids"]["before"]

    def query(result: UrbanScenarioResult) -> Row:
        search = next(
            s
            for s in result.searches
            if (s.point_id, s.destination_id) == (point_id, destination_id)
        )
        best = search.best_known
        return {
            "total_s": best.total_seconds if best else None,
            "initial_wait_s": best.initial_wait_seconds if best else None,
            "transfer_wait_s": best.transfer_wait_seconds if best else None,
            "status": search.search.state,
            "minimum_proven": search.search_complete,
        }

    result = run_comparison(baseline, operation, proof)
    before, after = query(result.before.urban[0]), query(result.after.urban[0])
    timing_fields = ("total_s", "initial_wait_s", "transfer_wait_s")
    return {
        "replayed": True,
        "scenario_id": result.scenario_id,
        "all_effects_proven": result.complete,
        "feasibility": result.feasibility.state,
        "before": before,
        "after": after,
        "matches_ml2_control": all(
            actual[k] == control[side][k]
            for side, actual in (("before", before), ("after", after))
            for k in timing_fields
        ),
        "expected_by_ml2": {
            side: {k: control[side][k] for k in timing_fields} for side in ("before", "after")
        },
    }
