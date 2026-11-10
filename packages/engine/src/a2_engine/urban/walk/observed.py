"""Preliminary decay of a found real walk route, separate from minimum indices.

Inputs and the normalized route must come from the accepted-data/trusted-executor
adapters. Evidence strings and caller-created dataclasses are not source authority.
The reported HTTP clock is not a certified upper bound on an exact native sum.
"""

from dataclasses import dataclass
from datetime import timedelta
from math import fsum
from typing import Literal

from a2_engine.shared.config import UrbanConfig, UrbanJourneyRules
from a2_engine.shared.results import MetricResult, finite_number
from a2_engine.shared.scoring import category_index
from a2_engine.urban.analysis.validation import _check_candidate
from a2_engine.urban.indices import select_h_seconds
from a2_engine.urban.models import UrbanCandidate, UrbanContext, UrbanLeg, references
from a2_engine.urban.valhalla.models import WalkRouteResult
from a2_engine.urban.valhalla.requests import make_route_request
from a2_engine.urban.walk.models import WalkInputs


@dataclass(frozen=True)
class ObservedRouteAssessment:
    context: UrbanContext
    request_id: str
    point_id: str
    destination_id: str
    category: str
    observed_total_seconds: float | None
    h_seconds: MetricResult
    observed_route_accessibility: MetricResult
    canonical_category_index: MetricResult
    complete_physical_time_confirmed: bool
    # None preserves an unmeasured access-to-native offset; it is not a zero.
    projection_offset_seconds: float | None
    evidence_refs: tuple[str, ...]
    limitations: tuple[str, ...]

    @property
    def completeness(self) -> Literal["partial"]:
        return "partial"

    @property
    def minimum_proven(self) -> bool:
        return False

    @property
    def ranking_eligible(self) -> bool:
        return False

    @property
    def certified_index_lower_bound(self) -> bool:
        return False


def assess_observed_walk_route(
    inputs: WalkInputs,
    route: WalkRouteResult,
    *,
    category: str,
    config: UrbanConfig,
    rules: UrbanJourneyRules,
) -> ObservedRouteAssessment:
    """Assess one found object without asserting nearest-object/search closure.

    Both physical approaches must be source-confirmed. An already validated
    six-decimal native correlation may support an explicitly uncertain estimate;
    its unmeasured offset stays None. Different/unbound endpoints are rejected.
    H comes from the original city area, never the selected route/point extent.
    """
    context = inputs.context
    request = route.request
    points = {p.point_id: p for p in inputs.points}
    destinations = {d.destination_id: d for d in inputs.destinations}
    origins = {p.entity_id: p for p in inputs.point_endpoints}
    endings = {d.entity_id: d for d in inputs.destination_endpoints}
    if request.point_id not in points or request.destination_id not in destinations:
        raise ValueError("OBSERVED_ROUTE_UNKNOWN_POINT_OR_DESTINATION")
    point, destination = points[request.point_id], destinations[request.destination_id]
    origin, ending = origins[point.point_id], endings[destination.destination_id]
    if (
        config.methodology_version != context.versions.methodology_version
        or rules.methodology_version != config.methodology_version
        or inputs.area.city_id != context.city_id
        or inputs.area.geometry_version != context.versions.geometry_version
        or point.territory_id != context.territory_id
        or destination.territory_id != context.territory_id
    ):
        raise ValueError("OBSERVED_ROUTE_CONTEXT_MISMATCH")
    if category not in destination.categories or category not in config.categories:
        raise ValueError("CATEGORY_DESTINATION_MISMATCH")
    if inputs.build is None or origin.correlation is None or ending.correlation is None:
        raise ValueError("OBSERVED_ROUTE_BUILD_OR_CORRELATION_UNAVAILABLE")
    if (
        inputs.build.snapshot_id != context.versions.snapshot_id
        or inputs.build.source_graph_version != inputs.source_graph_version
        or inputs.build.graph_version != context.versions.graph_version
        or origin.physical_node_id != point.origin_node_id
        or origin.access_node_id != point.access_node_id
        or ending.physical_node_id != destination.physical_node_id
        or ending.access_node_id != destination.access_node_id
        or any(
            endpoint.correlation is None
            or endpoint.correlation.source_location != endpoint.access_location
            or endpoint.correlation.access_node_id != endpoint.access_node_id
            for endpoint in (origin, ending)
        )
    ):
        raise ValueError("OBSERVED_ROUTE_ENDPOINT_SOURCE_MISMATCH")
    if request != make_route_request(
        point.point_id,
        destination.destination_id,
        inputs.build,
        context.search_parameters_id,
        origin.correlation,
        ending.correlation,
    ):
        raise ValueError("OBSERVED_ROUTE_REQUEST_SOURCE_MISMATCH")
    h = select_h_seconds(inputs.area.area_km2, bands=config.area_h_bands)
    limitations = set(inputs.limitations) | {
        "OBSERVED_ROUTE_ESTIMATE",
        "MINIMUM_NOT_PROVEN",
        "SINGLE_FOUND_OBJECT_NOT_CATEGORY_MINIMUM",
        "REPORTED_DURATION_NOT_CERTIFIED_UPPER_BOUND",
        "NOT_A_CITY_RANKING",
    }
    evidence = references(
        route.evidence_refs + point.source_refs + destination.source_refs + inputs.area.source_refs
    )

    def result(
        total: float | None,
        metric: MetricResult,
        *,
        quantized: bool = False,
    ) -> ObservedRouteAssessment:
        return ObservedRouteAssessment(
            context,
            request.request_id,
            point.point_id,
            destination.destination_id,
            category,
            total,
            h,
            metric,
            MetricResult(None, "unknown", "MINIMUM_NOT_PROVEN"),
            total is not None and not quantized,
            None if quantized or total is None else 0.0,
            evidence,
            tuple(sorted(limitations | ({metric.reason_code} if metric.reason_code else set()))),
        )

    def unknown(reason: str) -> ObservedRouteAssessment:
        return result(None, MetricResult(None, "unknown", reason))

    if destination.eligible is not True or not destination.in_service_scope:
        return unknown("DESTINATION_ELIGIBILITY_UNCONFIRMED")
    if route.state != "reachable" or route.network_seconds is None:
        return unknown(route.reason_code or "OBSERVED_ROUTE_UNAVAILABLE")
    if not route.evidence_refs or route.response_hash is None:
        return unknown("OBSERVED_ROUTE_EVIDENCE_UNAVAILABLE")
    for endpoint in (origin, ending):
        connector = endpoint.connector
        if connector is None or not connector.confirmed:
            return unknown("CONNECTOR_UNCONFIRMED")
        if (
            connector.snapshot_id != context.versions.snapshot_id
            or connector.source_graph_version != inputs.source_graph_version
            or connector.physical_node_id != endpoint.physical_node_id
            or connector.access_node_id != endpoint.access_node_id
            or connector.physical_location != endpoint.location
            or connector.access_location != endpoint.access_location
        ):
            raise ValueError("OBSERVED_CONNECTOR_SOURCE_MISMATCH")
        if endpoint.nonzero_approach_has_zero_time(
            access_distance_m=point.access_distance_m if endpoint is origin else 0.0
        ):
            return unknown("NONZERO_APPROACH_ZERO_TIME")
        evidence = references(evidence + connector.evidence_refs)
    assert origin.connector and origin.connector.seconds is not None
    assert ending.connector and ending.connector.seconds is not None
    quantized = any(
        correlation.coordinate_precision_digits is not None
        for correlation in (origin.correlation, ending.correlation)
    )
    if quantized:
        limitations.update(
            {"COORDINATE_QUANTIZATION_UNCERTAINTY", "PHYSICAL_CORRELATION_OFFSET_UNMEASURED"}
        )
    network = finite_number(route.network_seconds, "observed_network_seconds")
    if network == 0 and origin.access_location != ending.access_location:
        return unknown("NONZERO_NETWORK_ZERO_TIME")
    # Preserve all three reported operands; timestamp resolution does not replace
    # this raw duration. The existing chain validator independently checks nodes.
    total = fsum((origin.connector.seconds, network, ending.connector.seconds))
    departure = context.requested_departure_at
    legs = []
    for start, end, seconds, refs in (
        (
            origin.physical_node_id,
            origin.access_node_id,
            origin.connector.seconds,
            origin.connector.evidence_refs,
        ),
        (origin.access_node_id, ending.access_node_id, network, route.evidence_refs),
        (
            ending.access_node_id,
            ending.physical_node_id,
            ending.connector.seconds,
            ending.connector.evidence_refs,
        ),
    ):
        arrival = departure + timedelta(seconds=seconds)
        elapsed = (arrival - departure).total_seconds()
        if seconds > 0 and elapsed == 0:
            return unknown("OBSERVED_DURATION_BELOW_TIMESTAMP_RESOLUTION")
        legs.append(UrbanLeg("walk", "walk", start, end, departure, arrival, elapsed, None, refs))
        departure = arrival
    candidate = UrbanCandidate(
        request.request_id + ":observed:" + category,
        point.point_id,
        destination.destination_id,
        category,
        "walk",
        context,
        origin.physical_node_id,
        ending.physical_node_id,
        context.requested_departure_at,
        departure,
        tuple(legs),
        evidence,
    )
    _check_candidate(candidate, context, points, destinations, config, rules)
    decay = category_index(total, h.value)
    estimate = MetricResult(
        decay.value,
        decay.status,
        "MINIMUM_NOT_PROVEN" if decay.value is not None else decay.reason_code,
    )
    return result(total, estimate, quantized=quantized)
