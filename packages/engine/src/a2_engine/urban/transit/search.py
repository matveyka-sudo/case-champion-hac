"""transit / search — canonical urban implementation."""

import hashlib
import json
from dataclasses import asdict
from typing import Literal

from a2_engine.routing.timetable import solve_timetable
from a2_engine.shared.config import UrbanJourneyRules
from a2_engine.shared.results import MetricResult
from a2_engine.urban.journeys import validate_urban_journey
from a2_engine.urban.models import UrbanCandidate, UrbanDestination, UrbanPoint, references
from a2_engine.urban.transit.journeys import _identity_json, check_urban_inputs, path_legs
from a2_engine.urban.transit.models import UrbanTransitNetwork, UrbanTransitResult


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
