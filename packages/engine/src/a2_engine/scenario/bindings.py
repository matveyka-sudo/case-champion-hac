"""Accepted timetable identities shared by the overlay and resource check."""

from datetime import date
from typing import Literal

from a2_engine.intercity.analysis.models import PublicMode
from a2_engine.routing.timetable_models import TimetableRun
from a2_engine.scenario.models import ScenarioTimetable

TimetableExecutionKey = tuple[str, date, Literal["physical", "run"], str]


def timetable_physical_ids(timetables: tuple[ScenarioTimetable, ...]) -> dict[str, str]:
    """Resolve aliases globally and validate their accepted timetable data."""
    physical_ids: dict[str, str] = {}
    for dataset in timetables:
        for group in dataset.physical_services:
            for run_id in group.run_ids:
                if (
                    physical_ids.setdefault(run_id, group.physical_service_id)
                    != group.physical_service_id
                ):
                    raise ValueError("CONFLICTING_SCENARIO_PHYSICAL_IDENTITY")
    known_modes: dict[TimetableExecutionKey, PublicMode] = {}
    physical_runs: dict[TimetableExecutionKey, TimetableRun] = {}
    for dataset in timetables:
        modes = {binding.transport_type: binding.mode for binding in dataset.transport_modes}
        if len(modes) != len(dataset.transport_modes):
            raise ValueError("DUPLICATE_TRANSPORT_TYPE_BINDING")
        for run in dataset.timetable.runs:
            key = timetable_execution_key(run, physical_ids)
            if key[2] == "physical":
                other = physical_runs.setdefault(key, run)
                if other is not run and (
                    other.active != run.active or stop_pattern(other) != stop_pattern(run)
                ):
                    raise ValueError("INCONSISTENT_SCENARIO_PHYSICAL_EXECUTION_COPIES")
            mode = modes.get(run.transport_type)
            # An unbound occurrence must not erase a confirmed binding.
            if mode is None:
                continue
            if known_modes.setdefault(key, mode) != mode:
                raise ValueError("INCONSISTENT_SCENARIO_EXECUTION_MODES")
    return physical_ids


def timetable_execution_key(
    run: TimetableRun, physical_ids: dict[str, str]
) -> TimetableExecutionKey:
    run_id = run.run_id
    physical_id = physical_ids.get(run_id)
    if physical_id is None:
        return run.snapshot_id, run.service_date, "run", run_id
    return run.snapshot_id, run.service_date, "physical", physical_id


def stop_pattern(run: TimetableRun) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            event.sequence,
            event.node_id,
            event.arrival_at,
            event.departure_at,
            event.pickup,
            event.dropoff,
        )
        for event in run.events
    )
