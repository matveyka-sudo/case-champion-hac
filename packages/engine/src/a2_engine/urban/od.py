"""Lazy directed point-to-point tasks and compact, incremental tau collection.

No institution destination is constructed. Missing observations remain unknown;
all slice identities survive even an empty or interrupted execution.
"""

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import cached_property

from a2_engine.routing.road import RoadEndpoint, RoadRequest, RoadResult, road_hash
from a2_engine.routing.timetable import solve_timetable
from a2_engine.routing.timetable_models import Timetable
from a2_engine.shared.config import UrbanTauRules
from a2_engine.urban.models import UrbanPoint, references, utc_timestamp
from a2_engine.urban.tau import _local_instant
from a2_engine.urban.tau_models import (
    UrbanODPair,
    UrbanODScope,
    UrbanTauDay,
    UrbanTauSlice,
    UrbanTauWeek,
    city_zone,
)


@dataclass(frozen=True)
class UrbanODTask:
    plan_id: str
    scope: UrbanODScope
    profile_hash: str
    departure_at: datetime
    origin: UrbanPoint
    destination: UrbanPoint

    @property
    def task_id(self) -> str:
        return road_hash(
            [
                self.plan_id,
                self.profile_hash,
                self.departure_at.isoformat(),
                self.origin.point_id,
                self.destination.point_id,
            ]
        )


@dataclass(frozen=True)
class UrbanODPlan:
    scope: UrbanODScope
    points: tuple[UrbanPoint, ...]
    week_start: date
    reference_at: datetime
    calendar: tuple[UrbanTauDay, ...]
    departures: tuple[datetime, ...]
    profile_hash: str
    evidence_refs: tuple[str, ...]

    @cached_property
    def plan_id(self) -> str:
        return road_hash(
            [
                self.scope,
                self.points,
                self.week_start.isoformat(),
                self.reference_at.isoformat(),
                self.calendar,
                [d.isoformat() for d in self.departures],
                self.profile_hash,
                self.evidence_refs,
            ]
        )

    def tasks(self) -> Iterator[UrbanODTask]:
        for departure in self.departures:
            for origin in self.points:
                for destination in self.points:
                    if origin.point_id == destination.point_id:
                        continue
                    # Only a confirmed zero product permits skipping routing.
                    if origin.population == 0 or destination.population == 0:
                        continue
                    yield UrbanODTask(
                        self.plan_id, self.scope, self.profile_hash, departure, origin, destination
                    )

    def batches(self, size: int) -> Iterator[tuple[UrbanODTask, ...]]:
        if isinstance(size, bool) or not isinstance(size, int) or size < 1:
            raise ValueError("POSITIVE_OD_BATCH_SIZE_REQUIRED")
        batch = []
        for task in self.tasks():
            batch.append(task)
            if len(batch) == size:
                yield tuple(batch)
                batch = []
        if batch:
            yield tuple(batch)


def plan_urban_od_week(
    scope: UrbanODScope,
    points: Sequence[UrbanPoint],
    *,
    week_start: date,
    reference_at: datetime,
    calendar: Sequence[UrbanTauDay],
    rules: UrbanTauRules,
    profile_hash: str,
    evidence_refs: tuple[str, ...],
) -> UrbanODPlan:
    if scope.traffic_mode != "static":
        raise ValueError("OD_TRAFFIC_PROFILE_UNSUPPORTED")
    reference = utc_timestamp(reference_at)
    if week_start + timedelta(days=7) > reference.astimezone(city_zone(scope.timezone)).date():
        raise ValueError("OD_WEEK_NOT_COMPLETED")
    roster = tuple(sorted(points, key=lambda p: p.point_id))
    if (
        len({p.point_id for p in roster}) != len(roster)
        or frozenset(p.point_id for p in roster) != scope.point_ids
    ):
        raise ValueError("OD_POINT_ROSTER_MISMATCH")
    if any(p.territory_id != scope.city_id for p in roster):
        raise ValueError("OD_CITY_MISMATCH")
    if (
        week_start.weekday() != 0
        or rules.methodology_version != scope.versions.methodology_version
        or rules.max_total_wait_seconds != scope.max_total_wait_seconds
    ):
        raise ValueError("OD_WEEK_RULES_MISMATCH")
    if not profile_hash or not evidence_refs:
        raise ValueError("OD_PROFILE_AND_SCOPE_EVIDENCE_REQUIRED")
    days = tuple(sorted(calendar, key=lambda c: c.service_date))
    if len({c.service_date for c in days}) != len(days) or any(
        not week_start <= c.service_date < week_start + timedelta(days=7) for c in days
    ):
        raise ValueError("OD_CALENDAR_MISMATCH")
    departures = tuple(
        _local_instant(week_start + timedelta(days=i), clock, city_zone(scope.timezone))
        for i in range(rules.tau_days)
        for clock in rules.tau_times
    )
    return UrbanODPlan(
        scope,
        roster,
        week_start,
        reference,
        days,
        departures,
        profile_hash,
        references(evidence_refs),
    )


@dataclass(frozen=True)
class UrbanODObservation:
    task: UrbanODTask
    pair: UrbanODPair
    best_known_seconds: float | None = None

    def __post_init__(self) -> None:
        if (self.pair.origin_point_id, self.pair.destination_point_id) != (
            self.task.origin.point_id,
            self.task.destination.point_id,
        ):
            raise ValueError("OD_OBSERVATION_PAIR_MISMATCH")
        if self.best_known_seconds is not None:
            from a2_engine.shared.results import finite_number

            finite_number(self.best_known_seconds, "OD best known")


def od_road_request(
    task: UrbanODTask, entries: Mapping[str, RoadEndpoint], exits: Mapping[str, RoadEndpoint]
) -> RoadRequest:
    origin, destination = entries[task.origin.point_id], exits[task.destination.point_id]
    build = origin.build
    expected_costing = {"walk": "pedestrian", "private_car": "auto"}.get(task.scope.passenger_mode)
    if (
        build.costing != expected_costing
        or build.profile_hash != task.profile_hash
        or build.snapshot_id != task.scope.versions.snapshot_id
        or build.graph_version != task.scope.versions.graph_version
        or task.scope.traffic_mode != "static"
        or task.scope.versions.traffic_version != "static:" + build.build_id
    ):
        raise ValueError("OD_ROAD_PROFILE_MISMATCH")
    if (
        origin.physical_node_id != task.origin.origin_node_id
        or origin.access_node_id != task.origin.access_node_id
        or destination.physical_node_id != task.destination.origin_node_id
        or destination.access_node_id != task.destination.access_node_id
    ):
        raise ValueError("OD_PHYSICAL_BOUNDARY_MISMATCH")
    return RoadRequest(
        task.origin.point_id,
        task.destination.point_id,
        origin,
        destination,
        task.departure_at,
        task.scope.search_parameters_id,
        task.scope.timezone,
    )


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
