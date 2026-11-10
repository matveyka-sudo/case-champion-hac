"""Execute a pinned native graph audit into an exclusive, preserved run directory."""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import asdict
from fractions import Fraction
from pathlib import Path
from typing import Any

from a2_engine.urban.walk.source_bound import (
    check_native_walk_bounds,
    load_native_walk_export,
    solve_native_walk_relaxation,
)

PINNED_IMAGE = "sha256:4ac3d13d1019f9a130e9e6b6924c16ea55da0fa51e3653039152e33ad4095661"
SOURCE_HASHES = {
    "src_sif_pedestriancost.cc": "f45611ece3cf42d9dc394ab185bf720f0405415cba521310f087b69f1ffd7cbd",
    "src_thor_unidirectional_astar.cc": "c3fba59b10a374f812074fb535fa413ed6289e135faf5c4d9783ebebd6cd8e4a",
    "src_loki_search.cc": "38f3b89d9ac8d61e4432abc03369a0397947279be4a10999c6df7dc467f4b257",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path: Path, text: str) -> None:
    with path.open("x") as stream:
        stream.write(text)


def copy_new(source: Path, target: Path) -> None:
    with source.open("rb") as input_stream, target.open("xb") as output_stream:
        shutil.copyfileobj(input_stream, output_stream)
    target.chmod(source.stat().st_mode)


def reserve_run_directory(root: Path, run_id: str | None = None) -> Path:
    identifier = run_id or uuid.uuid4().hex
    if not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
        raise ValueError("SIMPLE_RUN_ID_REQUIRED")
    runs = root / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    output = runs / identifier
    output.mkdir(exist_ok=False)
    return output


def validate_graph_inputs(graph_dir: Path, artifact: dict[str, Any]) -> dict[str, Any]:
    """Reject redirected/extract/remote sources before invoking any subprocess."""
    if any(path.is_symlink() for path in (graph_dir / "tiles").rglob("*")) or any(
        (graph_dir / name).is_symlink()
        for name in ("valhalla.json", "walking-profile.json", "valhalla-build-manifest.json")
    ):
        raise ValueError("SYMLINK_TILE_SOURCE_FORBIDDEN")
    manifest: dict[str, Any] = json.loads((graph_dir / "valhalla-build-manifest.json").read_text())
    if manifest["image_id"] != PINNED_IMAGE:
        raise ValueError("UNPINNED_VALHALLA_IMAGE")
    if digest(graph_dir / "valhalla.json") != manifest["config_sha256"]:
        raise ValueError("VALHALLA_CONFIG_CHANGED")
    if digest(graph_dir / "walking-profile.json") != manifest["profile_sha256"]:
        raise ValueError("WALKING_PROFILE_CHANGED")
    profile = json.loads((graph_dir / "walking-profile.json").read_text())
    if profile["pedestrian_options"] != json.loads(artifact["build"]["pedestrian_options_json"]):
        raise ValueError("ARTIFACT_PROFILE_MISMATCH")
    mjolnir = json.loads((graph_dir / "valhalla.json").read_text())["mjolnir"]
    if mjolnir.get("tile_dir") != "/data/tiles" or any(
        value for key, value in mjolnir.items() if key.startswith("tile_url")
    ):
        raise ValueError("ALTERNATIVE_TILE_SOURCE_FORBIDDEN")
    for key in ("tile_extract", "traffic_extract"):
        value = mjolnir.get(key)
        if value:
            path = Path(value)
            if not path.is_relative_to("/data") or ".." in path.parts:
                raise ValueError("ALTERNATIVE_TILE_SOURCE_FORBIDDEN")
            alternate = graph_dir / path.relative_to("/data")
            if alternate.exists() or alternate.is_symlink():
                raise ValueError("UNEXPECTED_ALTERNATIVE_TILE_EXTRACT")
    if list((graph_dir / "tiles").rglob("*.gph.gz")):
        raise ValueError("UNEXPECTED_COMPRESSED_TILE_SOURCE")
    if {str(path.relative_to(graph_dir)) for path in (graph_dir / "tiles").rglob("*.gph")} != {
        row["path"] for row in manifest["files"]
    }:
        raise ValueError("SOURCE_TILE_ROSTER_CHANGED")
    for row in manifest["files"]:
        if digest(graph_dir / row["path"]) != row["sha256"]:
            raise ValueError("SOURCE_TILE_CHANGED:" + row["path"])
    return manifest


def execute(args: argparse.Namespace, output: Path) -> None:
    assets: Path = args.output_dir.resolve()
    graph_dir: Path = args.graph_dir.resolve()
    package = Path(__file__).parents[1]
    for source, name in (
        (assets / "source-bound-walk", "source-bound-walk"),
        (assets / "compiler-manifest.json", "compiler-manifest.json"),
        (package / "native/source_bound_walk.cc", "source_bound_walk.cc"),
        (Path(__file__), "audit_source_bound_walk.py"),
        (package / "src/a2_engine/urban/walk/source_bound.py", "source_bound.py"),
        (args.artifact, "artifact.json"),
        (graph_dir / "valhalla.json", "valhalla.json"),
        (graph_dir / "walking-profile.json", "walking-profile.json"),
        (graph_dir / "valhalla-build-manifest.json", "build-manifest.json"),
    ):
        copy_new(source, output / name)
    artifact: dict[str, Any] = json.loads((output / "artifact.json").read_text())
    manifest = validate_graph_inputs(graph_dir, artifact)
    if digest(output / "valhalla.json") != manifest["config_sha256"]:
        raise ValueError("CONFIG_SNAPSHOT_CHANGED")
    if digest(output / "walking-profile.json") != manifest["profile_sha256"] or (
        digest(output / "build-manifest.json") != digest(graph_dir / "valhalla-build-manifest.json")
    ):
        raise ValueError("GRAPH_MANIFEST_PROFILE_SNAPSHOT_CHANGED")
    (output / "source").mkdir()
    for name, expected in SOURCE_HASHES.items():
        copy_new(assets / "source" / name, output / "source" / name)
        if digest(output / "source" / name) != expected:
            raise ValueError("SOURCE_INVENTORY_CHANGED:" + name)
    compiler = json.loads((output / "compiler-manifest.json").read_text())
    if compiler["source_sha256"] != digest(output / "source_bound_walk.cc") or compiler[
        "binary_sha256"
    ] != digest(output / "source-bound-walk"):
        raise ValueError("COMPILER_SOURCE_BINARY_BINDING_CHANGED")
    builder_id = subprocess.check_output(
        ["docker", "image", "inspect", "--format", "{{.Id}}", args.builder_image], text=True
    ).strip()
    if builder_id != compiler["builder_image_id"]:
        raise ValueError("COMPILER_BUILDER_IMAGE_CHANGED")
    library_hashes = [
        subprocess.check_output(
            [
                "docker",
                "run",
                "--rm",
                "--pull",
                "never",
                "--network",
                "none",
                "--entrypoint",
                "sha256sum",
                image,
                "/usr/local/lib/libvalhalla.a",
            ],
            text=True,
        ).split()[0]
        for image in (builder_id, "ghcr.io/valhalla/valhalla@" + PINNED_IMAGE)
    ]
    if library_hashes[0] != library_hashes[1]:
        raise ValueError("BUILDER_VALHALLA_LIBRARY_CHANGED")
    request = {
        "costing": "pedestrian",
        "costing_options": {"pedestrian": json.loads(artifact["build"]["pedestrian_options_json"])},
        "locations": [
            {**row["source_location"], "minimum_reachability": 50}
            for row in artifact["correlations"]
        ],
    }
    write_new(output / "request.json", json.dumps(request, separators=(",", ":")))
    base = [
        "docker",
        "run",
        "--rm",
        "--pull",
        "never",
        "--network",
        "none",
        "--entrypoint",
        "/proof/source-bound-walk",
        "--mount",
        f"type=bind,source={graph_dir},target=/data,readonly",
        "--mount",
        f"type=bind,source={output},target=/proof,readonly",
        builder_id,
        "/proof/valhalla.json",
        "/proof/request.json",
    ]
    with (
        (output / "export.jsonl").open("x") as stream,
        (output / "export.stderr").open("x") as errors,
    ):
        subprocess.run(base, stdout=stream, stderr=errors, check=True)
    export = (output / "export.jsonl").read_text()
    graph = load_native_walk_export(export, expected_sha256=digest(output / "export.jsonl"))
    expected_tiles = set()
    for row in manifest["files"]:
        parts = Path(row["path"]).with_suffix("").parts
        expected_tiles.add((int("".join(parts[2:])) << 3) | int(parts[1]))
    if graph.tile_ids != expected_tiles:
        raise ValueError("NATIVE_TILE_INVENTORY_DIFFERS_FROM_SOURCE")
    candidate = solve_native_walk_relaxation(graph)
    write_new(output / "candidate.json", candidate.replay_json())
    replay_run = subprocess.run(base + ["/proof/candidate.json"], capture_output=True, text=True)
    write_new(output / "replay.jsonl", replay_run.stdout)
    write_new(output / "replay.stderr", replay_run.stderr)
    validate_graph_inputs(graph_dir, artifact)
    connectors = artifact["connectors"]
    result = check_native_walk_bounds(
        candidate,
        replay_run.stdout if replay_run.returncode == 0 else None,
        origin_connector_seconds=Fraction(str(connectors[0]["seconds"])),
        destination_connector_seconds=Fraction(str(connectors[1]["seconds"])),
    )
    receipt = {
        "git_commit": subprocess.check_output(
            ["git", "-C", str(package.parents[1]), "rev-parse", "HEAD"], text=True
        ).strip(),
        "model": "exact additive binary32 Valhalla3.5.1 edge/transition weights; static time=0",
        "scope": "all pinned tiles/levels; native Loki endpoint set incl filtered retry edges",
        "runtime_base_image": PINNED_IMAGE,
        "builder_image_id": builder_id,
        "runtime_library_sha256": library_hashes[0],
        "files_sha256": {path.name: digest(path) for path in output.iterdir() if path.is_file()},
        "source_inventory": SOURCE_HASHES,
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
        "tiles": len(graph.tile_ids),
        "endpoints": len(graph.endpoints),
        "native_endpoint_inventory": [asdict(endpoint) for endpoint in graph.endpoints],
        "native_replay_exit_code": replay_run.returncode,
        "result": asdict(result),
        "network_lower_seconds_approx": float(result.network_lower_seconds),
        "network_upper_seconds_approx": (
            float(result.network_upper_seconds)
            if result.network_upper_seconds is not None
            else None
        ),
        "index": None,
        "limitations": [
            "Loki finite search is not an exhaustive physical entrance inventory.",
            "Physical endpoint weights and projection offsets remain unbound.",
            "HTTP summaries/accumulated floats are not this exact additive objective.",
            "No complete destination/population scope follows from this audit.",
        ],
    }
    write_new(
        output / "receipt.json", json.dumps(receipt, ensure_ascii=False, indent=2, default=str)
    )
    print(
        json.dumps(
            {"receipt": str(output / "receipt.json"), "result": asdict(result), "index": None},
            default=str,
        )
    )


def run_audit(
    args: argparse.Namespace, executor: Callable[[argparse.Namespace, Path], None] = execute
) -> Path:
    output = reserve_run_directory(args.output_dir.resolve(), args.run_id)
    try:
        executor(args, output)
    except Exception as error:
        write_new(
            output / "failure.json",
            json.dumps({"error_type": type(error).__name__, "error": str(error)}),
        )
        raise
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--builder-image", default="waydex-walk-proof-builder:3.5.1")
    parser.add_argument("--run-id")
    run_audit(parser.parse_args())


if __name__ == "__main__":
    main()
