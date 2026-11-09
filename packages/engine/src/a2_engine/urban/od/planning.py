"""od / planning — canonical urban implementation."""

from collections.abc import Mapping, Sequence
from datetime import date, datetime

from a2_engine.routing.road import RoadEndpoint, RoadRequest
from a2_engine.shared.config import UrbanTauRules
from a2_engine.urban.models import UrbanPoint
from a2_engine.urban.od._planning import _plan_od_scope
from a2_engine.urban.od.models import UrbanODPlan, UrbanODTask
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
    return _plan_od_scope(
        scope,
        points,
        week_start=week_start,
        reference_at=reference_at,
        calendar=calendar,
        rules=rules,
        profile_hash=profile_hash,
        evidence_refs=evidence_refs,
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
