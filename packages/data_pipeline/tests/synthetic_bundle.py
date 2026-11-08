"""Synthetic bundle parts (synthetic=True). Geometry and IDs assert nothing real."""

from datetime import UTC, date, datetime
from typing import Any

from a2_data_pipeline.bundle import BundleManifest
from a2_data_pipeline.manifest import Checksum, License

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
SQUARE = {
    "type": "Polygon",
    "coordinates": [[[20, 54], [20.01, 54], [20.01, 54.01], [20, 54.01], [20, 54]]],
}
SQUARE_BBOX = (20.0, 54.0, 20.01, 54.01)
GRAPH = "synthetic-walk-v1"
ROAD_GRAPH = "synthetic-road-v1"
EVIDENCE = "e-syn"


def manifest(
    bundle_id: str = "synthetic-test-v1",
    state: str = "accepted",
    coverage: list[Any] | None = None,
    road_graph: str | None = None,
) -> BundleManifest:
    return BundleManifest.model_validate(
        {
            "format": "a2-ml2-bundle",
            "schema_version": 2,
            "bundle_id": bundle_id,
            "created_at": NOW,
            "parser_version": "test",
            "acceptance_state": state,
            "accepted_by": "ml2" if state == "accepted" else None,
            "accepted_at": NOW if state == "accepted" else None,
            "synthetic": True,
            "geographic_scope": ["syn"],
            "valid_from": date(2026, 11, 1),
            "valid_to": date(2026, 11, 30),
            "timezone": "Europe/Kaliningrad",
            "grids": [
                {
                    "grid_id": "syn-grid",
                    "city_id": "syn",
                    "crs": "EPSG:32634",
                    "spacing_m": 1000,
                    "origin_x": 0,
                    "origin_y": 0,
                    "version": 1,
                }
            ],
            "graph_version": GRAPH,
            "road_graph_version": road_graph,
            "source_versions": [
                {
                    "source_id": "synthetic",
                    "dataset_id": "synthetic-v1",
                    "retrieved_at": NOW,
                    "checksum": Checksum(algorithm="sha256", value="0" * 64, size_bytes=0),
                    "license": License(name="synthetic-only", url=None, status="permitted"),
                }
            ],
            "files": {},
            "coverage": coverage or [],
            "qa_report": "qa_report.json",
            "limitations": ["synthetic"],
        }
    )


def evidence(evidence_id: str = EVIDENCE) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "source_id": "synthetic",
        "dataset_id": "synthetic-v1",
        "record_id": evidence_id,
        "url": "https://example.org/synthetic",
        "fetched_at": NOW.isoformat(),
        "content_hash": "1" * 64,
        "verification_state": "verified",
    }


def territory(territory_id: str = "syn", level: str = "city") -> dict[str, Any]:
    return {
        "territory_id": territory_id,
        "level": level,
        "name": "Синтетический",
        "parent_id": None if level == "city" else "syn",
        "timezone": "Europe/Kaliningrad",
        "boundary_version": "synthetic-boundary-v1",
        "geometry": SQUARE,
        "area_km2": 1.0,
        "area_method": "synthetic",
        "evidence_ids": [EVIDENCE],
    }


def cell(cell_id: str, status: str = "sampled", population: float | None = 10.0) -> dict[str, Any]:
    return {
        "cell_id": cell_id,
        "city_id": "syn",
        "grid_id": "syn-grid",
        "ix": 0,
        "iy": 0,
        "geometry": SQUARE,
        "square_bbox": SQUARE_BBOX,
        "land_area_km2": 1.0,
        "population": population,
        "population_status": "unknown" if population is None else "estimated",
        "population_method": None if population is None else "synthetic",
        "population_year": None if population is None else 2025,
        "district_shares": [],
        "status": status,
        "exclusion_reason": "NO_BUILDING" if status == "excluded" else None,
        "evidence_ids": [EVIDENCE],
    }


def access_node(node_id: str = "walk:way:1:0.0", graph: str = GRAPH) -> dict[str, Any]:
    return {
        "access_node_id": node_id,
        "kind": "walk_network_projection",
        "lon": 20.004,
        "lat": 54.004,
        "graph_version": graph,
        "osm_way_ref": "way/1",
        "offset_along_way_m": 0.0,
        "evidence_ids": [EVIDENCE],
    }


def road_node(node_id: str = "road:way:7:0.0", graph: str = ROAD_GRAPH) -> dict[str, Any]:
    return {
        **access_node(node_id, graph),
        "kind": "road_network_projection",
        "osm_way_ref": "way/7",
    }


def freight_node(node_id: str, kind: str = "port", **overrides: Any) -> dict[str, Any]:
    return {
        "freight_node_id": node_id,
        "kind": kind,
        "city_id": "syn",
        "name": "Узел",
        "operator": None,
        "lon": 20.007,
        "lat": 54.007,
        "osm_refs": [f"way/{node_id}"],
        "rule": "synthetic",
        "access_node_id": "road:way:7:0.0",
        "access_distance_m": 20.0,
        "status": "accepted",
        "quarantine_reasons": [],
        "evidence_ids": [EVIDENCE],
        **overrides,
    }


def freight_site(site_id: str, **overrides: Any) -> dict[str, Any]:
    return {
        "site_id": site_id,
        "kind": "industrial",
        "city_id": "syn",
        "name": None,
        "lon": 20.003,
        "lat": 54.003,
        "area_m2": 10_000.0,
        "osm_ref": f"way/{site_id}",
        "access_node_id": "road:way:7:0.0",
        "access_distance_m": 30.0,
        "evidence_ids": [EVIDENCE],
        **overrides,
    }


def point(cell_id: str, **overrides: Any) -> dict[str, Any]:
    return {
        "point_id": f"p-{cell_id}",
        "cell_id": cell_id,
        "lon": 20.005,
        "lat": 54.005,
        "initial_center_lon": 20.005,
        "initial_center_lat": 54.005,
        "placement": "cell_center",
        "building_ref": "way/2",
        "offset_m": 0,
        "access_node_id": "walk:way:1:0.0",
        "access_distance_m": 12.5,
        "evidence_ids": [EVIDENCE],
        **overrides,
    }


def destination(destination_id: str, category: str, **overrides: Any) -> dict[str, Any]:
    return {
        "destination_id": destination_id,
        "category": category,
        "serving_city_id": "syn",
        "name": "Объект",
        "lon": 20.006,
        "lat": 54.006,
        "inside_city_boundary": True,
        "osm_ref": f"way/{destination_id}",
        "access_node_id": "walk:way:1:0.0",
        "access_distance_m": 5.0,
        "status": "accepted",
        "quarantine_reasons": [],
        "evidence_ids": [EVIDENCE],
        **overrides,
    }


def collections(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "evidence": [evidence()],
        "territories": [territory()],
        "population_cells": [cell("c1"), cell("c2", status="excluded")],
        "sample_points": [point("c1")],
        "destinations": [],
        "category_ownership": [],
        "access_nodes": [access_node()],
    }
    base.update(overrides)
    return base


QA = {"checks": [{"check_id": "synthetic_check", "status": "pass", "detail": "synthetic"}]}
