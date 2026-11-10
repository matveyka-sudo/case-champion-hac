"""Execute the pinned native graph inventory and replay against real local tiles.

Outputs live outside the repository. The receipt deliberately keeps index null:
source-bound network bounds cannot replace physical access or destination coverage.
"""

import argparse
import hashlib
import json
import subprocess
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--builder-image", default="waydex-walk-proof-builder:3.5.1")
    args = parser.parse_args()
    output: Path = args.output_dir.resolve()
    graph_dir: Path = args.graph_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    artifact: dict[str, Any] = json.loads(args.artifact.read_text())
    manifest = json.loads((graph_dir / "valhalla-build-manifest.json").read_text())
    if manifest["image_id"] != PINNED_IMAGE:
        raise ValueError("UNPINNED_VALHALLA_IMAGE")
    if {str(path.relative_to(graph_dir)) for path in (graph_dir / "tiles").rglob("*.gph")} != {
        row["path"] for row in manifest["files"]
    }:
        raise ValueError("SOURCE_TILE_ROSTER_CHANGED")
    for row in manifest["files"]:
        if digest(graph_dir / row["path"]) != row["sha256"]:
            raise ValueError("SOURCE_TILE_CHANGED:" + row["path"])
    for name, expected in SOURCE_HASHES.items():
        if digest(output / "source" / name) != expected:
            raise ValueError("SOURCE_INVENTORY_CHANGED:" + name)
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
        for image in (args.builder_image, "ghcr.io/valhalla/valhalla@" + PINNED_IMAGE)
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
    (output / "request.json").write_text(json.dumps(request, separators=(",", ":")))
    base = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--entrypoint",
        "/proof/source-bound-walk",
        "--mount",
        f"type=bind,source={graph_dir},target=/data,readonly",
        "--mount",
        f"type=bind,source={output},target=/proof",
        args.builder_image,
        "/data/valhalla.json",
        "/proof/request.json",
    ]
    with (output / "export.jsonl").open("w") as stream:
        subprocess.run(base, stdout=stream, check=True)
    export = (output / "export.jsonl").read_text()
    graph = load_native_walk_export(export, expected_sha256=digest(output / "export.jsonl"))
    expected_tiles = set()
    for row in manifest["files"]:
        parts = Path(row["path"]).with_suffix("").parts
        expected_tiles.add((int("".join(parts[2:])) << 3) | int(parts[1]))
    if graph.tile_ids != expected_tiles:
        raise ValueError("NATIVE_TILE_INVENTORY_DIFFERS_FROM_SOURCE")
    candidate = solve_native_walk_relaxation(graph)
    (output / "candidate.json").write_text(candidate.replay_json())
    replay_run = subprocess.run(base + ["/proof/candidate.json"], capture_output=True, text=True)
    (output / "replay.jsonl").write_text(replay_run.stdout)
    (output / "replay.stderr").write_text(replay_run.stderr)
    connectors = artifact["connectors"]
    result = check_native_walk_bounds(
        candidate,
        replay_run.stdout if replay_run.returncode == 0 else None,
        origin_connector_seconds=Fraction(str(connectors[0]["seconds"])),
        destination_connector_seconds=Fraction(str(connectors[1]["seconds"])),
    )
    receipt = {
        "model": "exact additive binary32 Valhalla3.5.1 edge/transition weights; static time=0",
        "scope": "all pinned tiles, all hierarchy levels, complete native Loki result incl filtered retry edges",
        "runtime_base_image": PINNED_IMAGE,
        "runtime_library_sha256": library_hashes[0],
        "exporter_source_sha256": digest(Path(__file__).parents[1] / "native/source_bound_walk.cc"),
        "binary_sha256": digest(output / "source-bound-walk"),
        "build_manifest_sha256": digest(graph_dir / "valhalla-build-manifest.json"),
        "artifact_sha256": digest(args.artifact),
        "request_sha256": digest(output / "request.json"),
        "export_sha256": graph.content_sha256,
        "source_inventory": SOURCE_HASHES,
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
        "tiles": len(graph.tile_ids),
        "endpoints": len(graph.endpoints),
        "native_endpoint_inventory": [asdict(endpoint) for endpoint in graph.endpoints],
        "native_replay_exit_code": replay_run.returncode,
        "native_replay_stderr": replay_run.stderr,
        "result": asdict(result),
        "network_lower_seconds_approx": float(result.network_lower_seconds),
        "network_upper_seconds_approx": (
            float(result.network_upper_seconds)
            if result.network_upper_seconds is not None
            else None
        ),
        "index": None,
        "limitations": [
            "Loki's finite search is not an exhaustive physical entrance inventory.",
            "Physical access-to-projection offsets remain unconfirmed.",
            "HTTP millisecond summaries and accumulated floats are not the exact additive objective.",
            "No complete destination catalog or population territory score follows from this proof.",
        ],
    }
    (output / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, default=str)
    )
    print(
        json.dumps(
            {"receipt": str(output / "receipt.json"), "result": asdict(result), "index": None},
            default=str,
        )
    )


if __name__ == "__main__":
    main()
