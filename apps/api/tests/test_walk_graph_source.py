"""TEST_ONLY extraction receipts plus one reviewed preserved Moscow replay."""

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest
from a2_api.adapters.walk_graph_source import verify_derived_graph
from a2_data_pipeline.contract_v2 import load_accepted_data
from a2_data_pipeline.manifest import dataset_manifest_path
from a2_data_pipeline.paths import bundles_dir


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, sort_keys=True).encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def packet(tmp_path):
    snapshot = load_accepted_data("kaliningrad-urban-2026-11-10.2")
    dataset = snapshot.source_versions["osm_geofabrik"]
    source_path = dataset_manifest_path(dataset)
    source = json.loads(source_path.read_text())
    shutil.copyfile(source_path, tmp_path / "source-manifest.json")
    source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    bbox = [-180, -90, 180, 90]
    access = {p.access_node_id for p in snapshot.sample_points} | {
        d.access_node_id for d in snapshot.destinations
    }
    endpoints = [
        dict(kind="point", id=p.point_id, lon=p.location.lon, lat=p.location.lat)
        for p in snapshot.sample_points
    ]
    endpoints.extend(
        dict(kind="destination", id=d.destination_id, lon=d.location.lon, lat=d.location.lat)
        for d in snapshot.destinations
    )
    endpoints.extend(
        dict(kind="access", id=n.node_id, lon=n.location.lon, lat=n.location.lat)
        for n in snapshot.access_nodes
        if n.node_id in access
    )
    inventory = dict(
        bbox=bbox,
        bundle_manifest_sha256=hashlib.sha256(
            (bundles_dir() / snapshot.snapshot_id / "manifest.json").read_bytes()
        ).hexdigest(),
        endpoints=endpoints,
        outside_count=0,
        snapshot_id=snapshot.snapshot_id,
    )
    receipt = dict(
        schema="waydex-source-bound-osm-extract/1",
        source_manifest_sha256=source_hash,
        upstream_source_sha256=source["checksum"]["value"],
        upstream_source_bytes=source["checksum"]["size_bytes"],
        upstream_source_filename=Path(source["raw_path"]).name,
        derived_input_sha256="9" * 64,
        derived_input_bytes=100,
        derived_input_filename="TEST_ONLY-derived.osm.pbf",
        strategy="smart",
        strategy_options=["types=multipolygon,restriction"],
        tool_version="1.16.0",
        tool_image_id="sha256:TEST_ONLY-tool",
        tool_binary_sha256="a" * 64,
        exit_code=0,
        container_exit_code=0,
        oom_killed=False,
        limit_reason=None,
        outside_paths_complete=False,
        minimum_proven=False,
        way_references_verified=True,
        restriction_member_references_verified=True,
        bbox=bbox,
        endpoint_snapshot_id=snapshot.snapshot_id,
        endpoint_counts={"outside_count": 0},
    )
    receipt["endpoint_inventory_sha256"] = write(tmp_path / "endpoint-inventory.json", inventory)
    receipt["fileinfo_sha256"] = write(
        tmp_path / "extract-fileinfo.json",
        dict(
            file=dict(size=100, name="/inert/TEST_ONLY-derived.osm.pbf"),
            header=dict(boxes=[bbox], option={"generator": "osmium/1.16.0"}),
            data=dict(
                objects_ordered=True,
                multiple_versions=False,
                count=dict(nodes=2, ways=1, relations=1),
            ),
        ),
    )
    for name, text, key in [
        (
            "way-refs-check.txt",
            "There are 2 nodes, 1 ways, and 1 relations in this file.\nNodes in ways missing: 0\n",
            "way_refs_report_sha256",
        ),
        (
            "restriction-refs-check.txt",
            "There are 2 nodes, 1 ways, and 1 relations in this file.\nNodes in ways missing: 0\nNodes in relations missing: 0\nWays in relations missing: 0\nRelations in relations missing: 0\n",
            "restriction_refs_report_sha256",
        ),
        ("restriction-scope.osm.pbf", "TEST_ONLY-binary", "restriction_scope_sha256"),
    ]:
        (tmp_path / name).write_text(text)
        receipt[key] = hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()
    (tmp_path / "tool-binary-sha256.txt").write_text("a" * 64 + "  /usr/bin/osmium\n")
    packages = []
    for name in ["TEST_ONLY-tool.deb", "TEST_ONLY-lib.deb"]:
        p = tmp_path / "tool-packages" / name
        p.parent.mkdir(exist_ok=True)
        p.write_bytes(b"TEST_ONLY-package")
        packages.append(
            dict(
                path=name, bytes=p.stat().st_size, sha256=hashlib.sha256(p.read_bytes()).hexdigest()
            )
        )
    tool = dict(
        schema="waydex-standard-tool-install/1",
        exit_code=0,
        oom_killed=False,
        limit_reason=None,
        image_id=receipt["tool_image_id"],
        version="osmium version 1.16.0\n",
        packages=packages,
        command=["builder@sha256:TEST_ONLY-build"],
    )
    graph = dict(
        upstream_source={
            "path": receipt["upstream_source_filename"],
            "sha256": receipt["upstream_source_sha256"],
            "bytes": receipt["upstream_source_bytes"],
        },
        derivation={
            "path": "extract-receipt.json",
            "sha256": write(tmp_path / "extract-receipt.json", receipt),
        },
        tool_installation={
            "path": "tool-install-receipt.json",
            "sha256": write(tmp_path / "tool-install-receipt.json", tool),
        },
        source_sha256=receipt["derived_input_sha256"],
        source_bytes=100,
        source_filename=receipt["derived_input_filename"],
        source_manifest_sha256=source_hash,
        source_graph_version=snapshot.provenance.graph_version,
        outside_paths_complete=False,
        image_repo_digests=["builder@sha256:TEST_ONLY-build"],
    )
    return tmp_path, snapshot, graph, receipt, source


def verify(packet, **changes):
    root, snapshot, graph, _, source = packet
    args = dict(
        snapshot=snapshot,
        city_id="kaliningrad",
        source_id="osm_geofabrik",
        dataset_id=source["dataset_id"],
        original_sha256=source["checksum"]["value"],
        original_bytes=source["checksum"]["size_bytes"],
        budget=[16 * 1024 * 1024],
    )
    args.update(changes)
    return verify_derived_graph(graph, root, **args)


def test_complete_source_chain_keeps_extraction_scope_explicit(packet):
    result = verify(packet)
    assert result.source_snapshot_id == packet[1].snapshot_id
    assert result.limitations == ("DERIVED_GRAPH_OUTSIDE_PATHS_INCOMPLETE",)


@pytest.mark.parametrize(
    "attack",
    [
        "half",
        "swap",
        "strategy",
        "minimum",
        "outside",
        "tool",
        "binary",
        "package",
        "missing_scope",
        "scope_hash",
        "way_report",
        "restriction_report",
        "fileinfo",
        "target_coordinate",
        "source_context",
        "bad_path",
        "budget",
    ],
)
def test_incomplete_or_rebound_derivation_never_passes(packet, attack):
    root, snapshot, graph, receipt, _ = packet
    if attack == "half":
        graph.pop("derivation")
    elif attack == "swap":
        graph["source_sha256"] = graph["upstream_source"]["sha256"]
    elif attack == "strategy":
        receipt["strategy"] = "simple"
    elif attack == "minimum":
        receipt["minimum_proven"] = True
    elif attack == "outside":
        graph["outside_paths_complete"] = True
    elif attack == "tool":
        body = json.loads((root / "tool-install-receipt.json").read_text())
        body["image_id"] = "sha256:wrong"
        graph["tool_installation"]["sha256"] = write(root / "tool-install-receipt.json", body)
    elif attack == "binary":
        (root / "tool-binary-sha256.txt").write_text("b" * 64 + " /usr/bin/osmium\n")
    elif attack == "package":
        (root / "tool-packages/TEST_ONLY-tool.deb").write_text("changed")
    elif attack == "missing_scope":
        (root / "restriction-scope.osm.pbf").unlink()
    elif attack == "scope_hash":
        (root / "restriction-scope.osm.pbf").write_text("changed")
    elif attack in ["way_report", "restriction_report"]:
        name = "way-refs-check.txt" if attack == "way_report" else "restriction-refs-check.txt"
        content = (root / name).read_text().replace("missing: 0", "missing: 1", 1)
        (root / name).write_text(content)
        receipt[
            "way_refs_report_sha256" if attack == "way_report" else "restriction_refs_report_sha256"
        ] = hashlib.sha256(content.encode()).hexdigest()
    elif attack == "fileinfo":
        body = json.loads((root / "extract-fileinfo.json").read_text())
        body["file"]["size"] = 101
        receipt["fileinfo_sha256"] = write(root / "extract-fileinfo.json", body)
    elif attack == "target_coordinate":
        point = snapshot.sample_points[0]
        location = point.location.model_copy(update={"lon": point.location.lon + 0.001})
        snapshot = snapshot.model_copy(
            update={
                "sample_points": [
                    point.model_copy(update={"location": location}),
                    *snapshot.sample_points[1:],
                ]
            }
        )
    elif attack == "source_context":
        receipt["endpoint_snapshot_id"] = "TEST_ONLY-other"
    elif attack == "bad_path":
        graph["derivation"]["path"] = "../outside.json"
    graph["derivation"] = (
        {**graph.get("derivation", {}), "sha256": write(root / "extract-receipt.json", receipt)}
        if attack != "half"
        else None
    )
    if attack == "half":
        graph.pop("derivation")
    with pytest.raises((ValueError, OSError, KeyError), match="DERIVED_|RUNTIME_|No such file"):
        verify(packet, snapshot=snapshot, **({"budget": [1]} if attack == "budget" else {}))


def test_preserved_moscow_target4_is_a_subset_of_original_target3_scope():
    configured = os.environ.get("A2_TEST_MOSCOW_DERIVED_GRAPH_PATH")
    root = Path(configured or "/Users/kirito/waydex-full-runtime/moscow-graph-20261110-v1")
    if not root.is_dir():
        if configured:
            pytest.fail("Configured reviewed Moscow graph packet is missing")
        pytest.skip(
            "Restore reviewed graph packet or set A2_TEST_MOSCOW_DERIVED_GRAPH_PATH; no network fallback"
        )
    snapshot = load_accepted_data("moscow-urban-2026-11-10.4")
    graph = json.loads((root / "valhalla-build-manifest.json").read_text())
    result = verify_derived_graph(
        graph,
        root,
        snapshot=snapshot,
        city_id="moscow",
        source_id="osm_geofabrik",
        dataset_id="osm_geofabrik.central-fed-district-261107",
        original_sha256="d28d118c9269138ae33ea6561d4e29e492bd9954e6dd088f80b7a3b1372b77f2",
        original_bytes=884829631,
        budget=[16 * 1024 * 1024],
    )
    assert snapshot.snapshot_id.endswith(".4") and result.source_snapshot_id.endswith(".3")


def test_document_limit_and_96_mib_cumulative_limit_remain_bounded(tmp_path):
    from a2_api.adapters.product_runtime import read_document

    path = tmp_path / "TEST_ONLY-padding.json"
    raw = b"0" + b" " * (32 * 1024 * 1024 - 1)
    path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    remaining = [96 * 1024 * 1024]
    for _ in range(3):
        assert read_document(path, digest, remaining) == 0
    assert remaining == [0]
    with pytest.raises(ValueError, match="CUMULATIVE_BYTE_BUDGET_EXCEEDED"):
        read_document(path, digest, remaining)
    path.write_bytes(raw + b" ")
    with pytest.raises(ValueError, match="HASH_OR_SIZE_MISMATCH"):
        read_document(path, hashlib.sha256(raw + b" ").hexdigest(), [96 * 1024 * 1024])
