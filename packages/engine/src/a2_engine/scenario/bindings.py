"""Accepted timetable identities shared by the overlay and resource check."""

from datetime import date
from typing import Literal

from a2_engine.routing.timetable_models import TimetableRun
from a2_engine.scenario.models import ScenarioTimetable

TimetableExecutionKey = tuple[str, date, Literal["physical", "run"], str]


def timetable_physical_ids(timetables: tuple[ScenarioTimetable, ...]) -> dict[str, str]:
    physical_ids: dict[str, str] = {}
    for dataset in timetables:
        for group in dataset.physical_services:
            for run_id in group.run_ids:
                if (
                    physical_ids.setdefault(run_id, group.physical_service_id)
                    != group.physical_service_id
                ):
                    raise ValueError("CONFLICTING_SCENARIO_PHYSICAL_IDENTITY")
    return physical_ids


def timetable_execution_key(
    run: TimetableRun, physical_ids: dict[str, str]
) -> TimetableExecutionKey:
    physical_id = physical_ids.get(run.run_id)
    if physical_id is None:
        return run.snapshot_id, run.service_date, "run", run.run_id
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
