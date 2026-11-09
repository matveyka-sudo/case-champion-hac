"""Synthetic-only snapshot boundaries; no real geography or service assertions."""

import importlib
import importlib.util
from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError


def dto() -> Any:
    assert importlib.util.find_spec("a2_contracts.v2.dataset"), "v2 accepted-data contracts missing"
    return importlib.import_module("a2_contracts.v2.dataset")


def snapshot() -> dict[str, Any]:
    refs = {"source_refs": ["e-1"]}
    period = {"start": "2026-11-01", "end": "2026-11-30"}
    location = {"lon": 30.5, "lat": 60.5}
    geometry = {
        "geometry_id": "geom-1",
        "version": "geometry-v1",
        "source_refs": ["e-1"],
        "bbox": {"west": 30.0, "south": 60.0, "east": 31.0, "north": 61.0},
    }
    return {
        "schema_version": "2",
        "snapshot_id": "synthetic-snapshot",
        "source_versions": {"source-1": "v1"},
        "geographic_scope": ["city-1"],
        "valid_period": period,
        "timezone": "Europe/Moscow",
        "provenance": {
            "graph_version": "graph-v1",
            "geometry_version": "geometry-v1",
            "calendar_version": "calendar-v1",
            "population_version": "population-v1",
        },
        "qa": {
            "acceptance_state": "accepted",
            "accepted_by": "reviewer",
            "accepted_at": "2026-11-08T10:00:00Z",
            "checks": ["synthetic_test_only"],
        },
        "evidence": [
            {
                "evidence_id": "e-1",
                "source_id": "source-1",
                "source_version": "v1",
                "url": "https://example.org/synthetic",
                "retrieved_at": "2026-11-08T10:00:00Z",
                "valid_period": period,
                "license": "synthetic_only",
                "content_hash": "synthetic-hash",
                "verified": True,
            }
        ],
        "registry_evidence": [
            {
                "registry_evidence_id": "registry-e-1",
                "registry_id": "synthetic-registry",
                "destination_id": "destination-1",
                "category": "adult_polyclinic",
                "ownership": "municipal",
                "verified": True,
                **refs,
            }
        ],
        "territories": [
            {
                "territory_id": "city-1",
                "level": "city",
                "name": "Synthetic city",
                "timezone": "Europe/Moscow",
                "geometry": geometry,
                "area_km2": 80,
                **refs,
            }
        ],
        "population_cells": [
            {
                "cell_id": "cell-1",
                "territory_id": "city-1",
                "grid_version": "grid-v1",
                "grid_origin_m": [0, 0],
                "projection": "local-metric-source-crs",
                "geometry": geometry,
                "population": 0,
                "population_year": 2026,
                "population_method": "synthetic",
                "unit": "persons",
                "sample_status": "included",
                "exclusion_reason": None,
                **refs,
            }
        ],
        "sample_points": [
            {
                "point_id": "point-1",
                "cell_id": "cell-1",
                "building_id": "building-1",
                "territory_id": "city-1",
                "location": location,
                "initial_center": location,
                "access_node_id": "node-1",
                "access_distance_m": 30,
                **refs,
            }
        ],
        "destinations": [
            {
                "destination_id": "destination-1",
                "territory_id": "city-1",
                "location": location,
                "categories": ["adult_polyclinic"],
                "access_node_id": "node-2",
                "object_identity": "facility-1",
                **refs,
            }
        ],
        "access_nodes": [
            {
                "node_id": "node-1",
                "territory_id": "city-1",
                "location": location,
                "kind": "stop",
                **refs,
            },
            {
                "node_id": "node-2",
                "territory_id": "city-1",
                "location": location,
                "kind": "stop",
                **refs,
            },
        ],
        "scheduled_services": [
            {
                "service_id": "service-1",
                "trip_id": "feed:trip-1",
                "mode": "public_transport",
                "timezone": "Europe/Moscow",
                "service_dates": ["2026-11-09"],
                "trip_identity_confirmed": True,
                "stop_times": [
                    {
                        "node_id": "node-1",
                        "sequence": 0,
                        "arrival_seconds": 90600,
                        "departure_seconds": 90700,
                        "pickup": True,
                        "dropoff": True,
                    },
                    {
                        "node_id": "node-2",
                        "sequence": 1,
                        "arrival_seconds": 91200,
                        "departure_seconds": 91300,
                        "pickup": True,
                        "dropoff": True,
                    },
                ],
                **refs,
            }
        ],
        "transfers": [
            {
                "transfer_id": "transfer-1",
                "from_node_id": "node-1",
                "to_node_id": "node-2",
                "seconds": 300,
                "verification_state": "verified",
                **refs,
            }
        ],
        "freight_sites": [
            {
                "site_id": "site-1",
                "territory_id": "city-1",
                "location": location,
                "access_node_id": "node-1",
                **refs,
            }
        ],
        "freight_nodes": [
            {
                "freight_node_id": "freight-1",
                "territory_id": "city-1",
                "location": location,
                "access_node_id": "node-1",
                "kind": "pipeline_terminal",
                **refs,
            },
            {
                "freight_node_id": "freight-2",
                "territory_id": "city-1",
                "location": location,
                "access_node_id": "node-2",
                "kind": "pipeline_terminal",
                **refs,
            },
        ],
        "freight_compatibility": [
            {
                "compatibility_id": "compat-1",
                "site_id": "site-1",
                "freight_node_id": "freight-1",
                "direction": "outbound",
                "food_classes": ["nonfood"],
                "temperature_requirements": [],
                "handling_requirements": [],
                "vehicle_constraints": [],
                "compatible": None,
                **refs,
            }
        ],
        "pipeline_connections": [
            {
                "connection_id": "pipe-1",
                "product": "oil",
                "from_node_id": "freight-1",
                "to_node_id": "freight-2",
                "connection_confirmed": True,
                "direction_confirmed": True,
                "volume": {"value": 120, "unit": "tonnes", "period": period, "product": "oil"},
                "capacity": {"value": 100, "unit": "tonnes", "period": period, "product": "oil"},
                "quantity_status": "available",
                **refs,
            }
        ],
        "reference_edges": [
            {
                "edge_id": "edge-1",
                "from_node_id": "node-1",
                "to_node_id": "node-2",
                "mode": "road",
                "length_m": 1000,
                "running_time_seconds": 120,
                "calibration_method": "synthetic-static",
                "geometry": geometry,
                **refs,
            }
        ],
        "minimum_transfers": [
            {
                "minimum_id": "minimum-1",
                "from_node_id": "node-1",
                "to_node_id": "node-2",
                "seconds": 60,
                "applicability": [],
                **refs,
            }
        ],
        "traffic_profiles": [],
        "category_ownership": [
            {
                "destination_id": "destination-1",
                "category": "adult_polyclinic",
                "ownership": "municipal",
                "registry_evidence_ids": ["registry-e-1"],
                **refs,
            }
        ],
        "coverage_by_mode_date_category": [
            {
                "territory_ids": ["city-1"],
                "modes": ["walk"],
                "categories": ["adult_polyclinic"],
                "valid_period": period,
                "completeness": "partial",
                **refs,
            }
        ],
        "confirmed_absence_scope": [],
    }


def traffic() -> dict[str, Any]:
    dates = [f"2026-11-{day:02d}" for day in (2, 3, 4, 5, 6)]
    return {
        "profile_id": "traffic-1",
        "directed_edge_id": "edge-1",
        "graph_version": "graph-v1",
        "traffic_mode": "typical",
        "status": "available",
        "speed_kph": 30,
        "is_closed": False,
        "weekday_type": "workday",
        "local_15min_bin": 32,
        "season": "autumn",
        "observed_period": {"start": "2026-11-01", "end": "2026-11-30"},
        "sample_count": 5,
        "observations": [
            {
                "date": day,
                "weekday_type": "workday",
                "season": "autumn",
                "graph_version": "graph-v1",
            }
            for day in dates
        ],
        "observed_at": None,
        "valid_until": None,
        "method": "median",
        "confidence": None,
        "source_refs": ["e-1"],
    }


def test_accepted_snapshot_preserves_zero_population_and_service_day_seconds() -> None:
    result = dto().AcceptedData.model_validate(snapshot())
    assert result.population_cells[0].population == 0
    assert result.scheduled_services[0].stop_times[0].arrival_seconds == 90600
    assert result.pipeline_connections[0].volume.value == 120
    assert result.pipeline_connections[0].capacity.value == 100


def test_unknown_population_is_not_replaced_with_zero() -> None:
    raw = snapshot()
    raw["population_cells"][0].update(population=None, population_year=None, population_method=None)
    assert dto().AcceptedData.model_validate(raw).population_cells[0].population is None


@pytest.mark.parametrize(
    "table,key",
    [
        ("territories", "territory_id"),
        ("population_cells", "cell_id"),
        ("sample_points", "point_id"),
        ("destinations", "destination_id"),
        ("access_nodes", "node_id"),
        ("scheduled_services", "service_id"),
        ("freight_sites", "site_id"),
        ("freight_nodes", "freight_node_id"),
        ("reference_edges", "edge_id"),
        ("evidence", "evidence_id"),
    ],
)
def test_snapshot_rejects_duplicate_primary_keys(table: str, key: str) -> None:
    raw = snapshot()
    raw[table].append(deepcopy(raw[table][0]))
    with pytest.raises(ValidationError):
        dto().AcceptedData.model_validate(raw)


@pytest.mark.parametrize(
    "table,field,value",
    [
        ("sample_points", "cell_id", "missing"),
        ("sample_points", "access_node_id", "missing"),
        ("destinations", "source_refs", ["missing"]),
        ("transfers", "to_node_id", "missing"),
        ("freight_compatibility", "site_id", "missing"),
        ("pipeline_connections", "from_node_id", "missing"),
        ("reference_edges", "to_node_id", "missing"),
    ],
)
def test_snapshot_rejects_dangling_references(table: str, field: str, value: Any) -> None:
    raw = snapshot()
    raw[table][0][field] = value
    with pytest.raises(ValidationError):
        dto().AcceptedData.model_validate(raw)


@pytest.mark.parametrize(
    "change",
    [
        {"acceptance_state": "validated"},
        {"accepted_by": ""},
        {"accepted_at": "2026-11-08T10:00:00"},
    ],
)
def test_qa_acceptance_is_explicit_and_aware(change: dict[str, Any]) -> None:
    raw = snapshot()
    raw["qa"].update(change)
    with pytest.raises(ValidationError):
        dto().AcceptedData.model_validate(raw)


def test_medical_object_requires_matching_verified_public_registry_record() -> None:
    raw = snapshot()
    raw["registry_evidence"][0]["verified"] = False
    with pytest.raises(ValidationError):
        dto().AcceptedData.model_validate(raw)


def test_private_polyclinic_is_not_a_public_category() -> None:
    raw = snapshot()
    raw["category_ownership"][0]["ownership"] = "private"
    with pytest.raises(ValidationError):
        dto().AcceptedData.model_validate(raw)


def test_sample_cannot_move_into_neighbor_cell() -> None:
    raw = snapshot()
    raw["sample_points"][0]["location"]["lon"] = 31.1
    with pytest.raises(ValidationError):
        dto().AcceptedData.model_validate(raw)


def test_one_sample_per_cell_and_exclusion_reason_are_preserved() -> None:
    raw = snapshot()
    raw["sample_points"].append(raw["sample_points"][0] | {"point_id": "point-2"})
    with pytest.raises(ValidationError):
        dto().AcceptedData.model_validate(raw)


@pytest.mark.parametrize(
    "change",
    [
        {"trip_identity_confirmed": False},
        {"trip_identity_confirmed": "true"},
        {"service_dates": []},
    ],
)
def test_schedule_cannot_assert_a_trip_from_unconfirmed_arrays(change: dict[str, Any]) -> None:
    raw = snapshot()
    raw["scheduled_services"][0].update(change)
    with pytest.raises(ValidationError):
        dto().AcceptedData.model_validate(raw)


def test_service_stop_chain_cannot_reverse_time() -> None:
    raw = snapshot()
    raw["scheduled_services"][0]["stop_times"][1]["arrival_seconds"] = 90601
    with pytest.raises(ValidationError):
        dto().AcceptedData.model_validate(raw)


def test_typical_speed_accepts_five_distinct_comparable_days() -> None:
    raw = snapshot()
    raw["traffic_profiles"] = [traffic()]
    assert dto().AcceptedData.model_validate(raw).traffic_profiles[0].speed_kph == 30


@pytest.mark.parametrize(
    "field,value",
    [
        ("weekday_type", "holiday"),
        ("season", "winter"),
        ("graph_version", "other-graph"),
        ("date", "2025-11-01"),
    ],
)
def test_typical_speed_rejects_incomparable_observations(field: str, value: str) -> None:
    raw = traffic()
    raw["observations"][0][field] = value
    with pytest.raises(ValidationError):
        dto().TrafficProfile.model_validate(raw)


def test_typical_speed_needs_five_distinct_days_not_five_rows() -> None:
    raw = traffic()
    raw["observations"] = [raw["observations"][0]] * 5
    with pytest.raises(ValidationError):
        dto().TrafficProfile.model_validate(raw)


def test_unknown_traffic_stays_unknown_and_closure_is_not_zero_speed() -> None:
    raw = traffic() | {"status": "unknown", "speed_kph": None, "observations": []}
    assert dto().TrafficProfile.model_validate(raw).speed_kph is None
    closed = traffic() | {"speed_kph": None, "is_closed": True}
    assert dto().TrafficProfile.model_validate(closed).is_closed is True
    with pytest.raises(ValidationError):
        dto().TrafficProfile.model_validate(traffic() | {"speed_kph": 0})


@pytest.mark.parametrize(
    "change",
    [
        {"unit": "barrels"},
        {"period": {"start": "2026-10-01", "end": "2026-10-31"}},
        {"product": "gas"},
        {"value": 0},
    ],
)
def test_pipeline_capacity_mismatch_cannot_be_available(change: dict[str, Any]) -> None:
    raw = snapshot()["pipeline_connections"][0]
    raw["capacity"].update(change)
    with pytest.raises(ValidationError):
        dto().PipelineConnection.model_validate(raw)
    raw["quantity_status"] = "unknown"
    assert dto().PipelineConnection.model_validate(raw).quantity_status == "unknown"


def test_pipeline_transit_without_confirmed_connection_and_direction_is_rejected() -> None:
    raw = snapshot()["pipeline_connections"][0] | {"direction_confirmed": False}
    with pytest.raises(ValidationError):
        dto().PipelineConnection.model_validate(raw)


def test_traffic_retains_sample_count_without_counting_duplicate_dates_as_days() -> None:
    raw = traffic() | {"sample_count": 100}
    assert dto().TrafficProfile.model_validate(raw).sample_count == 100
    with pytest.raises(ValidationError):
        dto().TrafficProfile.model_validate(raw | {"sample_count": 4})


def test_included_cell_cannot_lose_its_sample_silently() -> None:
    raw = snapshot()
    raw["sample_points"] = []
    with pytest.raises(ValidationError):
        dto().AcceptedData.model_validate(raw)


def test_excluded_cell_preserves_population_and_reason_without_creating_a_sample() -> None:
    raw = snapshot()
    raw["sample_points"] = []
    raw["population_cells"][0].update(
        sample_status="excluded", exclusion_reason="no_building", population=200
    )
    result = dto().AcceptedData.model_validate(raw)
    assert result.population_cells[0].population == 200
    assert result.population_cells[0].exclusion_reason == "no_building"


def test_same_object_with_two_entrances_is_not_counted_twice_in_a_category() -> None:
    raw = snapshot()
    raw["destinations"][0]["categories"] = ["shopping_center"]
    raw["registry_evidence"] = []
    raw["category_ownership"] = []
    raw["destinations"].append(raw["destinations"][0] | {"destination_id": "another-entrance"})
    with pytest.raises(ValidationError):
        dto().AcceptedData.model_validate(raw)


@pytest.mark.parametrize("change", [{"completeness": "partial"}, {}])
def test_confirmed_absence_requires_full_scope_and_verified_evidence(
    change: dict[str, Any],
) -> None:
    raw = snapshot()
    raw["confirmed_absence_scope"] = [
        raw["coverage_by_mode_date_category"][0] | {"completeness": "full"} | change
    ]
    if not change:
        raw["evidence"][0]["verified"] = False
        raw["destinations"][0]["categories"] = ["shopping_center"]
        raw["registry_evidence"] = []
        raw["category_ownership"] = []
    with pytest.raises(ValidationError):
        dto().AcceptedData.model_validate(raw)
