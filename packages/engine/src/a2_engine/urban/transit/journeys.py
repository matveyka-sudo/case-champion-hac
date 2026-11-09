"""transit / journeys — canonical urban implementation."""

from datetime import date, datetime

from a2_engine.routing.timetable_models import TimetablePath
from a2_engine.shared.config import UrbanJourneyRules
from a2_engine.urban.models import UrbanDestination, UrbanLeg, UrbanPoint
from a2_engine.urban.transit.models import UrbanTransitNetwork


def _identity_json(value: object) -> object:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, frozenset):
        return sorted(value)
    raise TypeError("UNSUPPORTED_ROUTE_IDENTITY_VALUE")


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
