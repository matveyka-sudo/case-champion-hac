"""Synthetic bundles (synthetic=True) for schema and publication rules only."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from a2_data_pipeline.bundle import BundleManifest, CoverageRecord, PopulationCell
from a2_data_pipeline.manifest import Checksum, License
from a2_data_pipeline.store import (
    BundleError,
    BundleIntegrityError,
    BundleNotAcceptedError,
    BundleNotFoundError,
    load_bundle,
    publish_bundle,
)
from pydantic import ValidationError

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
SQUARE = {"type": "Polygon", "coordinates": [[[20, 54], [20.01, 54], [20.01, 54.01], [20, 54]]]}


def manifest(bundle_id: str = "synthetic-test-v1", state: str = "accepted") -> BundleManifest:
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
            "valid_from": None,
            "valid_to": None,
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
            "source_versions": [
                {
                    "source_id": "synthetic",
                    "dataset_id": "synthetic",
                    "retrieved_at": NOW,
                    "checksum": Checksum(algorithm="sha256", value="0" * 64, size_bytes=0),
                    "license": License(name=None, url=None, status="permitted"),
                }
            ],
            "files": {},
            "coverage": [],
            "qa_report": "qa_report.json",
            "limitations": ["synthetic"],
        }
    )


def cell(cell_id: str, status: str = "sampled", population: float | None = 10.0) -> dict[str, Any]:
    return {
        "cell_id": cell_id,
        "city_id": "syn",
        "grid_id": "syn-grid",
        "ix": 0,
        "iy": 0,
        "geometry": SQUARE,
        "land_area_km2": 1.0,
        "population": population,
        "population_status": "unknown" if population is None else "estimated",
        "population_method": None if population is None else "synthetic",
        "population_year": None if population is None else 2025,
        "district_shares": [],
        "status": status,
        "exclusion_reason": "NO_BUILDING" if status == "excluded" else None,
    }


def point(cell_id: str) -> dict[str, Any]:
    return {
        "point_id": f"p-{cell_id}",
        "cell_id": cell_id,
        "lon": 20.005,
        "lat": 54.005,
        "placement": "cell_center",
        "building_ref": "way/1",
        "offset_m": 0,
    }


def collections(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "evidence": [],
        "territories": [],
        "population_cells": [cell("c1"), cell("c2", status="excluded")],
        "sample_points": [point("c1")],
        "destinations": [],
        "category_ownership": [],
    }
    base.update(overrides)
    return base


def test_publish_and_load_round_trip(tmp_path: Path) -> None:
    publish_bundle(manifest(), collections(), {"checks": []}, root=tmp_path)
    data = load_bundle("synthetic-test-v1", root=tmp_path)
    assert data.manifest.synthetic is True
    assert data.manifest.files["population_cells"].rows == 2
    assert [p.cell_id for p in data.sample_points] == ["c1"]


def test_published_bundle_is_immutable(tmp_path: Path) -> None:
    publish_bundle(manifest(), collections(), {}, root=tmp_path)
    with pytest.raises(BundleError):
        publish_bundle(manifest(), collections(), {}, root=tmp_path)


def test_unknown_and_unaccepted(tmp_path: Path) -> None:
    with pytest.raises(BundleNotFoundError):
        load_bundle("missing", root=tmp_path)
    publish_bundle(manifest("draft-v1", state="draft"), collections(), {}, root=tmp_path)
    with pytest.raises(BundleNotAcceptedError):
        load_bundle("draft-v1", root=tmp_path)
    assert load_bundle("draft-v1", root=tmp_path, allow_unaccepted=True)


def test_tampered_file_is_rejected(tmp_path: Path) -> None:
    target = publish_bundle(manifest(), collections(), {}, root=tmp_path)
    path = target / "sample_points.json"
    rows = json.loads(path.read_text())
    rows[0]["lon"] = 21.0
    path.write_text(json.dumps(rows))
    with pytest.raises(BundleIntegrityError):
        load_bundle("synthetic-test-v1", root=tmp_path)


def test_failed_validation_leaves_nothing(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        publish_bundle(manifest(), collections(sample_points=[]), {}, root=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_point_in_excluded_cell_rejected(tmp_path: Path) -> None:
    bad = collections(sample_points=[point("c1"), point("c2")])
    with pytest.raises(ValidationError, match="excluded cell"):
        publish_bundle(manifest(), bad, {}, root=tmp_path)


def test_unknown_population_is_not_zero() -> None:
    unknown = PopulationCell.model_validate(cell("c1", population=None))
    assert unknown.population is None
    with pytest.raises(ValidationError):
        PopulationCell.model_validate({**cell("c1"), "population_status": "zero"})
    with pytest.raises(ValidationError):
        PopulationCell.model_validate(
            {**cell("c1", population=None), "population_status": "estimated"}
        )


def test_district_shares_do_not_double_population() -> None:
    shares = [
        {"territory_id": "d1", "area_share": 0.5, "population": 6.0},
        {"territory_id": "d2", "area_share": 0.5, "population": 6.0},
    ]
    with pytest.raises(ValidationError, match="exceed"):
        PopulationCell.model_validate({**cell("c1"), "district_shares": shares})


def test_public_category_needs_registry_ownership(tmp_path: Path) -> None:
    school = {
        "destination_id": "school-1",
        "category": "school",
        "serving_city_id": "syn",
        "name": "Школа",
        "lon": 20.0,
        "lat": 54.0,
        "inside_city_boundary": True,
        "osm_ref": "way/2",
        "status": "accepted",
        "quarantine_reasons": [],
        "evidence_ids": [],
    }
    osm_only = {
        "destination_id": "school-1",
        "category": "school",
        "ownership": "municipal",
        "basis": "osm_tags",
        "verification_state": "unknown",
        "evidence_ids": [],
    }
    bad = collections(destinations=[school], category_ownership=[osm_only])
    with pytest.raises(ValidationError, match="registry-verified"):
        publish_bundle(manifest(), bad, {}, root=tmp_path)
    quarantined = {
        **school,
        "status": "quarantined",
        "quarantine_reasons": ["OWNERSHIP_UNVERIFIED"],
    }
    publish_bundle(
        manifest(),
        collections(destinations=[quarantined], category_ownership=[osm_only]),
        {},
        root=tmp_path,
    )


def test_absence_requires_complete_scope() -> None:
    with pytest.raises(ValidationError):
        CoverageRecord(
            collection="destinations",
            city_id="syn",
            category="airport",
            status="partial",
            confirmed_absence=True,
            reason="test",
        )
