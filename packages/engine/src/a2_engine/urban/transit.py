"""Urban endpoint and version rules around the generic event timetable solver."""

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Literal

from a2_engine.routing.timetable import solve_timetable
from a2_engine.routing.timetable_models import Timetable, TimetablePath, TimetableSearchResult
from a2_engine.shared.config import UrbanJourneyRules
from a2_engine.shared.results import MetricResult
from a2_engine.urban.journeys import UrbanJourneyTiming, validate_urban_journey
from a2_engine.urban.models import (
    UrbanCandidate,
    UrbanContext,
    UrbanDestination,
    UrbanLeg,
    UrbanPoint,
    references,
)


def _identity_json(value: object) -> object:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, frozenset):
        return sorted(value)
    raise TypeError("UNSUPPORTED_ROUTE_IDENTITY_VALUE")


@dataclass(frozen=True)
class UrbanTransitNetwork:
    context: UrbanContext  # All config/geometry/population/traffic/search identities retained.
    timetable: Timetable
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        scope = self.timetable.scope
        versions = self.context.versions
        if (scope.snapshot_id, scope.graph_version, scope.calendar_version) != (
            versions.snapshot_id,
            versions.graph_version,
            versions.calendar_version,
        ):
            raise ValueError("URBAN_TIMETABLE_VERSION_MISMATCH")
        object.__setattr__(self, "evidence_refs", references(self.evidence_refs, required=True))


@dataclass(frozen=True)
class UrbanTransitResult:
    network: UrbanTransitNetwork
    point_id: str
    destination_id: str
    category: str
    passenger_mode: str
    search: TimetableSearchResult
    best_known: UrbanJourneyTiming | None
    seconds: MetricResult

    @property
    def search_complete(self) -> bool:
        return self.search.proven


def check_urban_inputs(
    network: UrbanTransitNetwork,
    point: UrbanPoint,
    destination: UrbanDestination,
    rules: UrbanJourneyRules,
    *,
    reverse: bool = False,
) -> tuple[str, str]:
    if rules.methodology_version != network.context.versions.methodology_version:
        raise ValueError("TIMETABLE_METHODOLOGY_MISMATCH")
    if point.territory_id != network.context.territory_id:
        raise ValueError("POINT_TERRITORY_MISMATCH")
    physical_destination = destination.physical_node_id or destination.access_node_id
    origin, target = (
        (physical_destination, point.origin_node_id)
        if reverse
        else (point.origin_node_id, physical_destination)
    )
    source_access, target_access = (
        (destination.access_node_id, point.access_node_id)
        if reverse
        else (point.access_node_id, destination.access_node_id)
    )
    # Normalized physical nodes are boundary leaves, not alternative stop IDs.
    for physical, access, outgoing in (
        (origin, source_access, True),
        (target, target_access, False),
    ):
        if physical == access:
            continue
        if any(event.node_id == physical for run in network.timetable.runs for event in run.events):
            raise ValueError("PHYSICAL_ENDPOINT_IS_NOT_A_STOP")
        for path in network.timetable.footpaths:
            if (path.from_node_id == physical and outgoing and path.to_node_id != access) or (
                path.to_node_id == physical and not outgoing and path.from_node_id != access
            ):
                raise ValueError("PHYSICAL_ACCESS_LINK_MISMATCH")
    return origin, target


def path_legs(path: TimetablePath, evidence: tuple[str, ...]) -> tuple[UrbanLeg, ...]:
    modes = {
        "bus": "public_transport",
        "tram": "public_transport",
        "metro": "public_transport",
        "trolleybus": "public_transport",
        "public_transport": "public_transport",
        "rail": "rail",
        "water": "water",
    }
    legs = []
    for step in path.steps:
        mode = "walk" if step.kind == "walk" else modes.get(step.transport_type)
        if mode is None:
            raise ValueError("UNSUPPORTED_URBAN_TRANSPORT_TYPE")
        legs.append(
            UrbanLeg(
                mode,
                step.kind,
                step.from_node_id,
                step.to_node_id,
                step.start_at,
                step.end_at,
                step.duration_seconds,
                step.run_id,
                step.evidence_refs,
            )
        )
    if not legs and path.origin_node_id == path.destination_node_id:
        legs.append(
            UrbanLeg(
                "walk",
                "walk",
                path.origin_node_id,
                path.destination_node_id,
                path.departure_at,
                path.arrival_at,
                0,
                None,
                evidence,
            )
        )
    return tuple(legs)


def search_urban_transit(
    network: UrbanTransitNetwork,
    point: UrbanPoint,
    destination: UrbanDestination,
    *,
    category: str,
    rules: UrbanJourneyRules,
    passenger_mode: Literal["public_transport", "walk"] = "public_transport",
    resource_limit: int | None = None,
) -> UrbanTransitResult:
    origin, target = check_urban_inputs(network, point, destination, rules)
    if category not in destination.categories or passenger_mode not in ("public_transport", "walk"):
        raise ValueError("URBAN_TRANSIT_CATEGORY_OR_MODE_MISMATCH")
    result = solve_timetable(
        network.timetable,
        origin,
        target,
        network.context.requested_departure_at,
        max_wait_seconds=rules.max_total_wait_seconds,
        allow_transit=passenger_mode == "public_transport",
        resource_limit=resource_limit,
    )
    best = None
    if result.best_known is not None:
        path = result.best_known
        evidence = references(
            result.evidence_refs
            + network.evidence_refs
            + point.source_refs
            + destination.source_refs
        )
        fingerprint = json.dumps(
            [
                asdict(network.context),
                point.point_id,
                destination.destination_id,
                category,
                passenger_mode,
                asdict(network.timetable.scope),
                asdict(path),
                result.max_wait_seconds,
            ],
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            default=_identity_json,
        )
        journey_id = hashlib.sha256(fingerprint.encode()).hexdigest()
        candidate = UrbanCandidate(
            journey_id,
            point.point_id,
            destination.destination_id,
            category,
            passenger_mode,
            network.context,
            origin,
            target,
            path.departure_at,
            path.arrival_at,
            path_legs(path, evidence),
            evidence,
        )
        best = validate_urban_journey(candidate, rules=rules)
        if not best.admissible:
            raise ValueError("TIMETABLE_WAIT_BUDGET_INCONSISTENCY")
    seconds = (
        MetricResult(best.total_seconds, "available")
        if best is not None and result.proven
        else MetricResult(
            None,
            "unknown",
            "NO_ROUTE_WITHIN_SCOPE"
            if result.proven
            else result.reasons[0]
            if result.reasons
            else "TIMETABLE_INCOMPLETE",
        )
    )
    return UrbanTransitResult(
        network,
        point.point_id,
        destination.destination_id,
        category,
        passenger_mode,
        result,
        best,
        seconds,
    )
