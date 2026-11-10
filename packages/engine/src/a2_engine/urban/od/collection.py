"""od / collection — canonical urban implementation."""

from collections.abc import Iterable
from datetime import datetime

from a2_engine.routing.road import RoadResult, road_hash
from a2_engine.routing.timetable import solve_timetable
from a2_engine.routing.timetable_models import Timetable
from a2_engine.urban.models import references, utc_timestamp
from a2_engine.urban.od.models import UrbanODObservation, UrbanODPlan, UrbanODTask
from a2_engine.urban.od.planning import od_road_request
from a2_engine.urban.tau.calendar import city_zone
from a2_engine.urban.tau.models import UrbanODPair, UrbanTauSlice, UrbanTauWeek


def od_road_observation(task: UrbanODTask, result: RoadResult) -> UrbanODObservation:
    # Rebuild the precise request from the independently bound task and endpoints.
    expected = od_road_request(
        task,
        {task.origin.point_id: result.request.origin},
        {task.destination.point_id: result.request.destination},
    )
    if expected != result.request:
        raise ValueError("OD_ROAD_REQUEST_MISMATCH")
    seconds = result.total_seconds
    state = result.state if result.minimum_proven else "unknown"
    pair = UrbanODPair(
        task.origin.point_id,
        task.destination.point_id,
        state,
        seconds if state == "reachable" else None,
        result.minimum_proven,
        result.minimum_proven,
        result.evidence_refs,
        result.reason_code,
    )
    return UrbanODObservation(task, pair, seconds)


def od_timetable_observation(
    task: UrbanODTask, timetable: Timetable, *, resource_limit: int | None = None
) -> UrbanODObservation:
    scope = timetable.scope
    if (
        task.scope.passenger_mode != "public_transport"
        or task.scope.traffic_mode != "static"
        or road_hash(timetable) != task.profile_hash
        or scope.snapshot_id != task.scope.versions.snapshot_id
        or scope.graph_version != task.scope.versions.graph_version
        or scope.calendar_version != task.scope.versions.calendar_version
        or scope.timezone != task.scope.timezone
    ):
        raise ValueError("OD_TIMETABLE_SCOPE_MISMATCH")
    result = solve_timetable(
        timetable,
        task.origin.origin_node_id,
        task.destination.origin_node_id,
        task.departure_at,
        max_wait_seconds=task.scope.max_total_wait_seconds,
        resource_limit=resource_limit,
    )
    seconds = result.best_known.total_seconds if result.best_known else None
    pair = UrbanODPair(
        task.origin.point_id,
        task.destination.point_id,
        result.state if result.proven else "unknown",
        seconds if result.state == "reachable" and result.proven else None,
        result.proven,
        result.proven,
        result.evidence_refs,
        result.reasons[0] if result.reasons else None,
    )
    return UrbanODObservation(task, pair, seconds)


class UrbanODCollector:
    """Store compact observations only. Identical replay is idempotent, conflict fails."""

    def __init__(self, plan: UrbanODPlan) -> None:
        self.plan = plan
        self.observations: dict[tuple[datetime, str, str], UrbanODObservation] = {}
        self._points = {p.point_id: p for p in plan.points}

    def add(self, observations: Iterable[UrbanODObservation]) -> None:
        for observation in observations:
            task = observation.task
            if (
                task.origin.point_id not in self._points
                or task.destination.point_id not in self._points
            ):
                raise ValueError("FOREIGN_OD_POINT")
            expected = UrbanODTask(
                self.plan.plan_id,
                self.plan.scope,
                self.plan.profile_hash,
                utc_timestamp(task.departure_at),
                self._points[task.origin.point_id],
                self._points[task.destination.point_id],
            )
            if (
                task != expected
                or task.departure_at not in self.plan.departures
                or task.origin.point_id == task.destination.point_id
                or task.origin.population == 0
                or task.destination.population == 0
            ):
                raise ValueError("FOREIGN_OD_TASK")
            key = (task.departure_at, task.origin.point_id, task.destination.point_id)
            previous = self.observations.get(key)
            if previous is not None and previous != observation:
                raise ValueError("CONFLICTING_OD_REPLAY")
            self.observations[key] = observation

    def to_week(self) -> UrbanTauWeek:
        plan = self.plan
        slices = []
        zone = city_zone(plan.scope.timezone)
        # Missing pair rows are interpreted by calculate_urban_tau as unknown.
        for departure in plan.departures:
            local = departure.astimezone(zone)
            rows = tuple(
                observation.pair
                for key, observation in sorted(self.observations.items())
                if key[0] == departure
            )
            evidence = references(
                plan.evidence_refs + tuple(e for row in rows for e in row.evidence_refs)
            )
            slices.append(
                UrbanTauSlice(
                    plan.scope,
                    local.date(),
                    local.time().replace(tzinfo=None),
                    departure,
                    rows,
                    True,
                    evidence,
                )
            )
        return UrbanTauWeek(
            plan.plan_id,
            plan.scope,
            plan.week_start,
            plan.calendar,
            tuple(slices),
            plan.evidence_refs,
            False,
            (),
        )


def assemble_urban_od_week(
    plan: UrbanODPlan, observations: Iterable[UrbanODObservation]
) -> UrbanTauWeek:
    collector = UrbanODCollector(plan)
    collector.add(observations)
    return collector.to_week()
