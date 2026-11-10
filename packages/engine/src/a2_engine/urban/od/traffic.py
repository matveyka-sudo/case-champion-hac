"""Typical-only OD bridge. Static planner/identities retain their old contract."""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from datetime import date, datetime

from a2_engine.routing.road import road_hash
from a2_engine.routing.traffic.evaluation import (
    TrafficEndpoint,
    TrafficPathResult,
    TrafficRequest,
    validate_traffic_result,
)
from a2_engine.routing.traffic.models import TrafficSnapshot, traffic_id
from a2_engine.shared.config import UrbanTauRules
from a2_engine.urban.car.traffic import check_traffic_context
from a2_engine.urban.models import UrbanContext, UrbanPoint
from a2_engine.urban.od._planning import _plan_od_scope
from a2_engine.urban.od.collection import UrbanODCollector
from a2_engine.urban.od.models import UrbanODObservation, UrbanODPlan, UrbanODTask
from a2_engine.urban.tau.models import UrbanODPair, UrbanODScope, UrbanTauDay, UrbanTauWeek


def plan_typical_od_week(
    scope: UrbanODScope,
    points: Sequence[UrbanPoint],
    snapshot: TrafficSnapshot,
    *,
    week_start: date,
    reference_at: datetime,
    calendar: Sequence[UrbanTauDay],
    rules: UrbanTauRules,
    evidence_refs: tuple[str, ...],
    search_scope_id: str,
) -> UrbanODPlan:
    if (
        scope.passenger_mode != "private_car"
        or scope.traffic_mode != "typical"
        or snapshot.scope.mode != "typical"
        or scope.timezone != snapshot.scope.timezone
    ):
        raise ValueError("TYPICAL_CAR_OD_REQUIRED")
    check_traffic_context(
        UrbanContext(
            scope.city_id,
            scope.city_id,
            reference_at,
            scope.traffic_mode,
            scope.versions,
            scope.search_parameters_id,
        ),
        snapshot,
    )
    traffic_id(search_scope_id)
    return _plan_od_scope(
        scope,
        points,
        week_start=week_start,
        reference_at=reference_at,
        calendar=calendar,
        rules=rules,
        profile_hash=road_hash([snapshot.content_hash, search_scope_id]),
        evidence_refs=evidence_refs,
    )


def traffic_od_request(
    task: UrbanODTask,
    snapshot: TrafficSnapshot,
    entries: Mapping[str, TrafficEndpoint],
    exits: Mapping[str, TrafficEndpoint],
    *,
    checked_at: datetime,
    search_scope_id: str,
) -> TrafficRequest:
    if (
        task.scope.passenger_mode != "private_car"
        or task.scope.traffic_mode != "typical"
        or snapshot.scope.mode != "typical"
        or task.profile_hash != road_hash([snapshot.content_hash, search_scope_id])
        or task.scope.timezone != snapshot.scope.timezone
    ):
        raise ValueError("TYPICAL_OD_PROFILE_MISMATCH")
    check_traffic_context(
        UrbanContext(
            task.scope.city_id,
            task.scope.city_id,
            task.departure_at,
            task.scope.traffic_mode,
            task.scope.versions,
            task.scope.search_parameters_id,
        ),
        snapshot,
    )
    origin, destination = entries[task.origin.point_id], exits[task.destination.point_id]
    if (
        origin.physical_node_id,
        origin.access_node_id,
        destination.physical_node_id,
        destination.access_node_id,
    ) != (
        task.origin.origin_node_id,
        task.origin.access_node_id,
        task.destination.origin_node_id,
        task.destination.access_node_id,
    ):
        raise ValueError("TYPICAL_OD_PHYSICAL_BOUNDARY_MISMATCH")
    return TrafficRequest(
        snapshot,
        task.origin.point_id,
        task.destination.point_id,
        origin,
        destination,
        task.departure_at,
        checked_at,
        task.scope.search_parameters_id,
        search_scope_id,
    )


def traffic_od_observation(task: UrbanODTask, result: TrafficPathResult) -> UrbanODObservation:
    validate_traffic_result(result)
    request = result.request
    expected = traffic_od_request(
        task,
        request.snapshot,
        {task.origin.point_id: request.origin},
        {task.destination.point_id: request.destination},
        checked_at=request.checked_at,
        search_scope_id=request.search_scope_id,
    )
    if expected != request:
        raise ValueError("TYPICAL_OD_REQUEST_MISMATCH")
    seconds = result.known_seconds
    # A blocked supplied path does not prove that the pair is unreachable.
    proven = result.minimum_proven and seconds is not None
    pair = UrbanODPair(
        task.origin.point_id,
        task.destination.point_id,
        "reachable" if proven else "unknown",
        seconds if proven else None,
        proven,
        proven,
        result.evidence_refs + result.minimum_evidence_refs,
        None if proven else result.reasons[0] if result.reasons else "MINIMUM_TIME_UNPROVEN",
    )
    return UrbanODObservation(task, pair, seconds)


class TypicalODCollector:
    def __init__(
        self, plan: UrbanODPlan, snapshot: TrafficSnapshot, *, search_scope_id: str
    ) -> None:
        if (
            plan.scope.traffic_mode != "typical"
            or plan.profile_hash != road_hash([snapshot.content_hash, search_scope_id])
            or plan.scope.passenger_mode != "private_car"
            or plan.scope.timezone != snapshot.scope.timezone
            or snapshot.scope.mode != "typical"
        ):
            raise ValueError("TYPICAL_OD_COLLECTION_SCOPE_MISMATCH")
        check_traffic_context(
            UrbanContext(
                plan.scope.city_id,
                plan.scope.city_id,
                plan.reference_at,
                plan.scope.traffic_mode,
                plan.scope.versions,
                plan.scope.search_parameters_id,
            ),
            snapshot,
        )
        self.search_scope_id = search_scope_id
        self.plan = plan
        self.snapshot = snapshot
        self.collector = UrbanODCollector(plan)

    def add(self, results: Iterable[tuple[UrbanODTask, TrafficPathResult]]) -> None:
        for task, result in results:
            if (
                result.request.snapshot.content_hash != self.snapshot.content_hash
                or result.request.checked_at != self.plan.reference_at
                or result.request.search_scope_id != self.search_scope_id
            ):
                raise ValueError("TYPICAL_OD_IMMUTABLE_SNAPSHOT_MISMATCH")
            self.collector.add((traffic_od_observation(task, result),))

    def to_week(self) -> UrbanTauWeek:
        week = self.collector.to_week()
        accepted = self.snapshot.typical_accepted
        return replace(
            week,
            typical_profile_accepted=accepted,
            traffic_evidence_refs=self.snapshot.acceptance_evidence_refs if accepted else (),
        )
