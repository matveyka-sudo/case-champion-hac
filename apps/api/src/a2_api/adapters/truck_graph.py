"""Check mounted truck graph bytes against the operator-pinned build inventory."""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath

from a2_api.core.truck_routing import TruckRoutingBinding

MAX_GRAPH_BYTES = 2 * 1024 * 1024 * 1024


def verify_graph(binding: TruckRoutingBinding) -> None:
    configured = os.environ.get(binding.graph_directory_env)
    if not configured:
        raise ValueError("TRUCK_GRAPH_DIRECTORY_REQUIRED")
    root = Path(configured).resolve(strict=True)

    def checked(name: str, digest: str, limit: int) -> bytes:
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
            raise ValueError("TRUCK_GRAPH_PATH_INVALID")
        if path.stat().st_size > limit:
            raise ValueError("TRUCK_GRAPH_BYTE_LIMIT")
        result = hashlib.sha256()
        small = bytearray()
        total = 0
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                total += len(chunk)
                if total > limit:
                    raise ValueError("TRUCK_GRAPH_BYTE_LIMIT")
                result.update(chunk)
                if limit <= 1024 * 1024:
                    small.extend(chunk)
        if result.hexdigest() != digest:
            raise ValueError("TRUCK_GRAPH_CONTENT_CHANGED")
        return bytes(small)

    manifest = json.loads(
        checked("valhalla-build-manifest.json", binding.graph_manifest_sha256, 1024 * 1024)
    )
    config = json.loads(checked("valhalla.json", manifest["config_sha256"], 1024 * 1024))
    reader = config.get("mjolnir", {})
    if (
        not isinstance(reader, dict)
        or reader.get("tile_dir") != "/data/tiles"
        or reader.get("tile_url") not in (None, "")
    ):
        raise ValueError("TRUCK_GRAPH_DIRECTORY_SOURCE_REQUIRED")
    extract = reader.get("tile_extract")
    if extract not in (None, ""):
        if not isinstance(extract, str):
            raise ValueError("TRUCK_GRAPH_ARCHIVE_PATH_INVALID")
        relative = PurePosixPath(extract).relative_to("/data")
        if ".." in relative.parts:
            raise ValueError("TRUCK_GRAPH_ARCHIVE_PATH_INVALID")
        archive = root.joinpath(*relative.parts)
        if archive.exists() or archive.is_symlink():
            raise ValueError("TRUCK_GRAPH_UNVERIFIED_ARCHIVE_OVERRIDE")
    rows = manifest["files"]
    if not isinstance(rows, list) or not 0 < len(rows) <= 10000:
        raise ValueError("TRUCK_GRAPH_INVENTORY_REQUIRED")
    total = 0
    declared = set()
    for row in rows:
        if (
            not isinstance(row, dict)
            or set(row) != {"path", "sha256", "bytes"}
            or not isinstance(row["path"], str)
            or not row["path"].startswith("tiles/")
            or row["path"] in declared
            or type(row["bytes"]) is not int
            or row["bytes"] <= 0
        ):
            raise ValueError("TRUCK_GRAPH_INVENTORY_INVALID")
        total += row["bytes"]
        if total > MAX_GRAPH_BYTES or (root / row["path"]).stat().st_size != row["bytes"]:
            raise ValueError("TRUCK_GRAPH_SIZE_MISMATCH")
        checked(row["path"], row["sha256"], row["bytes"])
        declared.add(row["path"])
    actual = set()
    for path in (root / "tiles").rglob("*"):
        if path.is_symlink():
            raise ValueError("TRUCK_GRAPH_LINK_FORBIDDEN")
        if path.is_file():
            actual.add(path.relative_to(root).as_posix())
    if actual != declared:
        raise ValueError("TRUCK_GRAPH_INVENTORY_CHANGED")
    if int((root / "tiles").stat().st_mtime) != binding.tileset_last_modified:
        raise ValueError("TRUCK_GRAPH_STATUS_IDENTITY_CHANGED")
