"""car / analysis — canonical urban implementation."""

from collections.abc import Mapping, Sequence
from typing import Literal, cast

from a2_engine.routing.road import (
    RoadRequest,
    RoadResponse,
    RoadResult,
    normalize_road_response,
    road_arrival,
)
from a2_engine.shared.config import UrbanConfig, UrbanJourneyRules
from a2_engine.urban.analysis.calculation import analyze_urban_candidates
from a2_engine.urban.car.models import CarAnalysisResult, CarInputs, CarPlan
from a2_engine.urban.models import (
    UrbanCandidate,
    UrbanContext,
    UrbanCoverage,
    UrbanLeg,
    UrbanSearchScope,
    UrbanThreshold,
    references,
)


def plan_car_routes(inputs: CarInputs, *, categories: Sequence[str]) -> CarPlan:
    if not categories or len(set(categories)) != len(categories):
        raise ValueError("INVALID_CAR_CATEGORIES")
    entries, exits = dict(inputs.entries), dict(inputs.exits)
    requests, issues = [], []
    for point in sorted(inputs.points, key=lambda p: p.point_id):
        for destination in sorted(inputs.destinations, key=lambda d: d.destination_id):
            if not destination.in_service_scope or not destination.categories.intersection(
                categories
            ):
                continue
            if point.point_id not in entries or destination.destination_id not in exits:
                issues.append(
                    (point.point_id, destination.destination_id, "CAR_ACCESS_UNCONFIRMED")
                )
                continue
            try:
                request = RoadRequest(
                    point.point_id,
                    destination.destination_id,
                    entries[point.point_id],
                    exits[destination.destination_id],
                    inputs.context.requested_departure_at,
                    inputs.context.search_parameters_id,
                    inputs.timezone,
                )
            except ValueError as exc:
                if str(exc) not in (
                    "VALHALLA_SUBMINUTE_ENTRY_UNSUPPORTED",
                    "AMBIGUOUS_NETWORK_ENTRY",
                ):
                    raise
                issues.append((point.point_id, destination.destination_id, str(exc)))
                continue
            requests.append(request)
    return CarPlan(inputs, tuple(sorted(categories)), tuple(requests), tuple(issues))


def car_candidate(route: RoadResult, context: UrbanContext, category: str) -> UrbanCandidate:
    if (
        route.state != "reachable"
        or route.network_seconds is None
        or route.request.origin.build.costing != "auto"
    ):
        raise ValueError("REACHABLE_AUTO_ROUTE_REQUIRED")
    request = route.request
    if context.requested_departure_at != request.departure_at:
        raise ValueError("CAR_DEPARTURE_MISMATCH")
    start = request.departure_at
    legs = []
    for kind, mode, origin, destination, seconds, evidence in (
        (
            "walk",
            "walk",
            request.origin.physical_node_id,
            request.origin.access_node_id,
            request.origin.seconds,
            request.origin.evidence_refs,
        ),
        (
            "ride",
            "private_car",
            request.origin.access_node_id,
            request.destination.access_node_id,
            route.network_seconds,
            route.evidence_refs,
        ),
        (
            "walk",
            "walk",
            request.destination.access_node_id,
            request.destination.physical_node_id,
            request.destination.seconds,
            request.destination.evidence_refs,
        ),
    ):
        end = road_arrival(start, seconds)
        legs.append(
            UrbanLeg(
                mode,
                cast(Literal["walk", "ride", "wait_initial", "wait_transfer"], kind),
                origin,
                destination,
                start,
                end,
                (end - start).total_seconds(),
                None,
                evidence,
            )
        )
        start = end
    return UrbanCandidate(
        request.request_id + ":" + category,
        request.origin_id,
        request.destination_id,
        category,
        "private_car",
        context,
        request.origin.physical_node_id,
        request.destination.physical_node_id,
        request.departure_at,
        start,
        tuple(legs),
        route.evidence_refs,
        None,
    )


def analyze_car_responses(
    plan: CarPlan,
    responses: Sequence[RoadResponse],
    *,
    selected_categories: Mapping[str, str],
    config: UrbanConfig,
    rules: UrbanJourneyRules,
    thresholds: Sequence[UrbanThreshold] = (),
) -> CarAnalysisResult:
    if set(selected_categories) != set(plan.categories):
        raise ValueError("CAR_PLAN_SELECTION_MISMATCH")
    requests = {r.request_id: r for r in plan.requests}
    received: dict[str, RoadResponse] = {}
    for response in responses:
        if response.request_id not in requests:
            raise ValueError("UNKNOWN_CAR_RESPONSE")
        if response.request_id in received and received[response.request_id] != response:
            raise ValueError("CONFLICTING_CAR_RESPONSE")
        received[response.request_id] = response
    routes = tuple(
        normalize_road_response(requests[key], response)
        for key, response in sorted(received.items())
    )
    pairs = {(r.request.origin_id, r.request.destination_id): r for r in routes}
    inputs = plan.inputs
    candidates = tuple(
        car_candidate(r, inputs.context, category)
        for r in routes
        if r.state == "reachable"
        for d in inputs.destinations
        if d.destination_id == r.request.destination_id
        for category in sorted(d.categories.intersection(plan.categories))
    )
    catalogs = {c: (full, evidence) for c, full, evidence in inputs.catalogs}
    coverage = []
    for point in inputs.points:
        for category in plan.categories:
            roster = frozenset(
                d.destination_id
                for d in inputs.destinations
                if category in d.categories and d.in_service_scope
            )
            relevant = [pairs.get((point.point_id, key)) for key in sorted(roster)]
            full, catalog_evidence = catalogs.get(category, (False, ()))
            evidence = references(
                catalog_evidence + tuple(e for r in relevant if r for e in r.evidence_refs)
            )
            complete = all(
                r is not None
                and r.minimum_proven
                and r.state in ("reachable", "confirmed_unreachable")
                for r in relevant
            )
            coverage.append(
                UrbanCoverage(
                    UrbanSearchScope(
                        inputs.context,
                        point.point_id,
                        category,
                        "private_car",
                        rules.max_total_wait_seconds,
                    ),
                    full,
                    complete and bool(evidence),
                    evidence,
                    roster,
                )
            )
    analysis = analyze_urban_candidates(
        inputs.context,
        inputs.points,
        inputs.destinations,
        candidates,
        coverage,
        area=inputs.area,
        selected_categories=selected_categories,
        modes=("private_car",),
        population_coverage=inputs.population,
        config=config,
        rules=rules,
        thresholds=thresholds,
    )
    return CarAnalysisResult(plan, routes, analysis)
