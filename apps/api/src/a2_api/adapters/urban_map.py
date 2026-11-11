"""Generic map metadata from accepted urban facts; absent polygons stay absent."""

from collections.abc import Mapping

from a2_contracts.v2.dataset import AcceptedData
from a2_contracts.v2.map_data import AnalysisMapData, MapFeature, PointGeometry
from a2_contracts.v2.requests import UrbanPassengerRequest


def urban_map_features(
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


def accepted_urban_map(snapshot: AcceptedData, request: UrbanPassengerRequest) -> AnalysisMapData:
    if (
        request.city_id not in snapshot.geographic_scope
        or request.snapshot_id not in (None, snapshot.snapshot_id)
        or request.area_id is not None
    ):
        raise ValueError("URBAN_MAP_SOURCE_SCOPE_MISMATCH")
    return AnalysisMapData(
        status="available",
        completeness="partial",
        reason_code="PARTIAL_URBAN_MAP",
        features=urban_map_features(snapshot, request, {}),
    )
