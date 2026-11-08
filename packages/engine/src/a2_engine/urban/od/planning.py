"""od / planning — canonical urban implementation."""

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta

from a2_engine.routing.road import RoadEndpoint, RoadRequest
from a2_engine.shared.config import UrbanTauRules
from a2_engine.urban.models import UrbanPoint, references, utc_timestamp
from a2_engine.urban.od.models import UrbanODPlan, UrbanODTask
from a2_engine.urban.tau.calendar import _local_instant, city_zone
from a2_engine.urban.tau.models import UrbanODScope, UrbanTauDay


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
