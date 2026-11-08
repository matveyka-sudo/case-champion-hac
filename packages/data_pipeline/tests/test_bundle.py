"""Schema and publication rules on synthetic bundles (synthetic=True)."""

import json
from pathlib import Path
from typing import Any

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
from synthetic_bundle import (
    QA,
    ROAD_GRAPH,
    access_node,
    cell,
    collections,
    destination,
    freight_node,
    freight_site,
    manifest,
    point,
    road_node,
)


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


def freight_parts(**overrides: Any) -> dict[str, Any]:
    parts = collections(
        access_nodes=[access_node(), road_node()],
        freight_nodes=[freight_node("port-1")],
        freight_sites=[freight_site("site-1")],
    )
    parts.update(overrides)
    return parts


def test_freight_round_trip_and_old_bundles(tmp_path: Path) -> None:
    publish_bundle(manifest(road_graph=ROAD_GRAPH), freight_parts(), QA, root=tmp_path)
    data = load_bundle("synthetic-test-v1", root=tmp_path)
    assert [n.freight_node_id for n in data.freight_nodes] == ["port-1"]
    assert [s.site_id for s in data.freight_sites] == ["site-1"]
    assert data.manifest.files["freight_sites"].rows == 1
    # a bundle published before freight existed loads with empty freight lists
    publish_bundle(manifest("no-freight-v1"), collections(), QA, root=tmp_path)
    old = load_bundle("no-freight-v1", root=tmp_path)
    assert old.freight_nodes == [] and old.freight_sites == []
    assert "freight_nodes" not in old.manifest.files
    # optional collections are hashed like the others
    path = tmp_path / "synthetic-test-v1" / "freight_sites.json"
    sites = json.loads(path.read_text())
    sites[0]["area_m2"] = 1.0
    path.write_text(json.dumps(sites))
    with pytest.raises(BundleIntegrityError, match="freight_sites"):
        load_bundle("synthetic-test-v1", root=tmp_path)


def test_freight_joins_roads_and_people_join_walkways(tmp_path: Path) -> None:
    walk_site = freight_parts(freight_sites=[freight_site("s", access_node_id="walk:way:1:0.0")])
    with pytest.raises(ValidationError, match="freight must join the road_network_projection"):
        publish_bundle(manifest(road_graph=ROAD_GRAPH), walk_site, QA, root=tmp_path)
    road_point = freight_parts(sample_points=[point("c1", access_node_id="road:way:7:0.0")])
    with pytest.raises(ValidationError, match="sample_points must join the walk"):
        publish_bundle(manifest(road_graph=ROAD_GRAPH), road_point, QA, root=tmp_path)
    with pytest.raises(ValidationError, match="another graph version"):
        publish_bundle(manifest(), freight_parts(), QA, root=tmp_path)
    unknown_node = freight_parts(freight_nodes=[freight_node("n", access_node_id="road:way:9:0.0")])
    with pytest.raises(ValidationError, match="unresolved freight.access_node_id"):
        publish_bundle(manifest(road_graph=ROAD_GRAPH), unknown_node, QA, root=tmp_path)


def test_quarantined_freight_node_needs_reason(tmp_path: Path) -> None:
    silent = freight_parts(
        freight_nodes=[freight_node("rail-1", kind="rail_terminal", status="quarantined")]
    )
    with pytest.raises(ValidationError, match="quarantine reasons"):
        publish_bundle(manifest(road_graph=ROAD_GRAPH), silent, QA, root=tmp_path)


def test_freight_kind_scopes_only_freight_nodes() -> None:
    with pytest.raises(ValidationError, match="freight_kind"):
        CoverageRecord(
            collection="destinations",
            city_id="syn",
            freight_kind="port",
            status="partial",
            reason="x",
        )
    record = CoverageRecord(
        collection="freight_nodes",
        city_id="syn",
        freight_kind="rail_terminal",
        status="unknown",
        reason="freight operations unverified",
    )
    assert record.confirmed_absence is False


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
