"""Mapping of ML2 bundles to BACKEND's a2_contracts.v2.AcceptedData."""

import hashlib
import importlib
import json
from pathlib import Path
from typing import Any

import pytest
from a2_data_pipeline.bundle import CoverageRecord
from a2_data_pipeline.contract_v2 import registry_inputs, registry_options, to_accepted_data_v2
from a2_data_pipeline.store import list_bundles, load_bundle, publish_bundle
from synthetic_bundle import (
    EVIDENCE,
    GRAPH,
    QA,
    ROAD_GRAPH,
    access_node,
    collections,
    destination,
    freight_node,
    freight_site,
    manifest,
    road_node,
    territory,
)

COVERAGE = [
    CoverageRecord(
        collection="destinations",
        city_id="syn",
        category="shopping_center",
        status="partial",
        reason="synthetic rule set",
        evidence_ids=[EVIDENCE],
    ),
    CoverageRecord(
        collection="destinations",
        city_id="syn",
        category="school",
        status="unknown",
        reason="registry missing",
    ),
]


def synthetic_payload(tmp_path: Path) -> dict[str, Any]:
    mall = destination("mall-1", "shopping_center")
    school = destination(
        "school-1", "school", status="quarantined", quarantine_reasons=["OWNERSHIP_UNVERIFIED"]
    )
    parts = collections(
        territories=[territory(), territory("syn-d1", level="district")],
        destinations=[mall, school],
    )
    publish_bundle(manifest(coverage=COVERAGE), parts, QA, root=tmp_path)
    return to_accepted_data_v2(load_bundle("synthetic-test-v1", root=tmp_path))


def test_mapping_keeps_rules(tmp_path: Path) -> None:
    payload = synthetic_payload(tmp_path)
    assert payload["schema_version"] == "2"
    assert [d["destination_id"] for d in payload["destinations"]] == ["mall-1"]
    assert [t["territory_id"] for t in payload["territories"]] == ["syn"]
    assert payload["provenance"]["calendar_version"] == "not_collected"
    assert payload["category_ownership"] == []
    assert payload["coverage_by_mode_date_category"] == [
        {
            "territory_ids": ["syn"],
            "modes": ["walk"],
            "categories": ["shopping_center"],
            "valid_period": {"start": None, "end": None},
            "completeness": "partial",
            "source_refs": [EVIDENCE],
        }
    ]
    assert payload["evidence"][0]["source_version"] == payload["source_versions"]["synthetic"]
    cell = payload["population_cells"][0]
    assert cell["geometry"]["bbox"] == {"west": 20.0, "south": 54.0, "east": 20.01, "north": 54.01}
    assert payload["qa"]["checks"] == ["synthetic_check"]


def freight_payload(tmp_path: Path) -> dict[str, Any]:
    parts = collections(
        access_nodes=[access_node(), road_node(), road_node("road:way:8:0.0")],
        freight_nodes=[
            freight_node("port-1", operator="АО «Синтетический порт»"),
            freight_node(
                "rail-1",
                kind="rail_terminal",
                access_node_id="road:way:8:0.0",
                status="quarantined",
                quarantine_reasons=["FREIGHT_OPERATIONS_UNVERIFIED"],
            ),
        ],
        freight_sites=[freight_site("site-1")],
    )
    publish_bundle(manifest("freight-v1", road_graph=ROAD_GRAPH), parts, QA, root=tmp_path)
    return to_accepted_data_v2(load_bundle("freight-v1", root=tmp_path))


def test_freight_mapping_keeps_only_accepted_nodes(tmp_path: Path) -> None:
    payload = freight_payload(tmp_path)
    assert payload["freight_nodes"] == [
        {
            "freight_node_id": "port-1",
            "access_node_id": "road:way:7:0.0",
            "kind": "port",
            "territory_id": "syn",
            "location": {"lon": 20.007, "lat": 54.007},
            "source_refs": [EVIDENCE],
        }
    ]
    assert [s["site_id"] for s in payload["freight_sites"]] == ["site-1"]
    # the quarantined station's road node is not exported; walk and road nodes are
    assert {n["node_id"]: n["kind"] for n in payload["access_nodes"]} == {
        "walk:way:1:0.0": "walk_network_projection",
        "road:way:7:0.0": "road_network_projection",
    }
    assert payload["provenance"]["graph_version"] == f"{GRAPH}+{ROAD_GRAPH}"
    assert payload["freight_compatibility"] == []


def test_districts_become_area_options(tmp_path: Path) -> None:
    synthetic_payload(tmp_path)
    options = registry_options(load_bundle("synthetic-test-v1", root=tmp_path))
    assert options["areas"] == [{"area_id": "syn-d1", "city_id": "syn", "name": "Синтетический"}]
    assert [t["territory_id"] for t in options["territories"]] == ["syn"]


def write_directory(root: Path, timezone: str = "Europe/Kaliningrad") -> None:
    """A minimal published territory directory holding the synthetic city and its region."""

    rows = [
        {
            "territory_id": territory_id,
            "level": level,
            "name": "Синтетический",
            "timezone": timezone,
            "timezone_basis": "subject_iana",
            "parent_region_id": parent,
            "capital_city_id": capital,
            "wikidata_id": wikidata_id,
            "iso_code": None,
            "population": None,
            "population_date": None,
            "disputed": False,
        }
        for territory_id, level, parent, capital, wikidata_id in (
            ("region-syn", "region", None, "syn", "Q1"),
            ("syn", "city", "region-syn", None, "Q10"),
        )
    ]
    payload = json.dumps(rows).encode()
    target = root / "syn-territories"
    target.mkdir(parents=True)
    (target / "territories.json").write_bytes(payload)
    files = {"territories": {"path": "territories.json", "rows": len(rows)}}
    files["territories"]["sha256"] = hashlib.sha256(payload).hexdigest()
    (target / "manifest.json").write_text(json.dumps({"files": files}))


def test_registry_inputs_list_each_territory_once(tmp_path: Path) -> None:
    synthetic_payload(tmp_path)
    write_directory(tmp_path / "directories")
    inputs = registry_inputs(
        "syn-territories",
        ["synthetic-test-v1"],
        directory_root=tmp_path / "directories",
        bundle_root=tmp_path,
    )
    assert [t["territory_id"] for t in inputs["territories"]] == ["region-syn", "syn"]
    assert inputs["territories"][1]["parent_region_id"] == "region-syn"
    assert inputs["areas"] == [{"area_id": "syn-d1", "city_id": "syn", "name": "Синтетический"}]


def test_registry_inputs_reject_a_zone_mismatch(tmp_path: Path) -> None:
    synthetic_payload(tmp_path)
    write_directory(tmp_path / "directories", timezone="Europe/Moscow")
    with pytest.raises(ValueError, match="zone"):
        registry_inputs(
            "syn-territories",
            ["synthetic-test-v1"],
            directory_root=tmp_path / "directories",
            bundle_root=tmp_path,
        )


def test_unaccepted_bundle_is_not_mapped(tmp_path: Path) -> None:
    publish_bundle(manifest("draft-v1", state="draft"), collections(), QA, root=tmp_path)
    with pytest.raises(ValueError, match="accepted"):
        to_accepted_data_v2(load_bundle("draft-v1", root=tmp_path, allow_unaccepted=True))


def test_backend_model_accepts_payloads(tmp_path: Path) -> None:
    """Runs when BACKEND's a2_contracts.v2 (PR #8) is importable."""

    v2 = pytest.importorskip("a2_contracts.v2")
    v2.AcceptedData.model_validate(synthetic_payload(tmp_path))
    v2.AcceptedData.model_validate(freight_payload(tmp_path))
    for bundle_id in list_bundles():
        data = load_bundle(bundle_id)
        if data.manifest.acceptance_state == "accepted":
            accepted = importlib.import_module("a2_data_pipeline.contract_v2").load_accepted_data(
                bundle_id
            )
            assert accepted.snapshot_id == bundle_id
