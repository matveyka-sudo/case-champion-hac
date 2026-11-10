"""walk / analysis — canonical urban implementation."""

from collections.abc import Mapping, Sequence
from datetime import timedelta

from a2_engine.shared.config import UrbanConfig, UrbanJourneyRules
from a2_engine.urban.analysis.calculation import analyze_urban_candidates
from a2_engine.urban.models import (
    UrbanCandidate,
    UrbanCoverage,
    UrbanLeg,
    UrbanPopulationCoverage,
    UrbanSearchScope,
    UrbanThreshold,
    references,
)
from a2_engine.urban.valhalla.models import WalkResponseEnvelope, WalkRouteResult
from a2_engine.urban.valhalla.requests import make_route_request
from a2_engine.urban.valhalla.responses import normalize_walk_response
from a2_engine.urban.walk.models import WalkAnalysisResult, WalkInputs, WalkPlanIssue, WalkRoutePlan


def plan_walk_routes(
    inputs: WalkInputs, *, point_ids: Sequence[str], categories: Sequence[str]
) -> WalkRoutePlan:
    if (
        not point_ids
        or not categories
        or len(set(point_ids)) != len(point_ids)
        or len(set(categories)) != len(categories)
    ):
        raise ValueError("INVALID_PILOT_SELECTION")
    point_map = {p.entity_id: p for p in inputs.point_endpoints}
    if set(point_ids) - point_map.keys():
        raise ValueError("UNKNOWN_PILOT_POINT")
    requests, issues = [], []
    dest_map = {d.destination_id: d for d in inputs.destinations}
    for point_id in sorted(point_ids):
        origin = point_map[point_id]
        for destination in inputs.destination_endpoints:
            if not dest_map[destination.entity_id].categories.intersection(categories):
                continue
            reasons = set()
            if inputs.build is None:
                reasons.add("VALHALLA_BUILD_UNAVAILABLE")
            for ep in (origin, destination):
                if ep.connector is None or not ep.connector.confirmed:
                    reasons.add("CONNECTOR_UNCONFIRMED")
                if ep.correlation is None:
                    reasons.add("VALHALLA_CORRELATION_UNAVAILABLE")
            if reasons:
                issues.append(
                    WalkPlanIssue(point_id, destination.entity_id, tuple(sorted(reasons)))
                )
            else:
                assert inputs.build and origin.correlation and destination.correlation
                requests.append(
                    make_route_request(
                        point_id,
                        destination.entity_id,
                        inputs.build,
                        inputs.context.search_parameters_id,
                        origin.correlation,
                        destination.correlation,
                    )
                )
    return WalkRoutePlan(
        inputs, tuple(sorted(point_ids)), tuple(sorted(categories)), tuple(requests), tuple(issues)
    )


def analyze_walk_responses(
    plan: WalkRoutePlan,
    responses: Sequence[WalkResponseEnvelope],
    *,
    selected_categories: Mapping[str, str],
    config: UrbanConfig,
    rules: UrbanJourneyRules,
    thresholds: Sequence[UrbanThreshold] = (),
) -> WalkAnalysisResult:
    if set(selected_categories) != set(plan.categories):
        raise ValueError("PLAN_CATEGORY_SELECTION_MISMATCH")
    requests = {request.request_id: request for request in plan.requests}
    envelopes = {response.request_id: response for response in responses}
    if len(envelopes) != len(responses) or envelopes.keys() - requests.keys():
        raise ValueError("DUPLICATE_OR_UNKNOWN_RESPONSE")
    routes = tuple(
        normalize_walk_response(request, envelopes[request.request_id])
        for request in plan.requests
        if request.request_id in envelopes
    )
    return analyze_normalized_walk_routes(
        plan,
        routes,
        selected_categories=selected_categories,
        config=config,
        rules=rules,
        thresholds=thresholds,
    )


def analyze_normalized_walk_routes(
    plan: WalkRoutePlan,
    normalized_routes: Sequence[WalkRouteResult],
    *,
    selected_categories: Mapping[str, str],
    config: UrbanConfig,
    rules: UrbanJourneyRules,
    thresholds: Sequence[UrbanThreshold] = (),
) -> WalkAnalysisResult:
    """Reduce trusted normalized responses without retaining their raw JSON.

    The adapter must normalize each source-bound response with
    ``normalize_walk_response`` first. This seam verifies exact planned request
    identity and deterministic order; it does not create provenance or minimum proof.
    Missing responses keep the same incomplete coverage as the envelope entry point.
    """
    if set(selected_categories) != set(plan.categories):
        raise ValueError("PLAN_CATEGORY_SELECTION_MISMATCH")
    inputs = plan.inputs
    requests = {request.request_id: request for request in plan.requests}
    by_id = {route.request.request_id: route for route in normalized_routes}
    if len(by_id) != len(normalized_routes) or by_id.keys() - requests.keys():
        raise ValueError("DUPLICATE_OR_UNKNOWN_NORMALIZED_ROUTE")
    if any(route.request != requests[identity] for identity, route in by_id.items()):
        raise ValueError("NORMALIZED_ROUTE_PLAN_MISMATCH")
    routes = tuple(
        by_id[request.request_id] for request in plan.requests if request.request_id in by_id
    )
    results = {(r.request.point_id, r.request.destination_id): r for r in routes}
    point_endpoints = {p.entity_id: p for p in inputs.point_endpoints}
    dest_endpoints = {d.entity_id: d for d in inputs.destination_endpoints}
    candidates = []
    for route in routes:
        if route.state != "reachable":
            continue
        origin = point_endpoints[route.request.point_id]
        destination = dest_endpoints[route.request.destination_id]
        assert origin.connector and destination.connector
        assert origin.connector.seconds is not None and destination.connector.seconds is not None
        assert route.network_seconds is not None
        departure = inputs.context.requested_departure_at
        legs = []
        for start_node, end_node, seconds, evidence in (
            (
                origin.physical_node_id,
                origin.access_node_id,
                origin.connector.seconds,
                origin.connector.evidence_refs,
            ),
            (
                origin.access_node_id,
                destination.access_node_id,
                route.network_seconds,
                route.evidence_refs,
            ),
            (
                destination.access_node_id,
                destination.physical_node_id,
                destination.connector.seconds,
                destination.connector.evidence_refs,
            ),
        ):
            try:
                arrival = departure + timedelta(seconds=seconds)
            except OverflowError as exc:
                raise ValueError("JOURNEY_TIME_RANGE_EXCEEDED") from exc
            elapsed = (arrival - departure).total_seconds()
            if seconds > 0 and elapsed == 0:
                raise ValueError("WALK_DURATION_BELOW_TIMESTAMP_RESOLUTION")
            legs.append(
                UrbanLeg(
                    "walk",
                    "walk",
                    start_node,
                    end_node,
                    departure,
                    arrival,
                    elapsed,
                    None,
                    evidence,
                )
            )
            departure = arrival
        dest = next(d for d in inputs.destinations if d.destination_id == destination.entity_id)
        for category in sorted(dest.categories.intersection(plan.categories)):
            candidates.append(
                UrbanCandidate(
                    route.request.request_id + ":" + category,
                    origin.entity_id,
                    destination.entity_id,
                    category,
                    "walk",
                    inputs.context,
                    origin.physical_node_id,
                    destination.physical_node_id,
                    inputs.context.requested_departure_at,
                    departure,
                    tuple(legs),
                    route.evidence_refs,
                    # Network-only geometry stays on route, never mislabeled as full geometry.
                    None,
                )
            )
    coverage = []
    catalogs = {c.category: c for c in inputs.catalogs}
    for point_id in plan.point_ids:
        for category in plan.categories:
            roster = frozenset(
                d.destination_id
                for d in inputs.destinations
                if category in d.categories and d.in_service_scope
            )
            relevant = [results.get((point_id, dest)) for dest in sorted(roster)]
            complete = all(
                r is not None
                and r.minimum_proven
                and r.state in ("reachable", "confirmed_unreachable")
                for r in relevant
            )
            catalog = catalogs.get(category)
            evidence = references(
                tuple(ref for r in relevant if r for ref in r.evidence_refs)
                + (catalog.evidence_refs if catalog else ())
            )
            coverage.append(
                UrbanCoverage(
                    UrbanSearchScope(
                        inputs.context, point_id, category, "walk", rules.max_total_wait_seconds
                    ),
                    bool(catalog and catalog.complete),
                    complete and bool(evidence),
                    evidence,
                    roster,
                )
            )
    points = tuple(p for p in inputs.points if p.point_id in plan.point_ids)
    population = UrbanPopulationCoverage(
        inputs.context,
        inputs.population_coverage.complete and len(points) == len(inputs.points),
        inputs.population_coverage.excluded_population,
        inputs.population_coverage.evidence_refs,
        frozenset(p.point_id for p in points),
    )
    analysis = analyze_urban_candidates(
        inputs.context,
        points,
        inputs.destinations,
        candidates,
        coverage,
        area=inputs.area,
        selected_categories=selected_categories,
        modes=("walk",),
        population_coverage=population,
        config=config,
        rules=rules,
        thresholds=thresholds,
    )
    return WalkAnalysisResult(plan, routes, analysis)
