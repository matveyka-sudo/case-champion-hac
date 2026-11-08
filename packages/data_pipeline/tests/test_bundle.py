"""Schema and publication rules on synthetic bundles (synthetic=True)."""

import json
from pathlib import Path

import pytest
from a2_data_pipeline.bundle import CoverageRecord, PopulationCell
from a2_data_pipeline.store import (
    BundleError,
    BundleIntegrityError,
    BundleNotAcceptedError,
    BundleNotFoundError,
    load_bundle,
    publish_bundle,
)
from pydantic import ValidationError
from synthetic_bundle import QA, access_node, cell, collections, destination, manifest, point


def test_publish_and_load_round_trip(tmp_path: Path) -> None:
    publish_bundle(manifest(), collections(), QA, root=tmp_path)
    data = load_bundle("synthetic-test-v1", root=tmp_path)
    assert data.manifest.synthetic is True
    assert data.manifest.files["population_cells"].rows == 2
    assert data.manifest.files["qa_report"].rows == 1
    assert [p.cell_id for p in data.sample_points] == ["c1"]
    assert data.qa_report == QA


def test_published_bundle_is_immutable(tmp_path: Path) -> None:
    publish_bundle(manifest(), collections(), QA, root=tmp_path)
    with pytest.raises(BundleError):
        publish_bundle(manifest(), collections(), QA, root=tmp_path)


def test_unknown_and_unaccepted(tmp_path: Path) -> None:
    with pytest.raises(BundleNotFoundError):
        load_bundle("missing", root=tmp_path)
    publish_bundle(manifest("draft-v1", state="draft"), collections(), QA, root=tmp_path)
    with pytest.raises(BundleNotAcceptedError):
        load_bundle("draft-v1", root=tmp_path)
    assert load_bundle("draft-v1", root=tmp_path, allow_unaccepted=True)


@pytest.mark.parametrize("name", ["sample_points.json", "qa_report.json"])
def test_tampered_file_is_rejected(tmp_path: Path, name: str) -> None:
    target = publish_bundle(manifest(), collections(), QA, root=tmp_path)
    path = target / name
    payload = json.loads(path.read_text())
    if isinstance(payload, list):
        payload[0]["lon"] = 21.0
    else:
        payload["checks"] = []
    path.write_text(json.dumps(payload))
    with pytest.raises(BundleIntegrityError):
        load_bundle("synthetic-test-v1", root=tmp_path)


def test_failed_validation_leaves_nothing(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        publish_bundle(manifest(), collections(sample_points=[]), QA, root=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_point_in_excluded_cell_rejected(tmp_path: Path) -> None:
    bad = collections(sample_points=[point("c1"), point("c2")])
    with pytest.raises(ValidationError, match="excluded cell"):
        publish_bundle(manifest(), bad, QA, root=tmp_path)


def test_point_cannot_leave_its_square(tmp_path: Path) -> None:
    outside = collections(sample_points=[point("c1", lon=20.5)])
    with pytest.raises(ValidationError, match="leaves its original cell"):
        publish_bundle(manifest(), outside, QA, root=tmp_path)
    center_outside = collections(sample_points=[point("c1", initial_center_lat=55.0)])
    with pytest.raises(ValidationError, match="leaves its original cell"):
        publish_bundle(manifest(), center_outside, QA, root=tmp_path)


def test_access_nodes_must_resolve_and_share_graph(tmp_path: Path) -> None:
    missing = collections(sample_points=[point("c1", access_node_id="walk:way:9:0.0")])
    with pytest.raises(ValidationError, match="access_node_id"):
        publish_bundle(manifest(), missing, QA, root=tmp_path)
    other_graph = collections(access_nodes=[access_node(graph="another-graph")])
    with pytest.raises(ValidationError, match="another graph version"):
        publish_bundle(manifest(), other_graph, QA, root=tmp_path)


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
    school = destination("school-1", "school")
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
        publish_bundle(manifest(), bad, QA, root=tmp_path)
    quarantined = {
        **school,
        "status": "quarantined",
        "quarantine_reasons": ["OWNERSHIP_UNVERIFIED"],
    }
    publish_bundle(
        manifest(),
        collections(destinations=[quarantined], category_ownership=[osm_only]),
        QA,
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
