"""Pure accepted map metadata and evidence-bound network-leg shapes."""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict

from a2_contracts.common import canonical_hash
from a2_contracts.v2.dataset import AcceptedData
from a2_contracts.v2.journeys import Leg, LineGeometry
from a2_contracts.v2.map_data import AnalysisMapData, MapFeature, PointGeometry
from a2_contracts.v2.requests import UrbanPassengerRequest
from a2_contracts.v2.results import AnalysisResult
from a2_engine import WalkAnalysisResult
from a2_engine.urban.valhalla.codec import polyline6_coordinate_matches


def _features(
    snapshot: AcceptedData, request: UrbanPassengerRequest, names: Mapping[str, str]
) -> list[MapFeature]:
    city = request.city_id
    cells = {cell.cell_id: cell for cell in snapshot.population_cells if cell.territory_id == city}
    features = [
        MapFeature(
            entity_kind="cell",
            entity_id=cell.cell_id,
            cell_id=cell.cell_id,
            territory_id=city,
            geometry=None,
            geometry_kind="none",
            geometry_ref=cell.geometry.geometry_id,
            reason_code="MAP_GEOMETRY_UNAVAILABLE",
            population=cell.population,
            population_year=cell.population_year,
            population_method=cell.population_method,
            source_refs=sorted(set(cell.source_refs) | set(cell.geometry.source_refs)),
        )
        for cell in sorted(cells.values(), key=lambda row: row.cell_id)
    ]
    for point in sorted(snapshot.sample_points, key=lambda row: row.point_id):
        if point.territory_id != city:
            continue
        cell = cells[point.cell_id]
        features.append(
            MapFeature(
                entity_kind="sample_point",
                entity_id=point.point_id,
                point_id=point.point_id,
                cell_id=point.cell_id,
                territory_id=city,
                geometry=PointGeometry(
                    type="Point", coordinates=(point.location.lon, point.location.lat)
                ),
                geometry_kind="actual",
                population=cell.population,
                population_year=cell.population_year,
                population_method=cell.population_method,
                source_refs=sorted(set(point.source_refs) | set(cell.source_refs)),
            )
        )
    for destination in sorted(snapshot.destinations, key=lambda row: row.destination_id):
        categories = [
            category for category in request.categories if category in destination.categories
        ]
        if destination.territory_id != city or not categories:
            continue
        features.append(
            MapFeature(
                entity_kind="destination",
                entity_id=destination.destination_id,
                territory_id=city,
                categories=categories,
                name=names.get(destination.destination_id),
                geometry=PointGeometry(
                    type="Point", coordinates=(destination.location.lon, destination.location.lat)
                ),
                geometry_kind="actual",
                source_refs=sorted(set(destination.source_refs)),
            )
        )
    return features


def attach_walk_map(
    result: AnalysisResult,
    snapshot: AcceptedData,
    calculated: WalkAnalysisResult,
    destination_names: Mapping[str, str],
    *,
    map_features: Sequence[MapFeature] = (),
) -> AnalysisResult:
    """Preserve numerical results and attach no inferred approach or city polygons."""
    request = result.request
    inputs = calculated.plan.inputs
    context = inputs.context
    # prepare_walk_inputs uses this ASCII JSON recipe for canonical_payload_hash.
    source_hash = hashlib.sha256(
        json.dumps(
            snapshot.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            ensure_ascii=True,
        ).encode()
    ).hexdigest()
    if (
        not isinstance(request, UrbanPassengerRequest)
        or request.passenger_mode != "walk"
        or request.area_id is not None
        or result.snapshot_id != snapshot.snapshot_id
        or result.versions.graph_version != snapshot.provenance.graph_version
        or result.versions.calendar_version != snapshot.provenance.calendar_version
        or result.versions.population_version != snapshot.provenance.population_version
        or inputs.canonical_payload_hash != source_hash
        or inputs.source_graph_version != snapshot.provenance.graph_version
        or context.versions.snapshot_id != snapshot.snapshot_id
        or context.city_id != request.city_id
        or context.territory_id != request.city_id
        or context.requested_departure_at != request.departure_at
        or context.traffic_mode != request.traffic_mode
        or context.versions.config_hash != result.config_hash
        or context.versions.methodology_version != result.methodology_version
        or calculated.analysis.context != context
        or set(calculated.plan.categories) != set(request.categories)
    ):
        raise ValueError("WALK_MAP_SOURCE_SCOPE_MISMATCH")
    planned = {row.request_id: row for row in calculated.plan.requests}
    routes = {row.request.request_id: row for row in calculated.routes}
    if len(planned) != len(calculated.plan.requests) or len(routes) != len(calculated.routes):
        raise ValueError("WALK_MAP_DUPLICATE_ROUTE")
    candidates = {
        row.candidate.journey_id: row.candidate
        for row in calculated.analysis.journeys
        if row.admissible
    }
    journeys = {row.journey_id: row for row in result.journeys}
    evidence = {row.evidence_id for row in result.evidence_refs}
    points = {row.point_id: row for row in inputs.points}
    destinations = {row.destination_id: row for row in inputs.destinations}
    for route in calculated.routes:
        wire = route.request
        if (
            planned.get(wire.request_id) != wire
            or wire.path_scope != "network_only"
            or wire.build != inputs.build
        ):
            raise ValueError("WALK_MAP_ROUTE_REQUEST_MISMATCH")
        if route.network_geometry is None:
            continue
        if route.state != "reachable" or route.network_seconds is None:
            raise ValueError("WALK_MAP_UNKNOWN_ROUTE_HAS_GEOMETRY")
        if not set(route.network_geometry.evidence_refs) <= evidence:
            raise ValueError("WALK_MAP_UNRESOLVED_GEOMETRY_EVIDENCE")
        if set(route.network_geometry.evidence_refs) != set(route.evidence_refs):
            raise ValueError("WALK_MAP_GEOMETRY_EVIDENCE_MISMATCH")
        for coordinate, correlation in zip(
            (route.network_geometry.coordinates[0], route.network_geometry.coordinates[-1]),
            (wire.origin, wire.destination),
            strict=True,
        ):
            if not polyline6_coordinate_matches(
                coordinate[0], correlation.correlated_location.lon
            ) or not polyline6_coordinate_matches(
                coordinate[1], correlation.correlated_location.lat
            ):
                raise ValueError("WALK_MAP_GEOMETRY_ENDPOINT_MISMATCH")
        point, destination = points[wire.point_id], destinations[wire.destination_id]
        for category in request.categories:
            if category not in destination.categories:
                continue
            identifier = wire.request_id + ":" + category
            journey = journeys.get(identifier)
            if journey is None:
                continue
            candidate = candidates.get(identifier)
            if (
                candidate is None
                or candidate.point_id != point.point_id
                or candidate.destination_id != destination.destination_id
                or candidate.category != category
                or candidate.passenger_mode != "walk"
                or journey.point_id != candidate.point_id
                or journey.destination_object_id != candidate.destination_id
                or journey.origin_node_id != point.origin_node_id
                or journey.destination_node_id != destination.physical_node_id
                or journey.departure_at != candidate.departure_at
                or journey.arrival_at != candidate.arrival_at
                or len(journey.legs) != len(candidate.legs)
                or any(
                    leg.model_dump(exclude={"geometry"})
                    != Leg.model_validate(asdict(expected)).model_dump(exclude={"geometry"})
                    for leg, expected in zip(journey.legs, candidate.legs, strict=True)
                )
            ):
                raise ValueError("WALK_MAP_JOURNEY_IDENTITY_MISMATCH")
            for metric in result.metrics:
                if metric.journey_id == identifier and (
                    metric.subject.point_id != point.point_id
                    or metric.subject.category != category
                    or metric.subject.territory_id != request.city_id
                    or metric.subject.mode != "walk"
                ):
                    raise ValueError("WALK_MAP_CATEGORY_IDENTITY_MISMATCH")
            indices = [
                index
                for index, leg in enumerate(journey.legs)
                if leg.kind == "walk"
                and leg.mode == "walk"
                and leg.from_node_id == wire.origin.access_node_id
                and leg.to_node_id == wire.destination.access_node_id
                and set(leg.evidence_refs) == set(route.evidence_refs)
                and leg.duration_seconds == route.network_seconds
            ]
            if len(indices) != 1:
                raise ValueError("WALK_MAP_NETWORK_LEG_IDENTITY_MISMATCH")
            geometry = LineGeometry(
                type="LineString",
                coordinates=list(route.network_geometry.coordinates),
                evidence_refs=list(route.network_geometry.evidence_refs),
            )
            legs = list(journey.legs)
            index = indices[0]
            if legs[index].geometry is not None and legs[index].geometry != geometry:
                raise ValueError("WALK_MAP_EXISTING_GEOMETRY_DIFFERS")
            legs[index] = legs[index].model_copy(update={"geometry": geometry})
            journeys[identifier] = journey.model_copy(update={"legs": legs})
    features = {
        (feature.entity_kind, feature.entity_id): feature
        for feature in _features(snapshot, request, destination_names)
    }
    for source_feature in map_features:
        key = source_feature.entity_kind, source_feature.entity_id
        if (
            source_feature.entity_kind not in {"cell", "territory"}
            or source_feature.geometry_kind != "actual"
        ):
            raise ValueError("WALK_MAP_SOURCE_GEOMETRY_SCOPE_MISMATCH")
        previous = features.get(key)
        if previous is not None:
            if (
                previous.cell_id,
                previous.population,
                previous.population_year,
                previous.population_method,
            ) != (
                source_feature.cell_id,
                source_feature.population,
                source_feature.population_year,
                source_feature.population_method,
            ):
                raise ValueError("WALK_MAP_SOURCE_POPULATION_MISMATCH")
            features[key] = previous.model_copy(
                update={
                    "geometry": source_feature.geometry,
                    "geometry_kind": "actual",
                    "reason_code": source_feature.reason_code,
                    "source_refs": sorted(
                        set(previous.source_refs) | set(source_feature.source_refs)
                    ),
                }
            )
        elif source_feature.entity_kind == "territory":
            features[key] = source_feature
        else:
            raise ValueError("WALK_MAP_SOURCE_UNKNOWN_CELL")
    payload = result.model_dump(mode="json")
    payload.update(
        journeys=[journeys[row.journey_id].model_dump(mode="json") for row in result.journeys],
        map_data=AnalysisMapData(
            status="available",
            completeness="partial",
            reason_code="PARTIAL_WALK_MAP",
            features=list(features.values()),
        ).model_dump(mode="json"),
    )
    validated = AnalysisResult.model_validate(payload)
    return validated.model_copy(
        update={
            "result_hash": canonical_hash(
                validated.model_dump(mode="json", exclude={"result_hash"})
            )
        }
    )
