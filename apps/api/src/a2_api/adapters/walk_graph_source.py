"""Bounded verification of reviewed OSM extraction provenance, never a minimum proof."""

import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from a2_contracts.v2.dataset import AcceptedData
from a2_data_pipeline.manifest import dataset_manifest_path
from a2_data_pipeline.paths import bundles_dir

from a2_api.adapters.product_runtime import FileRef, read_document

MARKERS = frozenset({"upstream_source", "derivation", "tool_installation"})


@dataclass(frozen=True)
class DerivedGraph:
    source_snapshot_id: str
    receipt_sha256: str
    limitations: tuple[str, ...] = ("DERIVED_GRAPH_OUTSIDE_PATHS_INCOMPLETE",)


def _path(root: Path, name: str) -> Path:
    path = (root / name).resolve()
    if Path(name).is_absolute() or not path.is_relative_to(root.resolve()):
        raise ValueError("DERIVED_GRAPH_REFERENCE_OUTSIDE_ROOT")
    return path


def _document(root: Path, reference: Any, budget: list[int], limit: int = 1024 * 1024) -> Any:
    ref = FileRef.model_validate(reference)
    path = _path(root, ref.path)
    if path.stat().st_size > limit:
        raise ValueError("DERIVED_GRAPH_DOCUMENT_BYTE_BUDGET_EXCEEDED")
    return read_document(path, ref.sha256, budget)


def _bytes(root: Path, name: str, expected: str | None, budget: list[int], limit: int) -> bytes:
    path = _path(root, name)
    if path.stat().st_size > limit:
        raise ValueError("DERIVED_GRAPH_FILE_BYTE_BUDGET_EXCEEDED")
    digest = hashlib.sha256()
    chunks = []
    seen = 0
    with path.open("rb") as stream:
        while chunk := stream.read(min(1024 * 1024, budget[0] + 1)):
            budget[0] -= len(chunk)
            seen += len(chunk)
            if budget[0] < 0 or seen > limit:
                raise ValueError("DERIVED_GRAPH_CUMULATIVE_BYTE_BUDGET_EXCEEDED")
            digest.update(chunk)
            if limit <= 128 * 1024:
                chunks.append(chunk)
    if expected is not None and digest.hexdigest() != expected:
        raise ValueError("DERIVED_GRAPH_SOURCE_FILE_HASH_CHANGED")
    return b"".join(chunks)


def _zero_report(content: bytes, expected: set[str]) -> tuple[int, int, int]:
    text = content.decode()
    header = re.match(
        r"There are (\d+) nodes, (\d+) ways, and (\d+) relations in this file\.\n", text
    )
    rows = re.findall(r"(?m)^(.+?)missing:\s*(\d+)\s*$", text)
    if (
        header is None
        or len(rows) != len(expected)
        or {"".join(name.split()): int(value) for name, value in rows}
        != {name: 0 for name in expected}
    ):
        raise ValueError("DERIVED_GRAPH_REFERENCE_CLOSURE_REPORT_INVALID")
    return int(header[1]), int(header[2]), int(header[3])


def verify_derived_graph(
    graph: dict[str, Any],
    root: Path,
    *,
    snapshot: AcceptedData,
    city_id: str,
    source_id: str,
    dataset_id: str,
    original_sha256: str,
    original_bytes: int,
    budget: list[int],
) -> DerivedGraph:
    if MARKERS & graph.keys() != MARKERS:
        raise ValueError("DERIVED_GRAPH_COMPLETE_PROVENANCE_REQUIRED")
    upstream = graph["upstream_source"]
    if not isinstance(upstream, dict) or set(upstream) != {"path", "sha256", "bytes"}:
        raise ValueError("DERIVED_GRAPH_UPSTREAM_BINDING_REQUIRED")
    source_hash = graph["source_manifest_sha256"]
    source_path = dataset_manifest_path(dataset_id)
    source = _document(
        source_path.parent, {"path": source_path.name, "sha256": source_hash}, budget
    )
    copied = _document(root, {"path": "source-manifest.json", "sha256": source_hash}, budget)
    receipt = _document(root, graph["derivation"], budget)
    if (
        source != copied
        or source["status"] != "accepted"
        or source["source_id"] != source_id
        or source["dataset_id"] != dataset_id
        or city_id not in snapshot.geographic_scope
        or snapshot.source_versions.get(source_id) != dataset_id
        or not snapshot.provenance.graph_version.startswith(dataset_id + ":")
        or graph["source_graph_version"] != snapshot.provenance.graph_version
        or source["checksum"]["algorithm"] != "sha256"
        or source["checksum"]["value"] != original_sha256
        or source["checksum"]["size_bytes"] != original_bytes
        or upstream
        != {
            "path": PurePosixPath(source["raw_path"]).name,
            "sha256": original_sha256,
            "bytes": original_bytes,
        }
        or receipt["schema"] != "waydex-source-bound-osm-extract/1"
        or receipt["source_manifest_sha256"] != source_hash
        or receipt["upstream_source_sha256"] != original_sha256
        or receipt["upstream_source_bytes"] != original_bytes
        or receipt["upstream_source_filename"] != upstream["path"]
        or receipt["derived_input_sha256"] != graph["source_sha256"]
        or receipt["derived_input_bytes"] != graph["source_bytes"]
        or receipt["derived_input_filename"] != graph["source_filename"]
        or graph["source_sha256"] == original_sha256
        or type(graph["source_bytes"]) is not int
        or not 0 < graph["source_bytes"] < original_bytes
        or Path(graph["source_filename"]).name != graph["source_filename"]
        or not graph["source_filename"].endswith(".osm.pbf")
        or receipt["strategy"] != "smart"
        or receipt["strategy_options"] != ["types=multipolygon,restriction"]
        or receipt["tool_version"] != "1.16.0"
        or receipt["exit_code"] != 0
        or type(receipt["exit_code"]) is not int
        or receipt["container_exit_code"] != 0
        or type(receipt["container_exit_code"]) is not int
        or receipt["oom_killed"] is not False
        or receipt["limit_reason"] is not None
        or receipt["outside_paths_complete"] is not False
        or receipt["minimum_proven"] is not False
        or graph["outside_paths_complete"] is not False
        or graph.get("minimum_proven", False) is not False
        or receipt["way_references_verified"] is not True
        or receipt["restriction_member_references_verified"] is not True
    ):
        raise ValueError("DERIVED_GRAPH_SOURCE_OR_STRATEGY_BINDING_CHANGED")
    tool = _document(root, graph["tool_installation"], budget)
    binary = _bytes(root, "tool-binary-sha256.txt", None, budget, 128).decode().split()
    if (
        tool["schema"] != "waydex-standard-tool-install/1"
        or tool["exit_code"] != 0
        or type(tool["exit_code"]) is not int
        or tool["oom_killed"] is not False
        or tool["limit_reason"] is not None
        or tool["image_id"] != receipt["tool_image_id"]
        or not any(
            value in graph["image_repo_digests"]
            for value in tool["command"]
            if isinstance(value, str)
        )
        or not tool["version"].startswith("osmium version " + receipt["tool_version"] + "\n")
        or binary != [receipt["tool_binary_sha256"], "/usr/bin/osmium"]
        or len(tool["packages"]) != 2
    ):
        raise ValueError("DERIVED_GRAPH_TOOL_BINDING_CHANGED")
    packages = set()
    for row in tool["packages"]:
        if set(row) != {"path", "sha256", "bytes"} or Path(row["path"]).name != row["path"]:
            raise ValueError("DERIVED_GRAPH_TOOL_PACKAGE_BINDING_CHANGED")
        file = _path(root, "tool-packages/" + row["path"])
        if (
            row["path"] in packages
            or type(row["bytes"]) is not int
            or file.stat().st_size != row["bytes"]
        ):
            raise ValueError("DERIVED_GRAPH_TOOL_PACKAGE_BINDING_CHANGED")
        packages.add(row["path"])
        _bytes(root, "tool-packages/" + row["path"], row["sha256"], budget, 8 * 1024 * 1024)
    _bytes(
        root,
        "restriction-scope.osm.pbf",
        receipt["restriction_scope_sha256"],
        budget,
        8 * 1024 * 1024,
    )
    way_counts = _zero_report(
        _bytes(root, "way-refs-check.txt", receipt["way_refs_report_sha256"], budget, 128 * 1024),
        {"Nodesinways"},
    )
    _zero_report(
        _bytes(
            root,
            "restriction-refs-check.txt",
            receipt["restriction_refs_report_sha256"],
            budget,
            128 * 1024,
        ),
        {"Nodesinways", "Nodesinrelations", "Waysinrelations", "Relationsinrelations"},
    )
    info = _document(
        root, {"path": "extract-fileinfo.json", "sha256": receipt["fileinfo_sha256"]}, budget
    )
    inventory = _document(
        root,
        {"path": "endpoint-inventory.json", "sha256": receipt["endpoint_inventory_sha256"]},
        budget,
        8 * 1024 * 1024,
    )
    bbox = receipt["bbox"]
    if (
        not isinstance(bbox, list)
        or len(bbox) != 4
        or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
            for v in bbox
        )
        or not -180 <= bbox[0] < bbox[2] <= 180
        or not -90 <= bbox[1] < bbox[3] <= 90
        or info["file"]["size"] != graph["source_bytes"]
        or PurePosixPath(info["file"]["name"]).name != graph["source_filename"]
        or info["header"]["boxes"] != [bbox]
        or info["header"]["option"]["generator"] != "osmium/" + receipt["tool_version"]
        or info["data"]["objects_ordered"] is not True
        or info["data"]["multiple_versions"] is not False
        or way_counts
        != tuple(info["data"]["count"][kind] for kind in ("nodes", "ways", "relations"))
        or inventory["bbox"] != bbox
        or inventory["outside_count"] != 0
        or inventory["snapshot_id"] != receipt["endpoint_snapshot_id"]
        or receipt["endpoint_counts"]["outside_count"] != 0
    ):
        raise ValueError("DERIVED_GRAPH_SCOPE_OR_FILEINFO_CHANGED")
    # Preserve the original extraction context; a later target is a checked subset.
    old_manifest = bundles_dir() / inventory["snapshot_id"] / "manifest.json"
    if not old_manifest.resolve().is_relative_to(bundles_dir().resolve()):
        raise ValueError("DERIVED_GRAPH_SOURCE_SNAPSHOT_OUTSIDE_REGISTRY")
    original_context = _document(
        old_manifest.parent,
        {"path": old_manifest.name, "sha256": inventory["bundle_manifest_sha256"]},
        budget,
    )
    if (
        original_context["bundle_id"] != inventory["snapshot_id"]
        or original_context["graph_version"] != snapshot.provenance.graph_version
    ):
        raise ValueError("DERIVED_GRAPH_ORIGINAL_SNAPSHOT_CONTEXT_CHANGED")
    rows = inventory["endpoints"]
    if not isinstance(rows, list) or len(rows) > 32768:
        raise ValueError("DERIVED_GRAPH_ENDPOINT_INVENTORY_REQUIRED")
    endpoints = {}
    for row in rows:
        key = (row["kind"], row["id"])
        lon, lat = row["lon"], row["lat"]
        if (
            key in endpoints
            or row["kind"] not in {"point", "destination", "access"}
            or not bbox[0] <= lon <= bbox[2]
            or not bbox[1] <= lat <= bbox[3]
        ):
            raise ValueError("DERIVED_GRAPH_ENDPOINT_INVENTORY_CHANGED")
        endpoints[key] = (lon, lat)
    points = [p for p in snapshot.sample_points if p.territory_id == city_id]
    destinations = [d for d in snapshot.destinations if d.territory_id == city_id]
    access_ids = {row.access_node_id for row in points} | {
        row.access_node_id for row in destinations
    }
    targets = [("point", p.point_id, p.location) for p in points]
    targets.extend(("destination", d.destination_id, d.location) for d in destinations)
    targets.extend(
        ("access", n.node_id, n.location) for n in snapshot.access_nodes if n.node_id in access_ids
    )
    if any(
        endpoints.get((kind, identity)) != (location.lon, location.lat)
        for kind, identity, location in targets
    ):
        raise ValueError("DERIVED_GRAPH_TARGET_ENDPOINT_OUTSIDE_PRESERVED_SCOPE")
    return DerivedGraph(
        inventory["snapshot_id"], FileRef.model_validate(graph["derivation"]).sha256
    )
