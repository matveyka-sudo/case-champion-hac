"""Worker-only proof of a minimum in a finite, selected input graph."""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, replace
from datetime import date

from a2_contracts.common import canonical_hash
from a2_contracts.v2.journeys import JourneyResult, LastReturn, Leg
from a2_engine import (
    UrbanAnalysis,
    UrbanCandidate,
    UrbanCoverage,
    UrbanDestination,
    UrbanJourneyRules,
    UrbanPoint,
    UrbanTransitNetwork,
    search_urban_last_return,
)

from a2_api.adapters.product_inputs import UrbanInputs
from a2_api.adapters.product_projection import Projection


def bounded_coverage(
    coverage: tuple[UrbanCoverage, ...],
    destinations: Sequence[UrbanDestination],
    candidates: Sequence[UrbanCandidate],
    completed: Mapping[tuple[str, str], bool] | None = None,
) -> tuple[UrbanCoverage, ...]:
    roster: dict[str, set[str]] = defaultdict(set)
    for destination in destinations:
        for category in destination.categories:
            roster[category].add(destination.destination_id)
    observed: dict[tuple[str, str], set[str]] = defaultdict(set)
    for candidate in candidates:
        observed[(candidate.point_id, candidate.category)].add(candidate.destination_id)
    result = []
    for row in coverage:
        key = (row.scope.point_id, row.scope.category)
        expected = frozenset(roster[row.scope.category])
        proven = (
            bool(expected)
            and expected == observed[key]
            and (completed is None or completed.get(key, False))
        )
        result.append(
            replace(
                row,
                catalog_complete=True,
                catalog_destination_ids=expected,
                search_complete=proven,
            )
        )
    return tuple(result)


def bounded_network(
    network: UrbanTransitNetwork,
    point: UrbanPoint,
    destination: UrbanDestination,
    stops: frozenset[str],
    *,
    reverse: bool = False,
) -> UrbanTransitNetwork:
    """Use just this OD's direct walk, stop access and selected transfers."""
    start, origin_access = point.origin_node_id, point.access_node_id
    target = destination.physical_node_id or destination.access_node_id
    destination_access = destination.access_node_id
    if reverse:
        start, target = target, start
        origin_access, destination_access = destination_access, origin_access
    paths = tuple(
        path
        for path in network.timetable.footpaths
        if (path.from_node_id, path.to_node_id)
        in {
            (start, origin_access),
            (origin_access, destination_access),
            (destination_access, target),
        }
        or (path.from_node_id == origin_access and path.to_node_id in stops)
        or (path.from_node_id in stops and path.to_node_id == destination_access)
        or (path.from_node_id in stops and path.to_node_id in stops)
    )
    return replace(network, timetable=replace(network.timetable, footpaths=paths))


def project_bounded_returns(
    out: Projection,
    analysis: UrbanAnalysis,
    network: UrbanTransitNetwork,
    local_day: date,
    inputs: UrbanInputs,
    rules: UrbanJourneyRules,
    stops: frozenset[str],
) -> None:
    destinations = {destination.destination_id: destination for destination in inputs.destinations}
    requested = out.context.normalized_request
    seen = set()
    for mode in analysis.modes:
        for point in mode.points:
            for category in point.categories:
                if category.best is None or not category.minimum_proven:
                    continue
                forward = category.best.journey.candidate
                identity = (point.point.point_id, forward.destination_id)
                if identity in seen:
                    continue
                seen.add(identity)
                destination = destinations[forward.destination_id]
                result = search_urban_last_return(
                    bounded_network(network, point.point, destination, stops, reverse=True),
                    point.point,
                    destination,
                    local_day=local_day,
                    rules=rules,
                    lower_leave_at=forward.arrival_at,
                    resource_limit=inputs.resource_limit,
                )
                return_id = None
                journey = result.best_known if result.status == "available" else None
                if journey is not None:
                    return_id = "return-" + canonical_hash(
                        [*identity, journey.leave_at.isoformat()]
                    )
                    out.journeys.append(
                        JourneyResult(
                            journey_id=return_id,
                            scope="urban",
                            origin_node_id=journey.legs[0].from_node_id,
                            destination_node_id=journey.legs[-1].to_node_id,
                            requested_departure_at=journey.leave_at,
                            departure_at=journey.leave_at,
                            arrival_at=journey.arrival_at,
                            total_seconds=journey.totals.total_seconds,
                            path_seconds=journey.totals.total_seconds,
                            initial_wait_seconds=journey.totals.initial_wait_seconds,
                            total_wait_seconds=journey.totals.total_wait_seconds,
                            legs=[Leg.model_validate(asdict(leg)) for leg in journey.legs],
                            geometry=None,
                            geometry_kind="none",
                            status="available",
                            reason_code=None,
                            snapshot_id=out.context.snapshot_id,
                            completeness="partial",
                            traffic_mode=requested.traffic_mode,
                            time_kind="complete_chain",
                            reference_id=None,
                            point_id=identity[0],
                            destination_object_id=identity[1],
                            evidence_refs=list(result.evidence_refs),
                        )
                    )
                out.last_returns.append(
                    LastReturn(
                        point_id=identity[0],
                        destination_object_id=identity[1],
                        journey_id=return_id,
                        status=result.status,
                        reason_code="BOUNDED_URBAN_SAMPLE" if journey else "LAST_RETURN_UNKNOWN",
                        service_date=local_day,
                        timezone=result.timezone,
                        leave_at=result.leave_at,
                        first_departure_at=result.first_departure_at,
                        arrival_at=result.arrival_at,
                        evidence_refs=list(result.evidence_refs),
                    )
                )
