#!/usr/bin/env python3
"""Verify or exclusively copy the reviewed four-profile graph transport, offline."""

import argparse
import hashlib
import importlib.util
import io
import json
import os
import re
import stat
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
PRODUCT_SHA = "a68f69593e1aaea8917030e2b2f57433dc41d2ab5569bc3f15bcecfddf813dc6"
BUNDLE_SHA = "bd779178337c0975d63f3c84dd5e426c354272afb98dac20d9b499bac1c06da2"
MARKER = "graph-transfer-manifest.json"
# These inventories describe the reviewed bytes, not a newly generated graph.
PINS = {
    "graphs/moscow": (
        76,
        1334725287,
        "1a28176f74f8253d834a4c5be57080bb243d71728a1c59b442d719ff3da2c1ec",
    ),
    "graphs/novorossiysk": (
        1143,
        644517007,
        "cd1a259aa5e0de518615006a0f11eab3c9737f32cfc1f7c777015225372405fa",
    ),
    "graphs/kaliningrad": (
        128,
        55236863,
        "55ef11572fb49a3bd0c71d23372f6f89319bf3512092c289a24c3ebbe9716b06",
    ),
    "bindings/moscow": (
        6,
        22884180,
        "03cfe5cd805b080182c7c1b8e25ddc3c9d31021bebf8dcbc38c23d8080bff2c2",
    ),
    "bindings/novorossiysk": (
        6,
        1795996,
        "e6f55413401d9d6dd690bb7aa54e12dfbf17fc78f69844597809a97b72a4a4cc",
    ),
}
BINDINGS = {
    "moscow": "6b7fdc65bf19ab2ee11bec053b5020c32191f7f5307cb95d0224e894a3275825",
    "novorossiysk": "a32e078228072de85a9dfa83e05cf2ca7d79c2901c5cadb72bd3b9139f337070",
}
ARGUMENTS = {
    "graphs/moscow": "moscow_graph",
    "graphs/novorossiysk": "novorossiysk_graph",
    "graphs/kaliningrad": "kaliningrad_graph",
    "bindings/moscow": "moscow_binding",
    "bindings/novorossiysk": "novorossiysk_binding",
}


def encoded(value):
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode()


def checked_path(path):
    path = Path(path).absolute()
    if ".." in path.parts or any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("Graph transport paths cannot contain links")
    return path


def read_file(path, destination=None, limit=1024**3):
    """Stream a regular file without following its final path component."""
    path = checked_path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ValueError("Graph transport requires a bounded regular file")
        digest = hashlib.sha256()
        count = 0
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            count += len(chunk)
            if count > before.st_size:
                raise ValueError("Graph transport source changed while reading")
            digest.update(chunk)
            if destination is not None:
                destination.write(chunk)
        after = os.fstat(source.fileno())
        if count != before.st_size or (before.st_size, before.st_mtime_ns) != (
            after.st_size,
            after.st_mtime_ns,
        ):
            raise ValueError("Graph transport source changed while reading")
    return {
        "bytes": count,
        "sha256": digest.hexdigest(),
        "executable": bool(before.st_mode & 0o111),
    }


def inventory(folder):
    folder = checked_path(folder)
    if not folder.is_dir():
        raise ValueError("Graph transport root must be a directory")
    rows = []
    total = 0
    for path in sorted(folder.rglob("*")):
        checked_path(path)
        if path.is_dir():
            continue
        relative = path.relative_to(folder).as_posix()
        if any(
            p.startswith(".") or p in {"observations", "node_modules"} for p in Path(relative).parts
        ):
            raise ValueError("Private paths cannot enter graph transport")
        row = dict(path=relative, **read_file(path))
        rows.append(row)
        total += row["bytes"]
        if len(rows) > 20000 or total > 5 * 1024**3:
            raise ValueError("Graph transport inventory exceeds existing graph bounds")
    return rows


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_release(path, expected_sha256):
    """Explicit reviewed pins for a later release; legacy defaults stay frozen."""
    content = io.BytesIO()
    actual = read_file(path, content, limit=128 * 1024)
    if actual["sha256"] != expected_sha256:
        raise ValueError("Release pin document SHA256 differs")
    release = json.loads(content.getvalue())
    if (
        not isinstance(release, dict)
        or set(release)
        != {
            "schema_version",
            "product_sha256",
            "bundle_sha256",
            "groups",
            "bindings",
            "router_tileset_metadata",
        }
        or release["schema_version"] != "waydex-runtime-transfer-pins/1"
        or set(release["groups"]) != set(ARGUMENTS)
        or set(release["bindings"]) != set(BINDINGS)
        or set(release["router_tileset_metadata"]) != {"moscow", "novorossiysk", "kaliningrad"}
    ):
        raise ValueError("Invalid release pin document scope")
    hashes = [release["product_sha256"], release["bundle_sha256"], *release["bindings"].values()]
    for pin in release["groups"].values():
        if (
            not isinstance(pin, list)
            or len(pin) != 3
            or type(pin[0]) is not int
            or not 0 < pin[0] <= 20000
            or type(pin[1]) is not int
            or not 0 < pin[1] <= 5 * 1024**3
        ):
            raise ValueError("Invalid release inventory bound")
        hashes.append(pin[2])
    if any(not isinstance(v, str) or not re.fullmatch("[a-f0-9]{64}", v) for v in hashes):
        raise ValueError("Release pins require SHA256 identities")
    if any(type(v) is not int or v < 0 for v in release["router_tileset_metadata"].values()):
        raise ValueError("Invalid release router metadata")
    return release


def verify_sources(runtime, sources, release=None):
    runtime = checked_path(runtime)
    bundle = load_module("four_profile_bundle", ROOT / "infra/full-stack/bundle.py")
    bundle.verify(runtime, release["bundle_sha256"] if release else BUNDLE_SHA)
    if read_file(runtime / "manifest.json")["sha256"] != (
        release["product_sha256"] if release else PRODUCT_SHA
    ):
        raise ValueError("Four-profile product manifest differs")
    pins = release["groups"] if release else PINS
    bindings = release["bindings"] if release else BINDINGS
    groups = {}
    for group, pin in pins.items():
        rows = inventory(sources[group])
        actual = (
            len(rows),
            sum(r["bytes"] for r in rows),
            hashlib.sha256(encoded(rows)).hexdigest(),
        )
        if actual != tuple(pin):
            raise ValueError(f"Reviewed graph transport inventory differs: {group}")
        groups[group] = rows
    checker = load_module("four_profile_runtime", ROOT / "infra/full-stack/runtime.py")
    graph_manifests = {}
    router_tileset_metadata = {}
    saved = {
        key: os.environ.get(key)
        for key in ("WAYDEX_GRAPH_BINDING_PATH", "WAYDEX_GRAPH_BINDING_SHA256")
    }
    try:
        for city in ("moscow", "novorossiysk", "kaliningrad"):
            if city == "kaliningrad":
                os.environ.pop("WAYDEX_GRAPH_BINDING_PATH", None)
                os.environ.pop("WAYDEX_GRAPH_BINDING_SHA256", None)
                parent = runtime / "kaliningrad/native/artifact.json"
            else:
                os.environ["WAYDEX_GRAPH_BINDING_PATH"] = str(
                    sources[f"bindings/{city}"] / "binding.json"
                )
                os.environ["WAYDEX_GRAPH_BINDING_SHA256"] = bindings[city]
                parent = (
                    runtime / "moscow/artifact.json"
                    if city == "moscow"
                    else sources["bindings/novorossiysk"] / "parent-artifact.json"
                )
            artifact = json.loads(parent.read_bytes())
            graph_manifests[city] = checker.graph_check(sources[f"graphs/{city}"], artifact)
            if city != "kaliningrad":
                # Valhalla 3.5.1 /status uses tile_dir's mtime, not tile bytes.
                # Capture the source value and require its existing bound status.
                # N's graph-check parent is build-only; the pinned runtime owns status.
                status_artifact = json.loads((runtime / city / "artifact.json").read_bytes())
                tiles = checked_path(sources[f"graphs/{city}"] / "tiles")
                seconds = tiles.stat().st_mtime_ns // 10**9
                if seconds != status_artifact["status_expectation"]["tileset_last_modified"]:
                    raise ValueError("Source tile directory differs from the bound build status")
                router_tileset_metadata[f"graphs/{city}"] = {
                    "path": "tiles",
                    "mtime_seconds": seconds,
                }
            if release is not None:
                seconds = (
                    checked_path(sources[f"graphs/{city}"] / "tiles").stat().st_mtime_ns // 10**9
                )
                product = json.loads((runtime / "manifest.json").read_bytes())
                expected = {
                    p["truck_routing"]["tileset_last_modified"]
                    for p in product.get("profiles", [])
                    if p.get("city_id") == city and p.get("truck_routing") is not None
                }
                if (
                    expected != {release["router_tileset_metadata"][city]}
                    or seconds not in expected
                ):
                    raise ValueError("Release router metadata differs from immutable runtime")
                router_tileset_metadata[f"graphs/{city}"] = {
                    "path": "tiles",
                    "mtime_seconds": seconds,
                }
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return {
        "schema_version": "waydex-four-profile-graph-transfer/2",
        "groups": groups,
        "graph_manifests": graph_manifests,
        "router_tileset_metadata": router_tileset_metadata,
    }


def copy_sources(sources, manifest, output):
    if manifest.get("schema_version") != "waydex-four-profile-graph-transfer/2":
        raise ValueError("Graph transfer requires the checked v2 metadata receipt")
    output = checked_path(output)
    roots = [checked_path(p) for p in sources.values()]
    if output.exists() or any(output.is_relative_to(p) for p in roots):
        raise ValueError("Graph transfer destination must be new and outside its sources")
    metadata = manifest["router_tileset_metadata"]
    for group, row in metadata.items():
        if (
            group not in manifest["groups"]
            or row["path"] != "tiles"
            or type(row["mtime_seconds"]) is not int
            or row["mtime_seconds"] < 0
            or not checked_path(sources[group] / "tiles").is_dir()
            or (sources[group] / "tiles").stat().st_mtime_ns // 10**9 != row["mtime_seconds"]
        ):
            raise ValueError("Router tile directory metadata changed before copying")
    output.mkdir(mode=0o700)
    for group, rows in manifest["groups"].items():
        for row in rows:
            target = output / group / row["path"]
            target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            with target.open("xb") as destination:
                actual = read_file(sources[group] / row["path"], destination)
            if actual != {k: row[k] for k in ("bytes", "sha256", "executable")}:
                raise ValueError("Graph transport source changed while copying")
            target.chmod(0o555 if row["executable"] else 0o444)
    # Creating descendants changes directory mtimes. Restore only the actor's
    # observable whole-second metadata after the entire tree has been copied.
    for group, row in metadata.items():
        source = checked_path(sources[group] / "tiles")
        if source.stat().st_mtime_ns // 10**9 != row["mtime_seconds"]:
            raise ValueError("Router tile directory metadata changed while copying")
        target = checked_path(output / group / "tiles")
        os.utime(target, ns=(target.stat().st_atime_ns, row["mtime_seconds"] * 10**9))
        if target.stat().st_mtime_ns // 10**9 != row["mtime_seconds"]:
            raise ValueError("Destination cannot preserve router tile directory metadata")
    # This marker is published last. Failed partial transfers cannot verify.
    with (output / MARKER).open("xb") as target:
        target.write(encoded(manifest))
    (output / MARKER).chmod(0o444)


def summary(manifest):
    return {
        "groups": {
            name: {
                "files": len(rows),
                "bytes": sum(r["bytes"] for r in rows),
                "inventory_sha256": hashlib.sha256(encoded(rows)).hexdigest(),
            }
            for name, rows in manifest["groups"].items()
        },
        "HTTP_calls": 0,
        "SQL_calls": 0,
        "graph_manifests": manifest["graph_manifests"],
        "router_tileset_metadata": manifest["router_tileset_metadata"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    export.add_argument("--runtime", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--dry-run", action="store_true")
    for name in ARGUMENTS.values():
        export.add_argument("--" + name.replace("_", "-"), type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--runtime", type=Path, required=True)
    verify.add_argument("--transfer", type=Path, required=True)
    for command in (export, verify):
        command.add_argument("--release-pins", type=Path)
        command.add_argument("--release-pins-sha256")
    args = parser.parse_args()
    if bool(args.release_pins) != bool(args.release_pins_sha256):
        raise ValueError("Both release pin document and SHA256 are required")
    release = (
        load_release(args.release_pins, args.release_pins_sha256) if args.release_pins else None
    )
    if args.command == "export":
        sources = {group: checked_path(getattr(args, name)) for group, name in ARGUMENTS.items()}
        output = checked_path(args.output)
        roots = [checked_path(args.runtime), *sources.values()]
        if output.exists() or any(output.is_relative_to(p) for p in roots):
            raise ValueError("Graph transfer destination must be new and outside runtime")
        manifest = verify_sources(args.runtime, sources, release)
        if not args.dry_run:
            copy_sources(sources, manifest, output)
        print(json.dumps(dict(summary(manifest), dry_run=args.dry_run)))
    else:
        transfer = checked_path(args.transfer)
        sources = {group: transfer / group for group in PINS}
        manifest = verify_sources(args.runtime, sources, release)
        marker = checked_path(transfer / MARKER)
        marker_bytes = io.BytesIO()
        read_file(marker, marker_bytes, limit=2 * 1024**2)
        if marker_bytes.getvalue() != encoded(manifest):
            raise ValueError("Graph transport completion manifest differs")
        expected = {MARKER} | {
            f"{g}/{r['path']}" for g, rows in manifest["groups"].items() for r in rows
        }
        actual = set()
        for path in transfer.rglob("*"):
            checked_path(path)
            if not path.is_dir():
                actual.add(path.relative_to(transfer).as_posix())
        if actual != expected:
            raise ValueError("Graph transport contains unlisted or missing files")
        print(json.dumps(summary(manifest)))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError) as exc:
        sys.exit(str(exc))
