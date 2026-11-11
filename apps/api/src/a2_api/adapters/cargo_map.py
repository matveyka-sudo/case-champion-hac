"""Accepted physical freight locations, independent of transport availability."""

from a2_contracts.v2.dataset import AcceptedData, Located
from a2_contracts.v2.map_data import AnalysisMapData, MapEntityKind, MapFeature, PointGeometry
from a2_contracts.v2.requests import UrbanCargoRequest

from a2_api.adapters.product_inputs import UrbanCargoInputs


def accepted_cargo_map(
    snapshot: AcceptedData, request: UrbanCargoRequest, inputs: UrbanCargoInputs
) -> AnalysisMapData:
    if (
        request.city_id not in snapshot.geographic_scope
        or request.snapshot_id not in (None, snapshot.snapshot_id)
        or request.area_id is not None
    ):
        raise ValueError("CARGO_MAP_SOURCE_SCOPE_MISMATCH")
    sites = set(inputs.coverage.expected_site_ids)
    selected_types = set(request.freight_node_types or (n.analysis_type for n in inputs.nodes))
    nodes = {n.node_id for n in inputs.nodes if n.analysis_type in selected_types}
    features = []
    collections: tuple[tuple[MapEntityKind, list[tuple[str, Located]]], ...] = (
        ("freight_site", [(s.site_id, s) for s in snapshot.freight_sites if s.site_id in sites]),
        (
            "freight_node",
            [(n.freight_node_id, n) for n in snapshot.freight_nodes if n.freight_node_id in nodes],
        ),
    )
    for kind, rows in collections:
        for identity, row in rows:
            if row.territory_id != request.city_id:
                raise ValueError("CARGO_MAP_ENTITY_OUTSIDE_CITY")
            features.append(
                MapFeature(
                    entity_kind=kind,
                    entity_id=identity,
                    territory_id=row.territory_id,
                    geometry=PointGeometry(
                        type="Point", coordinates=(row.location.lon, row.location.lat)
                    ),
                    geometry_kind="actual",
                    source_refs=sorted(set(row.source_refs)),
                )
            )
    if {f.entity_id for f in features} != sites | nodes:
        raise ValueError("CARGO_MAP_ENTITY_OUTSIDE_SNAPSHOT")
    if not features:
        return AnalysisMapData()
    return AnalysisMapData(
        status="available",
        completeness="partial",
        reason_code="FREIGHT_LOCATIONS_ONLY",
        features=sorted(features, key=lambda f: (f.entity_kind, f.entity_id)),
    )
