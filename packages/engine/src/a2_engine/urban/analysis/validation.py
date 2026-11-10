"""analysis / validation — canonical urban implementation."""

from collections.abc import Mapping

from a2_engine.shared.config import UrbanConfig, UrbanJourneyRules
from a2_engine.urban.journeys import UrbanJourneyTiming, validate_urban_journey
from a2_engine.urban.models import UrbanCandidate, UrbanContext, UrbanDestination, UrbanPoint


def _check_candidate(
    candidate: UrbanCandidate,
    context: UrbanContext,
    points: Mapping[str, UrbanPoint],
    destinations: Mapping[str, UrbanDestination],
    config: UrbanConfig,
    rules: UrbanJourneyRules,
) -> UrbanJourneyTiming:
    if candidate.context != context:
        raise ValueError("CANDIDATE_CONTEXT_MISMATCH")
    if candidate.point_id not in points or candidate.destination_id not in destinations:
        raise ValueError("UNKNOWN_POINT_OR_DESTINATION")
    point, destination = points[candidate.point_id], destinations[candidate.destination_id]
    if (
        candidate.category not in config.categories
        or candidate.category not in destination.categories
    ):
        raise ValueError("CATEGORY_DESTINATION_MISMATCH")
    if candidate.passenger_mode not in config.modes:
        raise ValueError("UNSUPPORTED_PASSENGER_MODE")
    if candidate.origin_node_id != point.origin_node_id or candidate.destination_node_id != (
        destination.physical_node_id or destination.access_node_id
    ):
        raise ValueError("CANDIDATE_BOUNDARY_MISMATCH")
    timing = validate_urban_journey(candidate, rules=rules)
    if point.access_distance_m > 0:
        access_walk = 0.0
        reached = False
        for leg in candidate.legs:
            if leg.kind != "walk":
                break
            access_walk += leg.duration_seconds
            if leg.to_node_id == point.access_node_id:
                reached = True
                break
        if not reached or access_walk <= 0:
            raise ValueError("ACCESS_CONNECTOR_REQUIRED")
    if destination.physical_node_id not in (None, destination.access_node_id):
        final_leg = candidate.legs[-1]
        if not (
            final_leg.kind == "walk"
            and final_leg.from_node_id == destination.access_node_id
            and final_leg.to_node_id == destination.physical_node_id
        ):
            raise ValueError("DESTINATION_CONNECTOR_REQUIRED")
    return timing
