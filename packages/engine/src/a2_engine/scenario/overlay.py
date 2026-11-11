"""Allowlisted, evidence-bound edits to concrete executions; no ready metrics."""

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal

from a2_engine.freight.delivery.search import analyze_intercity_cargo
from a2_engine.intercity.analysis.identity import normalize_physical_services
from a2_engine.routing.timetable import transfer_rule, walk_access_paths
from a2_engine.routing.timetable_models import Timetable, TimetableRun, microseconds
from a2_engine.scenario.bindings import (
    stop_pattern,
    timetable_execution_key,
    timetable_physical_ids,
)
from a2_engine.scenario.identity import scenario_hash
from a2_engine.scenario.models import (
    ScenarioBaseline,
    ScenarioEventChange,
    ScenarioOperation,
    ScenarioOverlay,
    ScenarioTimetable,
)

SCENARIO_TEMPLATES = ("coordinate_transfer", "additional_departure")


def execution_hash(
    baseline: ScenarioBaseline, target_kind: str, target_id: str, execution_id: str
) -> str:
    if target_kind == "timetable":
        dataset = next((d for d in baseline.inputs.timetables if d.network_id == target_id), None)
        run = (
            next((r for r in dataset.timetable.runs if r.run_id == execution_id), None)
            if dataset
            else None
        )
        if run is None:
            raise ValueError("SCENARIO_EXECUTION_NOT_FOUND")
        return scenario_hash(run)
    if target_kind == "cargo":
        job = next((j for j in baseline.inputs.cargo if j.job_id == target_id), None)
        execution = (
            next((e for e in job.executions if e.execution_id == execution_id), None)
            if job
            else None
        )
        if job is None or execution is None:
            raise ValueError("SCENARIO_CARGO_EXECUTION_NOT_FOUND")
        return scenario_hash(
            (execution, tuple(a for a in job.actions if a.action_id in execution.action_ids))
        )
    raise ValueError("SCENARIO_TARGET_NOT_ALLOWLISTED")


def _shift_time(at: datetime, seconds: int) -> datetime:
    try:
        return at + timedelta(seconds=seconds)
    except OverflowError as exc:
        raise ValueError("SCENARIO_TIME_RANGE_EXCEEDED") from exc


def _shift_run(run: TimetableRun, operation: ScenarioOperation) -> TimetableRun:
    if (
        run.service_date != operation.service_date
        or run.active is not True
        or not run.calendar_evidence_refs
    ):
        raise ValueError("SCENARIO_EXECUTION_CALENDAR_MISMATCH")
    try:
        sequence = int(operation.event_id)
    except ValueError as exc:
        raise ValueError("EXPLICIT_STOP_SEQUENCE_REQUIRED") from exc
    if not any(e.sequence == sequence for e in run.events):
        raise ValueError("SCENARIO_STOP_EVENT_NOT_FOUND")
    events = []
    for event in run.events:
        arrival, departure = event.arrival_at, event.departure_at
        if operation.timing_model == "whole_execution" or event.sequence > sequence:
            arrival = _shift_time(arrival, operation.shift_seconds)
            departure = _shift_time(departure, operation.shift_seconds)
        elif event.sequence == sequence:
            departure = _shift_time(departure, operation.shift_seconds)
        if (arrival, departure) != (event.arrival_at, event.departure_at):
            event = replace(
                event,
                arrival_at=arrival,
                departure_at=departure,
                evidence_refs=tuple(sorted(set(event.evidence_refs + operation.evidence_refs)))
                if event.evidence_refs
                else (),
            )
        events.append(event)
    # Existing immutable run validator checks dwell, ordering and event conflicts.
    return replace(run, events=tuple(events))


def _confirm_transfer(
    dataset: ScenarioTimetable, normalized: Timetable, operation: ScenarioOperation
) -> None:
    runs = {run.run_id: run for run in dataset.timetable.runs}
    physical_ids = timetable_physical_ids((dataset,))
    unaliased = runs.keys() - physical_ids.keys()
    canonical_runs = {
        (run.trip_id, run.service_date): run
        for run in normalized.runs
        if run.run_id not in unaliased
    }

    def resolve(run_id: str) -> TimetableRun | None:
        run = runs.get(run_id)
        if run is None or run_id not in physical_ids:
            return run
        return canonical_runs[physical_ids[run_id], run.service_date]

    incoming = resolve(operation.incoming_execution_id)
    outgoing = resolve(operation.execution_id)
    if (
        incoming is None
        or outgoing is None
        or incoming.run_id == outgoing.run_id
        or incoming.active is not True
        or not incoming.calendar_evidence_refs
    ):
        raise ValueError("REAL_TRANSFER_PAIR_REQUIRED")
    previous = [
        (i, e)
        for i, e in enumerate(incoming.events)
        if str(e.sequence) == operation.incoming_event_id
    ]
    following = [
        (i, e) for i, e in enumerate(outgoing.events) if str(e.sequence) == operation.event_id
    ]
    if not previous or not following or not previous[0][1].dropoff or not following[0][1].pickup:
        raise ValueError("TRANSFER_PICKUP_DROPOFF_REQUIRED")
    i, arrival = previous[0]
    j, departure = following[0]
    if not any(e.pickup for e in incoming.events[:i]) or not any(
        e.dropoff for e in outgoing.events[j + 1 :]
    ):
        raise ValueError("TRANSFER_PICKUP_DROPOFF_REQUIRED")
    if not arrival.evidence_refs or not departure.evidence_refs:
        raise ValueError("ACCEPTED_STOP_EVENT_PROOF_REQUIRED")
    rule = transfer_rule(normalized, incoming, i, outgoing, j)
    if (
        rule is None
        or rule.allowed is not True
        or rule.minimum_seconds is None
        or not rule.evidence_refs
    ):
        raise ValueError("ACCEPTED_TRANSFER_RULE_REQUIRED")
    walks, _, interrupted = walk_access_paths(normalized, arrival.node_id)
    if interrupted or departure.node_id not in walks:
        raise ValueError("ACCEPTED_TRANSFER_PATH_REQUIRED")
    available = microseconds(departure.departure_at - arrival.arrival_at)
    if (
        available < walks[departure.node_id][0]
        or Decimal(available) < Decimal(str(rule.minimum_seconds)) * 1000000
    ):
        raise ValueError("SCENARIO_DOES_NOT_COORDINATE_TRANSFER")


def apply_scenario_overlay(
    baseline: ScenarioBaseline, operation: ScenarioOperation
) -> ScenarioOverlay:
    if not isinstance(baseline, ScenarioBaseline):
        raise ValueError("IMMUTABLE_BASELINE_REQUIRED_CANNOT_STACK_OVERLAYS")
    if operation.baseline_id != baseline.baseline_id or baseline.input_hash != scenario_hash(
        baseline.inputs
    ):
        raise ValueError("SCENARIO_BASELINE_MISMATCH")
    if operation.template_id not in SCENARIO_TEMPLATES:
        raise ValueError("SCENARIO_OPERATION_NOT_ALLOWLISTED")
    if operation.expected_execution_hash != execution_hash(
        baseline, operation.target_kind, operation.target_id, operation.execution_id
    ):
        raise ValueError("SCENARIO_EXECUTION_HASH_MISMATCH")
    token = scenario_hash((baseline.baseline_id, operation))
    changed = []
    affected: set[str] = set()
    inputs = baseline.inputs
    if operation.target_kind == "timetable":
        target = next(d for d in inputs.timetables if d.network_id == operation.target_id)
        source = next(r for r in target.timetable.runs if r.run_id == operation.execution_id)
        if source.service_date != operation.service_date:
            raise ValueError("SCENARIO_SERVICE_DATE_MISMATCH")
        replacements: dict[str, TimetableRun] = {}
        physical_ids = timetable_physical_ids(inputs.timetables)
        target_key = timetable_execution_key(source, physical_ids)
        aliases = {
            run.run_id
            for dataset in inputs.timetables
            for run in dataset.timetable.runs
            if timetable_execution_key(run, physical_ids) == target_key
        }
        if operation.template_id == "coordinate_transfer":
            if (
                operation.new_run is not None
                or operation.new_physical_service is not None
                or operation.demand_evidence_refs
            ):
                raise ValueError("COORDINATION_CANNOT_CREATE_SERVICES")
            if operation.incoming_execution_id in aliases:
                raise ValueError("PHYSICAL_ALIAS_IS_NOT_A_TRANSFER")
            for dataset in inputs.timetables:
                for run in dataset.timetable.runs:
                    if run.run_id in aliases:
                        updated = _shift_run(run, operation)
                        if run.run_id in replacements and replacements[run.run_id] != updated:
                            raise ValueError("INCONSISTENT_EXECUTION_COPIES")
                        replacements[run.run_id] = updated
            affected.update(replacements)
        else:
            new_run, identity = operation.new_run, operation.new_physical_service
            if (
                not operation.demand_evidence_refs
                or new_run is None
                or identity is None
                or operation.timing_model != "whole_execution"
            ):
                raise ValueError("ADDITIONAL_DEPARTURE_REQUIRES_DEMAND_PHYSICAL_EXECUTION")
            expected = _shift_run(source, operation)
            if (
                new_run.run_id == source.run_id
                or new_run.snapshot_id != source.snapshot_id
                or new_run.service_date != operation.service_date
                or new_run.active is not True
                or not new_run.calendar_evidence_refs
                or not all(e.evidence_refs for e in new_run.events)
                or new_run.transport_type != source.transport_type
                or identity.run_ids != (new_run.run_id,)
            ):
                raise ValueError("INVALID_ADDITIONAL_EXECUTION")

            if stop_pattern(new_run) != stop_pattern(expected) or any(
                r.run_id == new_run.run_id for d in inputs.timetables for r in d.timetable.runs
            ):
                raise ValueError("ADDITIONAL_EXECUTION_MUST_MATCH_APPROVED_TRIP_COPY")
            new_key = (
                new_run.snapshot_id,
                new_run.service_date,
                "physical",
                identity.physical_service_id,
            )
            if any(
                timetable_execution_key(run, physical_ids) == new_key
                for dataset in inputs.timetables
                for run in dataset.timetable.runs
            ):
                raise ValueError("ADDITIONAL_EXECUTION_REUSES_PHYSICAL_IDENTITY")
            needed = {e.sequence for e in new_run.events[:-1] if e.pickup}
            if not needed <= {d.event_sequence for d in identity.departures}:
                raise ValueError("COMPLETE_NEW_PHYSICAL_DEPARTURES_REQUIRED")
            affected.add(new_run.run_id)
        datasets = []
        for dataset in inputs.timetables:
            runs = tuple(replacements.get(r.run_id, r) for r in dataset.timetable.runs)
            physical = dataset.physical_services
            if operation.template_id == "additional_departure" and any(
                r.run_id in aliases for r in dataset.timetable.runs
            ):
                assert operation.new_run is not None and operation.new_physical_service is not None
                runs += (operation.new_run,)
                physical += (operation.new_physical_service,)
            if runs != dataset.timetable.runs:
                before_runs = {r.run_id: r for r in dataset.timetable.runs}
                for run in runs:
                    old = before_runs.get(run.run_id)
                    for i, event in enumerate(run.events):
                        previous = old.events[i] if old else None
                        if old is None or event != previous:
                            changed.append(
                                ScenarioEventChange(
                                    dataset.network_id,
                                    run.run_id,
                                    str(event.sequence),
                                    previous.arrival_at if previous else None,
                                    previous.departure_at if previous else None,
                                    event.arrival_at,
                                    event.departure_at,
                                )
                            )
                table = replace(
                    dataset.timetable,
                    runs=runs,
                    scope=replace(
                        dataset.timetable.scope, timetable_version="scenario-overlay:" + token
                    ),
                )
                normalized, _, _ = normalize_physical_services(
                    table, dataset.transport_modes, physical
                )
                dataset = replace(dataset, timetable=table, physical_services=physical)
                if (
                    operation.template_id == "coordinate_transfer"
                    and dataset.network_id == operation.target_id
                ):
                    _confirm_transfer(dataset, normalized, operation)
            datasets.append(dataset)
        inputs = replace(inputs, timetables=tuple(datasets))
    else:
        if (
            operation.template_id != "coordinate_transfer"
            or operation.new_run is not None
            or operation.new_physical_service is not None
        ):
            raise ValueError("CARGO_OPERATION_NOT_REGISTERED_BY_ALLOWLIST")
        job = next(j for j in inputs.cargo if j.job_id == operation.target_id)
        execution = next(e for e in job.executions if e.execution_id == operation.execution_id)
        incoming_execution = next(
            (e for e in job.executions if e.execution_id == operation.incoming_execution_id), None
        )
        if (
            execution.service_date != operation.service_date
            or incoming_execution is None
            or incoming_execution.execution_id == execution.execution_id
        ):
            raise ValueError("CARGO_SCENARIO_EXECUTION_SCOPE_MISMATCH")
        action_map = {a.action_id: a for a in job.actions}
        if (
            operation.event_id not in execution.action_ids
            or operation.incoming_event_id not in incoming_execution.action_ids
            or operation.event_id not in action_map
            or operation.incoming_event_id not in action_map
        ):
            raise ValueError("CARGO_SCENARIO_EVENT_NOT_FOUND")
        anchor = action_map[operation.event_id]
        incoming = action_map[operation.incoming_event_id]
        if anchor.kind != "movement" or incoming.kind != "movement" or anchor.scheduled_at is None:
            raise ValueError("CARGO_COORDINATION_SHIFTS_DEPARTURES_NOT_HANDLING")
        updated_actions = []
        for action in job.actions:
            if action.action_id in execution.action_ids:
                if action.service_id != execution.execution_id:
                    raise ValueError("CARGO_EXECUTION_BINDING_MISMATCH")
                if action.scheduled_at is not None and (
                    operation.timing_model == "whole_execution"
                    or action.scheduled_at >= anchor.scheduled_at
                ):
                    shifted = _shift_time(action.scheduled_at, operation.shift_seconds)
                    changed.append(
                        ScenarioEventChange(
                            job.job_id,
                            execution.execution_id,
                            action.action_id,
                            action.scheduled_at,
                            action.scheduled_at,
                            shifted,
                            shifted,
                        )
                    )
                    action = replace(
                        action,
                        scheduled_at=shifted,
                        evidence_refs=tuple(
                            sorted(set(action.evidence_refs + operation.evidence_refs))
                        )
                        if action.evidence_refs
                        else (),
                    )
            updated_actions.append(action)
        updated_job = replace(job, actions=tuple(updated_actions))
        # Existing cargo search verifies actual loading, waiting, compatibility and
        # completion. Partial terminal processing still permits a known transfer prefix.
        result = analyze_intercity_cargo(
            job.scope,
            job.started_at,
            job.profile,
            job.states,
            updated_job.actions,
            job.readiness,
            config=job.config,
            rules=job.rules,
            resource_limit=job.resource_limit,
        )

        def paired(path: object) -> bool:
            from a2_engine.freight.delivery.models import CargoDeliveryPath

            if not isinstance(path, CargoDeliveryPath):
                return False
            return (
                operation.incoming_event_id in path.action_ids
                and operation.event_id in path.action_ids
                and path.action_ids.index(operation.incoming_event_id)
                < path.action_ids.index(operation.event_id)
            )

        if not any(paired(path) for path in result.candidates + result.incomplete_paths):
            raise ValueError("CARGO_SCENARIO_TRANSFER_NOT_CONFIRMED")
        jobs = []
        source_movements = frozenset(
            (a.scheduled_at, a.seconds, a.mode)
            for a in job.actions
            if a.action_id in execution.action_ids and a.kind == "movement"
        )
        for other in inputs.cargo:
            if other.job_id == job.job_id:
                jobs.append(updated_job)
                continue
            shared = next(
                (
                    e
                    for e in other.executions
                    if e.execution_id == execution.execution_id
                    and e.service_date == execution.service_date
                ),
                None,
            )
            if shared is None or other.scope.context != job.scope.context:
                jobs.append(other)
                continue
            movements = frozenset(
                (a.scheduled_at, a.seconds, a.mode)
                for a in other.actions
                if a.action_id in shared.action_ids and a.kind == "movement"
            )
            if movements != source_movements:
                raise ValueError("INCONSISTENT_SHARED_CARGO_EXECUTION")
            cloned_actions = []
            for action in other.actions:
                if (
                    action.action_id in shared.action_ids
                    and action.scheduled_at is not None
                    and (
                        operation.timing_model == "whole_execution"
                        or action.scheduled_at >= anchor.scheduled_at
                    )
                ):
                    shifted = _shift_time(action.scheduled_at, operation.shift_seconds)
                    changed.append(
                        ScenarioEventChange(
                            other.job_id,
                            shared.execution_id,
                            action.action_id,
                            action.scheduled_at,
                            action.scheduled_at,
                            shifted,
                            shifted,
                        )
                    )
                    action = replace(
                        action,
                        scheduled_at=shifted,
                        evidence_refs=tuple(
                            sorted(set(action.evidence_refs + operation.evidence_refs))
                        )
                        if action.evidence_refs
                        else (),
                    )
                cloned_actions.append(action)
            jobs.append(replace(other, actions=tuple(cloned_actions)))
        inputs = replace(inputs, cargo=tuple(jobs))
        affected.add(execution.execution_id)
    if not changed:
        raise ValueError("SCENARIO_NO_TRANSPORT_CHANGE")
    return ScenarioOverlay(
        "overlay:" + token,
        baseline.baseline_id,
        operation.operation_id,
        inputs,
        tuple(sorted(changed, key=lambda e: (e.network_id, e.execution_id, e.event_id))),
        tuple(sorted(affected)),
    )
