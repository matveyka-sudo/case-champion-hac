"""Operator-pinned graph sources for the shared pedestrian runtime."""

import hashlib
import importlib.util
import json
import math
import re
from pathlib import Path, PurePosixPath

spec = importlib.util.spec_from_file_location("graph_bundle", Path(__file__).with_name("bundle.py"))
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)

MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
MAX_SOURCE_BYTES = 1024 * 1024 * 1024
MAX_GRAPH_BYTES = 5 * 1024 * 1024 * 1024
MAX_TILES = 20000
OSMIUM_BINARY_SHA256 = "4740189c5cf26b399f5bc707fdea0df75cd9b7cd60dc3f9918b0a794c8bd3350"


def document(root, reference, limit=bundle.MAX_METADATA_BYTES):
    path = bundle.checked_reference(root, reference, limit)
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Graph metadata exceeds its byte budget")
    return path, json.loads(raw)


def report(graph, name, expected):
    path = bundle.checked_reference(graph, {"path": name, "sha256": expected})
    return path.read_text()


def zero_missing(text, pairs):
    for entity, owner in pairs:
        found = re.findall(rf"^{entity}\s+in\s+{owner}\s+missing:\s+(\d+)\s*$", text, re.MULTILINE)
        if found != ["0"]:
            raise ValueError("Derived source has unverified or missing references")


def derived_source(graph, manifest, source, snapshot, city, image):
    _, receipt = document(graph, manifest["derivation"])
    _, installed = document(graph, manifest["tool_installation"])
    upstream = manifest["upstream_source"]
    checksum = source["checksum"]
    if (
        set(upstream) != {"path", "sha256", "bytes"}
        or upstream["path"] != PurePosixPath(source["raw_path"]).name
        or upstream["sha256"] != checksum["value"]
        or upstream["bytes"] != checksum["size_bytes"]
        or receipt["schema"] != "waydex-source-bound-osm-extract/1"
        or receipt["upstream_source_filename"] != upstream["path"]
        or receipt["upstream_source_sha256"] != upstream["sha256"]
        or receipt["upstream_source_bytes"] != upstream["bytes"]
        or receipt["source_manifest_sha256"] != manifest["source_manifest_sha256"]
        or receipt["derived_input_filename"] != manifest["source_filename"]
        or receipt["derived_input_sha256"] != manifest["source_sha256"]
        or receipt["derived_input_bytes"] != manifest["source_bytes"]
        or receipt["exit_code"] != 0
        or receipt["container_exit_code"] != 0
        or receipt["oom_killed"] is not False
        or receipt["limit_reason"] is not None
        or receipt["way_references_verified"] is not True
        or receipt["restriction_member_references_verified"] is not True
        or receipt["outside_paths_complete"] is not False
        or manifest["outside_paths_complete"] is not False
        or receipt["minimum_proven"] is not False
    ):
        raise ValueError(
            "Original source and derived build input require a complete separate binding"
        )
    original = bundle.checked_reference(
        graph, {"path": upstream["path"], "sha256": upstream["sha256"]}, MAX_SOURCE_BYTES
    )
    if original.stat().st_size != upstream["bytes"]:
        raise ValueError("Original upstream PBF size differs")
    if (
        receipt["strategy"] != "smart"
        or receipt["strategy_options"] != ["types=multipolygon,restriction"]
        or receipt["tool_version"] != "1.16.0"
        or receipt["tool_binary_sha256"] != OSMIUM_BINARY_SHA256
        or installed["schema"] != "waydex-standard-tool-install/1"
        or installed["exit_code"] != 0
        or installed["oom_killed"] is not False
        or installed["limit_reason"] is not None
        or not installed["version"].startswith("osmium version 1.16.0\n")
        or receipt["tool_image_id"] != installed["image_id"]
        or re.fullmatch(r"sha256:[a-f0-9]{64}", installed["image_id"]) is None
        or image not in installed["command"]
    ):
        raise ValueError("Derived source standard tool identity or strategy differs")
    binary = bundle.local_packet_path(graph, "tool-binary-sha256.txt")
    if binary.stat().st_size > 128 or binary.read_text().split() != [
        OSMIUM_BINARY_SHA256,
        "/usr/bin/osmium",
    ]:
        raise ValueError("Derived source standard binary differs")
    packages = installed["packages"]
    if not isinstance(packages, list) or not 1 <= len(packages) <= 16:
        raise ValueError("Standard tool packages must be inventoried")
    package_bytes = 0
    tool_package = False
    for package in packages:
        package_bytes += package["bytes"]
        if package_bytes > 64 * 1024 * 1024:
            raise ValueError("Standard tool packages exceed byte budget")
        path = bundle.checked_reference(
            graph,
            {"path": "tool-packages/" + package["path"], "sha256": package["sha256"]},
            64 * 1024 * 1024,
        )
        if path.stat().st_size != package["bytes"]:
            raise ValueError("Standard tool package size differs")
        tool_package |= package["path"].startswith("osmium-tool_1.16.0-1build1_")
    if not tool_package:
        raise ValueError("Pinned standard tool package is missing")
    zero_missing(
        report(graph, "way-refs-check.txt", receipt["way_refs_report_sha256"]), [("Nodes", "ways")]
    )
    zero_missing(
        report(graph, "restriction-refs-check.txt", receipt["restriction_refs_report_sha256"]),
        [
            ("Nodes", "ways"),
            ("Nodes", "relations"),
            ("Ways", "relations"),
            ("Relations", "relations"),
        ],
    )
    bundle.checked_reference(
        graph,
        {"path": "restriction-scope.osm.pbf", "sha256": receipt["restriction_scope_sha256"]},
        MAX_SOURCE_BYTES,
    )
    _, fileinfo = document(
        graph, {"path": "extract-fileinfo.json", "sha256": receipt["fileinfo_sha256"]}
    )
    _, inventory = document(
        graph, {"path": "endpoint-inventory.json", "sha256": receipt["endpoint_inventory_sha256"]}
    )
    bbox = receipt["bbox"]
    if (
        not isinstance(bbox, list)
        or len(bbox) != 4
        or any(type(v) not in (int, float) or not math.isfinite(v) for v in bbox)
        or not -180 <= bbox[0] < bbox[2] <= 180
        or not -90 <= bbox[1] < bbox[3] <= 90
        or fileinfo["header"]["boxes"] != [bbox]
        or fileinfo["data"]["objects_ordered"] is not True
        or fileinfo["file"]["size"] != manifest["source_bytes"]
        or inventory["bbox"] != bbox
        or inventory["outside_count"] != 0
        or inventory["snapshot_id"] != receipt["endpoint_snapshot_id"]
    ):
        raise ValueError("Derived source spatial coverage or endpoint inventory differs")
    indexed = {(r["kind"], r["id"]): (r["lon"], r["lat"]) for r in inventory["endpoints"]}
    if len(indexed) != len(inventory["endpoints"]) or any(
        kind not in {"point", "destination", "access"}
        or not bbox[0] <= lon <= bbox[2]
        or not bbox[1] <= lat <= bbox[3]
        for (kind, _), (lon, lat) in indexed.items()
    ):
        raise ValueError("Duplicate derived source endpoint identity")
    points = [r for r in snapshot["sample_points"] if r["territory_id"] == city]
    destinations = [r for r in snapshot["destinations"] if r["territory_id"] == city]
    access = {r["access_node_id"] for r in points + destinations}
    nodes = [r for r in snapshot["access_nodes"] if r["node_id"] in access]
    if {r["node_id"] for r in nodes} != access:
        raise ValueError("Canonical passenger access node is missing")
    groups = [
        ("point", points, "point_id"),
        ("destination", destinations, "destination_id"),
        ("access", nodes, "node_id"),
    ]
    for kind, rows, key in groups:
        for row in rows:
            lon, lat = row["location"]["lon"], row["location"]["lat"]
            if (
                not bbox[0] <= lon <= bbox[2]
                or not bbox[1] <= lat <= bbox[3]
                or indexed.get((kind, row[key])) != (lon, lat)
            ):
                raise ValueError(
                    "Current canonical endpoint is outside the reviewed extract coverage"
                )
    return original.stat().st_size


def verify(graph, parent, binding_path, binding_sha, image, generator_sha, directory_sources):
    """Verify a trusted operator binding; never consume request URLs or hashes."""
    graph = Path(graph).resolve(strict=True)
    binding_path = Path(binding_path)
    root = binding_path.parent.resolve(strict=True)
    _, binding = document(root, {"path": binding_path.name, "sha256": binding_sha})
    if (
        set(binding)
        != {"schema_version", "city_id", "snapshot", "source_manifest", "graph_manifest"}
        or binding["schema_version"] != "waydex-graph-source-binding/1"
    ):
        raise ValueError("Unsupported operator graph source binding")
    reference = binding["snapshot"]
    if set(reference) != {"path", "sha256", "snapshot_id", "content_hash"}:
        raise ValueError("Pinned canonical snapshot reference is required")
    _, snapshot = document(root, {k: reference[k] for k in ("path", "sha256")}, MAX_SNAPSHOT_BYTES)
    canonical = hashlib.sha256(
        json.dumps(
            snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()
    build = parent["build"]
    city = binding["city_id"]
    source_graph = snapshot["provenance"]["graph_version"]
    if (
        snapshot["schema_version"] != "2"
        or snapshot["qa"]["acceptance_state"] != "accepted"
        or snapshot["snapshot_id"] != reference["snapshot_id"]
        or canonical != reference["content_hash"]
        or build["snapshot_id"] != reference["snapshot_id"]
        or parent.get("bundle_id", reference["snapshot_id"]) != reference["snapshot_id"]
        or city not in snapshot["geographic_scope"]
        or not any(
            t["territory_id"] == city and t["level"] == "city" for t in snapshot["territories"]
        )
        or build["source_graph_version"] != source_graph
    ):
        raise ValueError("Operator graph city or canonical snapshot differs from the artifact")
    _, source = document(root, binding["source_manifest"])
    _, manifest = document(root, binding["graph_manifest"])
    # /runtime/graph and /data may be separate mounts of the same restored bytes.
    bundle.checked_reference(
        graph,
        {"path": "valhalla-build-manifest.json", "sha256": binding["graph_manifest"]["sha256"]},
    )
    bundle.checked_reference(
        graph, {"path": "source-manifest.json", "sha256": binding["source_manifest"]["sha256"]}
    )
    if (
        source.get("status") != "accepted"
        or snapshot["source_versions"].get(source["source_id"]) != source["dataset_id"]
        or not source_graph.startswith(source["dataset_id"] + ":")
        or manifest["source_graph_version"] != source_graph
        or manifest["source_manifest_sha256"] != binding["source_manifest"]["sha256"]
        or build["graph_version"] != "valhalla-351-tiles-" + binding["graph_manifest"]["sha256"]
        or manifest["image_id"] != image.split("@", 1)[1]
        or image not in manifest["image_repo_digests"]
    ):
        raise ValueError("Operator graph source version, image or build binding differs")
    checksum = source["checksum"]
    name = manifest["source_filename"]
    if (
        checksum["algorithm"] != "sha256"
        or type(checksum["size_bytes"]) is not int
        or not 0 < checksum["size_bytes"] <= MAX_SOURCE_BYTES
        or len(bundle.safe_name(name).parts) != 1
        or not name.endswith(".osm.pbf")
    ):
        raise ValueError("Pinned PBF source size, name or checksum differs")
    original_bytes = 0
    if "derivation" in manifest:
        original_bytes = derived_source(graph, manifest, source, snapshot, city, image)
    elif (
        "upstream_source" in manifest
        or "tool_installation" in manifest
        or manifest["source_bytes"] != checksum["size_bytes"]
        or manifest["source_sha256"] != checksum["value"]
        or name != PurePosixPath(source["raw_path"]).name
    ):
        raise ValueError("Original source or complete explicit derivation is required")
    pbf = bundle.checked_reference(
        graph, {"path": name, "sha256": manifest["source_sha256"]}, MAX_SOURCE_BYTES
    )
    if pbf.stat().st_size != manifest["source_bytes"]:
        raise ValueError("Pinned PBF size differs")
    _, configuration = document(
        graph, {"path": "valhalla.json", "sha256": manifest["config_sha256"]}
    )
    directory_sources(graph, configuration)
    _, profile = document(
        graph, {"path": "walking-profile.json", "sha256": manifest["profile_sha256"]}
    )
    if (
        profile["engine_version"] != "3.5.1"
        or build["engine_version"] != profile["engine_version"]
        or build["profile_version"] != profile["profile_version"]
        or json.loads(build["pedestrian_options_json"]) != profile["pedestrian_options"]
    ):
        raise ValueError("Mounted pedestrian profile differs from the artifact")
    bundle.checked_reference(
        graph, {"path": "valhalla-build-config-3.5.1.py", "sha256": generator_sha}
    )
    version = bundle.local_packet_path(graph, "valhalla-version.txt")
    if version.stat().st_size > 128 or version.read_text().strip() != "valhalla_build_tiles 3.5.1":
        raise ValueError("Mounted graph binary version differs")
    rows = manifest["files"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_TILES:
        raise ValueError("Bounded graph tile inventory required")
    declared = set()
    total = original_bytes + pbf.stat().st_size
    for row in rows:
        if (
            not isinstance(row, dict)
            or set(row) != {"path", "sha256", "bytes"}
            or not row["path"].startswith("tiles/")
            or row["path"] in declared
            or type(row["bytes"]) is not int
            or row["bytes"] <= 0
        ):
            raise ValueError("Invalid or duplicate graph tile record")
        tile = bundle.checked_reference(
            graph, {"path": row["path"], "sha256": row["sha256"]}, MAX_GRAPH_BYTES - total
        )
        if tile.stat().st_size != row["bytes"]:
            raise ValueError("Graph tile size differs")
        total += row["bytes"]
        declared.add(row["path"])
    actual = set()
    for path in (graph / "tiles").rglob("*"):
        if path.is_symlink():
            raise ValueError("Graph tiles cannot contain links")
        if path.is_file():
            actual.add(path.relative_to(graph).as_posix())
    if actual != declared:
        raise ValueError("Graph tile inventory differs from the mounted directory")
    return binding["graph_manifest"]["sha256"]
