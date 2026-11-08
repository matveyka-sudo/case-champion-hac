"""Mapping of ML2 bundles to BACKEND's a2_contracts.v2.AcceptedData."""

import importlib
from pathlib import Path
from typing import Any

import pytest
from a2_data_pipeline.bundle import CoverageRecord
from a2_data_pipeline.contract_v2 import registry_options, to_accepted_data_v2
from a2_data_pipeline.store import list_bundles, load_bundle, publish_bundle
from synthetic_bundle import EVIDENCE, QA, collections, destination, manifest, territory

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


def test_districts_become_area_options(tmp_path: Path) -> None:
    synthetic_payload(tmp_path)
    options = registry_options(load_bundle("synthetic-test-v1", root=tmp_path))
    assert options["areas"] == [{"area_id": "syn-d1", "city_id": "syn", "name": "Синтетический"}]
    assert [t["territory_id"] for t in options["territories"]] == ["syn"]


def test_unaccepted_bundle_is_not_mapped(tmp_path: Path) -> None:
    publish_bundle(manifest("draft-v1", state="draft"), collections(), QA, root=tmp_path)
    with pytest.raises(ValueError, match="accepted"):
        to_accepted_data_v2(load_bundle("draft-v1", root=tmp_path, allow_unaccepted=True))


def test_backend_model_accepts_payloads(tmp_path: Path) -> None:
    """Runs when BACKEND's a2_contracts.v2 (PR #8) is importable."""

    v2 = pytest.importorskip("a2_contracts.v2")
    v2.AcceptedData.model_validate(synthetic_payload(tmp_path))
    for bundle_id in list_bundles():
        data = load_bundle(bundle_id)
        if data.manifest.acceptance_state == "accepted":
            accepted = importlib.import_module("a2_data_pipeline.contract_v2").load_accepted_data(
                bundle_id
            )
            assert accepted.snapshot_id == bundle_id
