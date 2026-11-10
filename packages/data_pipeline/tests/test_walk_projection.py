"""The pure adapter preserves source fields, historical times, and unknowns."""

import hashlib
import json
import subprocess
import sys

import pytest
from a2_data_pipeline.bundle import BundleData, WalkConnector
from a2_data_pipeline.store import load_bundle
from a2_data_pipeline.walk_projection import project_walk_connectors
from synthetic_bundle import EVIDENCE, QA, collections, destination, manifest


def bundle() -> BundleData:
    rows = collections(
        destinations=[destination("mall", "shopping_center")],
        walk_connectors=[
            WalkConnector(
                physical_node_id="physical:point:p-c1",
                entity_kind="point",
                entity_id="p-c1",
                access_node_id="walk:way:1:0.0",
                length_m=9.7,
                traversable=True,
                reasons=[],
                evidence_ids=[EVIDENCE],
            ),
            WalkConnector(
                physical_node_id="physical:destination:mall",
                entity_kind="destination",
                entity_id="mall",
                access_node_id="walk:way:1:0.0",
                length_m=18.1,
                traversable=None,
                reasons=["CROSSES_BARRIER"],
                evidence_ids=[EVIDENCE],
            ),
        ],
    )
    return BundleData(manifest=manifest(), qa_report=QA, **rows)


def test_projection_keeps_evidence_order_and_unknown_connector() -> None:
    data = bundle()
    result = project_walk_connectors(
        data,
        walking_speed_kmh=5,
        speed_evidence_refs=("profile:z", "profile:a"),
        profile_version="accepted-test-profile",
    )
    assert result == [
        {
            "snapshot_id": "synthetic-test-v1",
            "source_graph_version": data.manifest.graph_version,
            "physical_node_id": connector.physical_node_id,
            "access_node_id": connector.access_node_id,
            "physical_location": {"lon": place.lon, "lat": place.lat},
            "access_location": {"lon": data.access_nodes[0].lon, "lat": data.access_nodes[0].lat},
            "seconds": seconds,
            "traversable": connector.traversable,
            "time_basis": "walk-connector-geometry-v2:straight_line_length/accepted-test-profile",
            "evidence_refs": [EVIDENCE, "profile:z", "profile:a"],
        }
        for connector, place, seconds in zip(
            data.walk_connectors,
            [data.sample_points[0], data.destinations[0]],
            [7.0, None],
            strict=True,
        )
    ]


@pytest.mark.parametrize("speed", [0, 0.49, 25.01, float("nan"), float("inf")])
def test_projection_rejects_unaccepted_speed(speed: float) -> None:
    with pytest.raises(ValueError, match="accepted walking speed"):
        project_walk_connectors(
            bundle(),
            walking_speed_kmh=speed,
            speed_evidence_refs=["profile:test"],
            profile_version="test",
        )


def test_projection_requires_speed_evidence() -> None:
    with pytest.raises(ValueError, match="accepted walking speed"):
        project_walk_connectors(
            bundle(),
            walking_speed_kmh=5,
            speed_evidence_refs=[],
            profile_version="test",
        )


def test_pure_module_imports_without_gis_stack() -> None:
    code = (
        "import sys; from a2_data_pipeline.walk_projection import project_walk_connectors; "
        "assert not any(n.split('.')[0] in {'shapely','pyproj','pyarrow','osmium','numpy','rasterio'} "
        "for n in sys.modules)"
    )
    subprocess.run([sys.executable, "-c", code], check=True, timeout=20)


def test_existing_wrapper_and_pure_projection_have_identical_moscow_bytes() -> None:
    from a2_data_pipeline.supplements import walk_connectors

    data = load_bundle("moscow-urban-2026-11-10.3")
    pure = project_walk_connectors(
        data,
        walking_speed_kmh=5,
        speed_evidence_refs=["profile:z", "profile:a"],
        profile_version="accepted-test-profile",
    )
    old_public = walk_connectors(
        data.manifest.bundle_id,
        walking_speed_kmh=5,
        speed_evidence_refs=["profile:z", "profile:a"],
        profile_version="accepted-test-profile",
    )
    assert json.dumps(pure).encode() == json.dumps(old_public).encode()
    assert len(pure) == 11983
    # Captured from the pre-extraction supplements.py implementation SHA256
    # 850903980360b2fbdb3ade53821d94e9ac4b83186b80e2ed95d3de78b7dedd24.
    # This checks the old result independently of the newly delegated wrapper.
    assert hashlib.sha256(json.dumps(pure).encode()).hexdigest() == (
        "c0b297334f20ee553ad32d50d9e326dc8a6c62321aac8c9e4f01d41de6848917"
    )
