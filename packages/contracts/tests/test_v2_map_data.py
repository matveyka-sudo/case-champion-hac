"""Synthetic map payloads prove wire integrity, not geographic data readiness."""

import importlib
from copy import deepcopy

import pytest
from a2_contracts.v2.results import AnalysisResult
from pydantic import ValidationError
from test_v2_results import analysis


def map_types():
    return importlib.import_module("a2_contracts.v2.map_data")


def feature(kind="cell", identifier="cell", **changes):
    data = dict(
        entity_kind=kind,
        entity_id=identifier,
        territory_id="demo-city",
        point_id=None,
        cell_id=None,
        categories=[],
        name=None,
        geometry={
            "type": "Polygon",
            "coordinates": [[[37.0, 55.0], [37.01, 55.0], [37.01, 55.01], [37.0, 55.0]]],
        },
        geometry_kind="actual",
        geometry_ref=None,
        reason_code=None,
        population=None,
        population_year=None,
        population_method=None,
        source_refs=["demo-evidence"],
    )
    data.update(changes)
    return data


def mapped_analysis():
    data = analysis()
    data["map_data"] = dict(
        status="available",
        completeness="partial",
        reason_code=None,
        features=[
            feature(point_id="demo-point"),
            feature(
                "sample_point",
                "demo-point",
                cell_id="cell",
                geometry={"type": "Point", "coordinates": [37.005, 55.001]},
            ),
            feature(
                "destination",
                "school",
                categories=["school"],
                geometry={"type": "Point", "coordinates": [37.006, 55.002]},
            ),
        ],
    )
    # Explicit synthetic route provenance; the map contract never synthesizes a line.
    data["journeys"] = [
        dict(
            journey_id="synthetic-walk",
            scope="urban",
            origin_node_id="demo-point",
            destination_node_id="school-node",
            requested_departure_at="2026-11-09T08:15:00+03:00",
            departure_at="2026-11-09T08:15:00+03:00",
            arrival_at="2026-11-09T08:45:00+03:00",
            total_seconds=1800,
            initial_wait_seconds=0,
            path_seconds=None,
            total_wait_seconds=0,
            legs=[
                dict(
                    mode="walk",
                    kind="walk",
                    from_node_id="demo-point",
                    to_node_id="school-node",
                    start_at="2026-11-09T08:15:00+03:00",
                    end_at="2026-11-09T08:45:00+03:00",
                    duration_seconds=1800,
                    service_id=None,
                    evidence_refs=["demo-evidence"],
                )
            ],
            geometry={
                "type": "LineString",
                "coordinates": [[37.005, 55.001], [37.006, 55.002]],
                "evidence_refs": ["demo-evidence"],
            },
            geometry_kind="actual",
            status="available",
            reason_code=None,
            snapshot_id="demo-snapshot",
            completeness="full",
            traffic_mode="static",
            time_kind="complete_chain",
            reference_id=None,
            destination_object_id="school",
            point_id="demo-point",
            evidence_refs=["demo-evidence"],
        )
    ]
    data["metrics"][0]["journey_id"] = "synthetic-walk"
    return data


def test_absent_map_is_explicitly_unknown_and_schema_stays_v2():
    saved = AnalysisResult.model_validate(analysis())
    assert saved.schema_version == "2"
    assert saved.map_data.status == "unknown"
    assert saved.map_data.reason_code == "MAP_DATA_UNAVAILABLE"
    assert saved.map_data.features == []


def test_synthetic_features_join_saved_point_and_preserve_unknown_population():
    saved = AnalysisResult.model_validate(mapped_analysis())
    cell, point, destination = saved.map_data.features
    assert cell.population is None
    assert cell.point_id == point.entity_id == saved.metrics[0].subject.point_id
    assert point.cell_id == cell.entity_id
    assert destination.categories == ["school"]
    assert cell.geometry.type == "Polygon"
    assert saved.journeys[0].destination_object_id == destination.entity_id
    assert saved.journeys[0].point_id == point.entity_id
    assert saved.journeys[0].geometry_kind == "actual"
    assert saved.journeys[0].geometry.coordinates[0] == point.geometry.coordinates
    assert AnalysisResult.model_validate_json(saved.model_dump_json()) == saved


@pytest.mark.parametrize(
    "changes",
    [
        {"point_id": "missing"},
        {"cell_id": "missing"},
        {"source_refs": ["missing"]},
    ],
)
def test_features_reject_dangling_optional_links_and_evidence(changes):
    data = mapped_analysis()
    data["map_data"]["features"][0].update(changes)
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(data)


def test_duplicate_feature_identity_is_rejected():
    data = mapped_analysis()
    data["map_data"]["features"].append(deepcopy(data["map_data"]["features"][0]))
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(data)


@pytest.mark.parametrize(
    "geometry",
    [
        {"type": "Point", "coordinates": [181, 55]},
        {"type": "Point", "coordinates": [37, -91]},
        {"type": "Point", "coordinates": [37, 55, 1]},
        {"type": "Point", "coordinates": [True, 55]},
        {"type": "Polygon", "coordinates": [[[37, 55], [38, 55], [38, 56], [37, 56]]]},
        {"type": "MultiPolygon", "coordinates": [[[[37, 55], [38, 55], [38, 56], [37, 56]]]]},
        {"type": "Polygon", "coordinates": []},
        {"type": "Circle", "coordinates": [37, 55]},
    ],
)
def test_geojson_is_typed_bounded_and_rings_closed(geometry):
    with pytest.raises(ValidationError):
        map_types().MapFeature.model_validate(feature(geometry=geometry))


def test_missing_geometry_remains_explicit_and_does_not_invent_a_polygon():
    raw = feature(geometry=None, geometry_kind="none", reason_code="CELL_GEOMETRY_UNAVAILABLE")
    parsed = map_types().MapFeature.model_validate(raw)
    assert parsed.geometry is None and parsed.reason_code == "CELL_GEOMETRY_UNAVAILABLE"
    with pytest.raises(ValidationError):
        map_types().MapFeature.model_validate(raw | {"reason_code": None})
    with pytest.raises(ValidationError):
        map_types().MapFeature.model_validate(raw | {"geometry_kind": "actual"})
    data = mapped_analysis()
    data["map_data"]["features"][0] = raw
    AnalysisResult.model_validate(data)
    data["map_data"]["completeness"] = "full"
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(data)


def test_known_population_has_year_method_and_zero_is_not_unknown():
    raw = feature(population=0, population_year=2026, population_method="synthetic_only")
    assert map_types().MapFeature.model_validate(raw).population == 0
    with pytest.raises(ValidationError):
        map_types().MapFeature.model_validate(raw | {"population_year": None})
    with pytest.raises(ValidationError):
        map_types().MapFeature.model_validate(raw | {"population_method": None})


def test_multipolygon_is_preserved_and_unknown_map_has_no_features():
    polygon = feature()["geometry"]["coordinates"]
    parsed = map_types().MapFeature.model_validate(
        feature(geometry={"type": "MultiPolygon", "coordinates": [polygon]})
    )
    assert parsed.geometry.type == "MultiPolygon"
    with pytest.raises(ValidationError):
        map_types().AnalysisMapData(
            status="unknown", completeness="partial", reason_code="MISSING", features=[parsed]
        )


def test_partial_map_does_not_require_every_analysis_entity_or_route_geometry():
    data = mapped_analysis()
    data["map_data"]["features"] = [feature()]
    data["journeys"][0].update(geometry=None, geometry_kind="none")
    saved = AnalysisResult.model_validate(data)
    assert saved.journeys[0].geometry is None
    assert saved.map_data.completeness == "partial"


def test_absent_optional_map_preserves_historical_saved_wire_hash():
    import json
    from pathlib import Path

    from a2_contracts.common import canonical_hash
    from a2_contracts.v2.results import AnalysisResult

    fixture = Path(__file__).resolve().parents[1] / "fixtures/v2/urban_passenger-result.json"
    normalized = AnalysisResult.model_validate(
        json.loads(fixture.read_text())["payload"]
    ).model_dump(mode="json")
    normalized.pop("map_data", None)
    normalized["result_hash"] = canonical_hash(
        {k: v for k, v in normalized.items() if k != "result_hash"}
    )
    restored = AnalysisResult.model_validate(normalized)
    assert restored.model_dump(mode="json") == normalized
    assert restored.map_data.status == "unknown"
