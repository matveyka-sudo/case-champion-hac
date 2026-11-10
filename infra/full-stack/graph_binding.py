"""Operator-pinned graph sources for the shared pedestrian runtime."""

import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath

spec = importlib.util.spec_from_file_location("graph_bundle", Path(__file__).with_name("bundle.py"))
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)

MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
MAX_SOURCE_BYTES = 1024 * 1024 * 1024
MAX_GRAPH_BYTES = 5 * 1024 * 1024 * 1024
MAX_TILES = 20000


def document(root, reference, limit=bundle.MAX_METADATA_BYTES):
    path = bundle.checked_reference(root, reference, limit)
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Graph metadata exceeds its byte budget")
    return path, json.loads(raw)


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
        or manifest["source_bytes"] != checksum["size_bytes"]
        or manifest["source_sha256"] != checksum["value"]
        or name != PurePosixPath(source["raw_path"]).name
        or len(bundle.safe_name(name).parts) != 1
        or not name.endswith(".osm.pbf")
    ):
        raise ValueError("Pinned PBF source size, name or checksum differs")
    pbf = bundle.checked_reference(
        graph, {"path": name, "sha256": checksum["value"]}, MAX_SOURCE_BYTES
    )
    if pbf.stat().st_size != checksum["size_bytes"]:
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
    total = pbf.stat().st_size
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
