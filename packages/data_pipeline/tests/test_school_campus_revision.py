"""Exact geometry failures use synthetic shapes; immutable revision checks use published bundles."""

import copy
import json
from typing import Any

import pytest
from a2_data_pipeline.contract_v2 import load_accepted_data
from a2_data_pipeline.paths import data_dir
from a2_data_pipeline.school_campus_revision import (
    BASE_BUNDLE,
    CAMPUSES,
    DATASET_ID,
    NEXT_BUNDLE,
    PROTECTED,
    ROSTER_SHA256,
    SOURCE_ID,
    verify_geometry,
    verify_source_identities,
)
from a2_data_pipeline.store import load_bundle, sha256_file


def synthetic_polygons() -> tuple[dict[str, Any], dict[str, Any]]:
    site = {
        "tags": {"amenity": "school"},
        "node_refs": [1, 2, 3, 4, 1],
        "coordinates": [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
    }
    building = {
        "tags": {"building": "yes", "addr:street": "Synthetic Street", "addr:housenumber": "2"},
        "node_refs": [5, 6, 7, 8, 5],
        "coordinates": [[1, 1], [3, 1], [3, 3], [1, 3], [1, 1]],
    }
    return site, building


def test_exact_address_and_entire_building_are_required() -> None:
    site, building = synthetic_polygons()
    verify_geometry(site, building, "Synthetic Street", "2")
    wrong = copy.deepcopy(building)
    wrong["tags"]["addr:housenumber"] = "2А"
    with pytest.raises(ValueError, match="EXACT_FOUNDER_BUILDING_ADDRESS"):
        verify_geometry(site, wrong, "Synthetic Street", "2")
    # Centroid is inside the site, but part of the building lies outside.
    crossing = copy.deepcopy(building)
    crossing["coordinates"] = [[8, 1], [11, 1], [11, 3], [8, 3], [8, 1]]
    with pytest.raises(ValueError, match="WHOLE_BUILDING_MUST_BE_CONTAINED_NO_BUFFER"):
        verify_geometry(site, crossing, "Synthetic Street", "2")


def test_invalid_or_open_polygon_is_not_repaired_for_acceptance() -> None:
    site, building = synthetic_polygons()
    building["node_refs"][-1] = 99
    with pytest.raises(ValueError, match="CLOSED_SOURCE_POLYGON"):
        verify_geometry(site, building, "Synthetic Street", "2")


@pytest.mark.parametrize("failure", ["roster", "pbf", "source_version"])
def test_source_bytes_and_version_cannot_be_relabelled(failure: str) -> None:
    base = load_bundle(BASE_BUNDLE)
    pbf_hash = next(
        s.checksum.value for s in base.manifest.source_versions if s.source_id == "osm_geofabrik"
    )
    roster_hash = ROSTER_SHA256
    if failure == "roster":
        roster_hash = "0" * 64
    elif failure == "pbf":
        pbf_hash = "0" * 64
    else:
        versions = [
            s.model_copy(update={"dataset_id": "unreviewed-version"})
            if s.source_id == "klgd_school_sites"
            else s
            for s in base.manifest.source_versions
        ]
        base = base.model_copy(
            update={"manifest": base.manifest.model_copy(update={"source_versions": versions})}
        )
    with pytest.raises(ValueError, match="SHA256_MISMATCH|SOURCE_VERSION_MISMATCH"):
        verify_source_identities(base, roster_hash, pbf_hash)


def test_revision_changes_only_two_reviewed_campus_identities() -> None:
    old, new = load_bundle(BASE_BUNDLE), load_bundle(NEXT_BUNDLE)
    promoted = {case.destination_id for case in CAMPUSES}
    old_by_id = {d.destination_id: d for d in old.destinations}
    new_by_id = {d.destination_id: d for d in new.destinations}
    assert old_by_id.keys() == new_by_id.keys()
    assert {key for key in old_by_id if old_by_id[key] != new_by_id[key]} == promoted
    for key in promoted:
        assert new_by_id[key].status == "accepted" and old_by_id[key].status == "quarantined"
        assert old_by_id[key].model_dump(
            exclude={"name", "status", "quarantine_reasons", "evidence_ids"}
        ) == new_by_id[key].model_dump(
            exclude={"name", "status", "quarantine_reasons", "evidence_ids"}
        )
    assert sum(d.category == "school" and d.status == "accepted" for d in new.destinations) == 79
    assert sum(d.category == "school" and d.status == "quarantined" for d in new.destinations) == 51
    assert new.manifest.coverage == old.manifest.coverage
    for name in PROTECTED:
        assert new.manifest.files[name].sha256 == old.manifest.files[name].sha256
        assert getattr(new, name) == getattr(old, name)
    assert (
        sha256_file(data_dir() / "bundles" / BASE_BUNDLE / "manifest.json").value
        == "b174039c6c2d1feb33a46063418e5975afd5bc58095c20e8ff65f73434e25fa1"
    )


def test_campus1_and_campus2_stay_distinct_with_source_versions_preserved() -> None:
    old, new = load_accepted_data(BASE_BUNDLE), load_accepted_data(NEXT_BUNDLE)
    for source_id, version in old.source_versions.items():
        assert new.source_versions[source_id] == version
    assert new.source_versions[SOURCE_ID] == DATASET_ID
    ids = {
        "kaliningrad-school-klgd_education-4409878",
        "kaliningrad-school-klgd_education-4409869",
        *(case.destination_id for case in CAMPUSES),
    }
    campuses = [d for d in new.destinations if d.destination_id in ids]
    assert len(campuses) == len({d.object_identity for d in campuses}) == 4
    report = json.loads(
        (
            data_dir() / "manifests" / "qa" / "kaliningrad-school-campus-revision-2026-11-10.json"
        ).read_text()
    )
    assert {r["institution"]["institution_id"] for r in report["records"]} == {"4409878", "4409869"}
    assert all(
        r["whole_building_contained"] and r["geometry_buffer_m"] == 0 for r in report["records"]
    )
    assert report["index_ready"] is False
