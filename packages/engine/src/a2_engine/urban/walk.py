"""AcceptedData v2 mapping and walk/static integration, with no foreign imports or IO."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from math import fsum
from typing import Any
from zoneinfo import ZoneInfo

from a2_engine.shared.config import UrbanConfig, UrbanJourneyRules
from a2_engine.shared.results import finite_number
from a2_engine.urban.analysis import UrbanAnalysis, analyze_urban_candidates
from a2_engine.urban.models import (
    UrbanArea,
    UrbanCandidate,
    UrbanContext,
    UrbanCoverage,
    UrbanDestination,
    UrbanLeg,
    UrbanPoint,
    UrbanPopulationCoverage,
    UrbanSearchScope,
    UrbanThreshold,
    UrbanVersions,
    references,
    utc_timestamp,
)
from a2_engine.urban.valhalla import (
    ValhallaBuild,
    ValhallaCorrelation,
    WalkConnector,
    WalkCoordinate,
    WalkResponseEnvelope,
    WalkRouteRequest,
    WalkRouteResult,
    digest,
    make_route_request,
    normalize_walk_response,
)


def _coordinate(row: Mapping[str, Any]) -> WalkCoordinate:
    return WalkCoordinate(row["lon"], row["lat"])


def _table(rows: Sequence[Mapping[str, Any]], key: str) -> dict[str, Mapping[str, Any]]:
    table = {row[key]: row for row in rows}
    if len(table) != len(rows):
        raise ValueError("DUPLICATE_CANONICAL_ID:" + key)
    return table


def _active(period: Mapping[str, Any], on: date) -> bool:
    return (period["start"] is None or date.fromisoformat(period["start"]) <= on) and (
        period["end"] is None or on <= date.fromisoformat(period["end"])
    )


@dataclass(frozen=True)
class WalkEndpoint:
    entity_id: str
    physical_node_id: str
    access_node_id: str
    location: WalkCoordinate
    access_location: WalkCoordinate
    connector: WalkConnector | None
    correlation: ValhallaCorrelation | None
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class WalkCatalog:
    category: str
    complete: bool
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class WalkInputs:
    canonical_payload_hash: str
    source_graph_version: str
    context: UrbanContext
    build: ValhallaBuild | None
    points: tuple[UrbanPoint, ...]
    destinations: tuple[UrbanDestination, ...]
    point_endpoints: tuple[WalkEndpoint, ...]
    destination_endpoints: tuple[WalkEndpoint, ...]
    catalogs: tuple[WalkCatalog, ...]
    area: UrbanArea
    population_coverage: UrbanPopulationCoverage
    limitations: tuple[str, ...]


def prepare_walk_inputs(
    accepted: Mapping[str, Any],
    *,
    city_id: str,
    departure_at: datetime,
    methodology_version: str,
    config_hash: str,
    search_parameters_id: str,
    build: ValhallaBuild | None = None,
    connectors: Sequence[WalkConnector] = (),
    correlations: Sequence[ValhallaCorrelation] = (),
) -> WalkInputs:
    """Consume model_dump(mode='json') of BACKEND-validated AcceptedData v2.

    ML2 owns bundle conversion. This projection checks engine invariants and never
    re-implements the canonical wire validator or infers connector passability.
    """
    if accepted["schema_version"] != "2" or accepted["qa"]["acceptance_state"] != "accepted":
        raise ValueError("ACCEPTED_DATA_V2_REQUIRED")
    territories = _table(accepted["territories"], "territory_id")
    city = territories[city_id]
    departure = utc_timestamp(departure_at)
    local_day = departure.astimezone(ZoneInfo(city["timezone"])).date()
    if not _active(accepted["valid_period"], local_day):
        raise ValueError("SNAPSHOT_OUTSIDE_VALID_PERIOD")
    provenance = accepted["provenance"]
    snapshot = accepted["snapshot_id"]
    source_graph = provenance["graph_version"]
    if build is not None and (
        build.snapshot_id != snapshot or build.source_graph_version != source_graph
    ):
        raise ValueError("BUILD_SOURCE_MISMATCH")
    versions = UrbanVersions(
        snapshot,
        build.graph_version if build else source_graph,
        provenance["calendar_version"],
        provenance["population_version"],
        provenance["geometry_version"],
        methodology_version,
        config_hash,
        "static:" + (build.build_id if build else "unbound"),
    )
    context = UrbanContext(city_id, city_id, departure, "static", versions, search_parameters_id)
    cells = _table(accepted["population_cells"], "cell_id")
    nodes = _table(accepted["access_nodes"], "node_id")
    _table(accepted["sample_points"], "point_id")
    _table(accepted["destinations"], "destination_id")
    connector_map = {c.physical_node_id: c for c in connectors}
    correlation_map = {c.access_node_id: c for c in correlations}
    if len(connector_map) != len(connectors) or len(correlation_map) != len(correlations):
        raise ValueError("DUPLICATE_CONNECTOR_OR_CORRELATION")
    used_connectors: set[str] = set()
    used_correlations: set[str] = set()

    def endpoint(row: Mapping[str, Any], entity_id: str, kind: str) -> WalkEndpoint:
        physical = f"physical:{kind}:{entity_id}"
        access_id = row["access_node_id"]
        node = nodes[access_id]
        if node["territory_id"] != city_id:
            raise ValueError("ACCESS_TERRITORY_MISMATCH")
        location, access = _coordinate(row["location"]), _coordinate(node["location"])
        connector = connector_map.get(physical)
        if connector is not None:
            used_connectors.add(physical)
            if (
                connector.snapshot_id != snapshot
                or connector.source_graph_version != source_graph
                or connector.access_node_id != access_id
                or connector.physical_location != location
                or connector.access_location != access
            ):
                raise ValueError("CONNECTOR_SOURCE_MISMATCH")
        correlation = correlation_map.get(access_id)
        if correlation is not None:
            used_correlations.add(access_id)
            if build is None or correlation.build != build or correlation.source_location != access:
                raise ValueError("CORRELATION_SOURCE_MISMATCH")
        return WalkEndpoint(
            entity_id,
            physical,
            access_id,
            location,
            access,
            connector,
            correlation,
            references(tuple(row["source_refs"]) + tuple(node["source_refs"])),
        )

    points, point_endpoints = [], []
    used_cells: set[str] = set()
    for row in sorted(accepted["sample_points"], key=lambda row: row["point_id"]):
        if row["territory_id"] != city_id:
            continue
        cell = cells[row["cell_id"]]
        if (
            cell["cell_id"] in used_cells
            or cell["territory_id"] != city_id
            or cell["sample_status"] != "included"
        ):
            raise ValueError("INVALID_POINT_POPULATION_BASIS")
        used_cells.add(cell["cell_id"])
        ep = endpoint(row, row["point_id"], "point")
        point_endpoints.append(ep)
        points.append(
            UrbanPoint(
                row["point_id"],
                cell["cell_id"],
                ep.physical_node_id,
                ep.access_node_id,
                row["access_distance_m"],
                city_id,
                cell["population"],
                cell["population_year"],
                cell["population_method"],
                references(tuple(row["source_refs"]) + tuple(cell["source_refs"])),
            )
        )
    destinations, destination_endpoints = [], []
    for row in sorted(accepted["destinations"], key=lambda row: row["destination_id"]):
        if row["territory_id"] != city_id:
            continue
        ep = endpoint(row, row["destination_id"], "destination")
        destination_endpoints.append(ep)
        # Public institutional ownership is validated by AcceptedData, not guessed here.
        destinations.append(
            UrbanDestination(
                row["destination_id"],
                row["object_identity"],
                frozenset(row["categories"]),
                ep.access_node_id,
                city_id,
                True,
                True,
                tuple(row["source_refs"]),
                physical_node_id=ep.physical_node_id,
            )
        )
    if set(connector_map) != used_connectors or set(correlation_map) != used_correlations:
        raise ValueError("UNKNOWN_SUPPLEMENT_ENDPOINT")
    relevant_cells = [cell for cell in cells.values() if cell["territory_id"] == city_id]
    missing = [cell for cell in relevant_cells if cell["cell_id"] not in used_cells]
    excluded = (
        None
        if any(cell["population"] is None for cell in missing)
        else fsum(finite_number(cell["population"], "excluded_population") for cell in missing)
    )
    evidence = references(tuple(ref for cell in relevant_cells for ref in cell["source_refs"]))
    population = UrbanPopulationCoverage(
        context,
        all(cell["population"] is not None for cell in relevant_cells)
        and used_cells
        == {cell["cell_id"] for cell in relevant_cells if cell["sample_status"] == "included"},
        excluded,
        evidence,
        frozenset(p.point_id for p in points),
    )
    catalogs = []
    for category in sorted({c for d in destinations for c in d.categories}):
        scopes = [
            s
            for s in accepted["coverage_by_mode_date_category"]
            if city_id in s["territory_ids"]
            and "walk" in s["modes"]
            and category in s["categories"]
            and _active(s["valid_period"], local_day)
        ]
        catalogs.append(
            WalkCatalog(
                category,
                any(s["completeness"] == "full" for s in scopes),
                references(tuple(ref for s in scopes for ref in s["source_refs"])),
            )
        )
    limitations = set()
    if build is None:
        limitations.add("VALHALLA_BUILD_UNAVAILABLE")
    for ep in (*point_endpoints, *destination_endpoints):
        if ep.connector is None or not ep.connector.confirmed:
            limitations.add("CONNECTOR_UNCONFIRMED")
        if ep.correlation is None:
            limitations.add("VALHALLA_CORRELATION_UNAVAILABLE")
    if excluded != 0:
        limitations.add("INCOMPLETE_POPULATION_COVERAGE")
    return WalkInputs(
        digest(accepted),
        source_graph,
        context,
        build,
        tuple(points),
        tuple(destinations),
        tuple(point_endpoints),
        tuple(destination_endpoints),
        tuple(catalogs),
        UrbanArea(city_id, city["area_km2"], versions.geometry_version, tuple(city["source_refs"])),
        population,
        tuple(sorted(limitations)),
    )


@dataclass(frozen=True)
class WalkPlanIssue:
    point_id: str
    destination_id: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class WalkRoutePlan:
    inputs: WalkInputs
    point_ids: tuple[str, ...]
    categories: tuple[str, ...]
    requests: tuple[WalkRouteRequest, ...]
    issues: tuple[WalkPlanIssue, ...]


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


@dataclass(frozen=True)
class WalkAnalysisResult:
    plan: WalkRoutePlan
    routes: tuple[WalkRouteResult, ...]
    analysis: UrbanAnalysis


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
    inputs = plan.inputs
    requests = {request.request_id: request for request in plan.requests}
    envelopes = {response.request_id: response for response in responses}
    if len(envelopes) != len(responses) or envelopes.keys() - requests.keys():
        raise ValueError("DUPLICATE_OR_UNKNOWN_RESPONSE")
    routes = tuple(
        normalize_walk_response(request, envelopes[request.request_id])
        for request in plan.requests
        if request.request_id in envelopes
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
            legs.append(
                UrbanLeg(
                    "walk",
                    "walk",
                    start_node,
                    end_node,
                    departure,
                    arrival,
                    seconds,
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
