"""Offline, exclusive geometry supplement for a preserved native certificate."""

import argparse
import json
import subprocess
from dataclasses import asdict
from pathlib import Path

from a2_engine.urban.walk.canonical_point import load_reviewed_common_access_model
from a2_engine.urban.walk.geometry import load_certified_walk_geometry, make_certified_geometry_plan
from a2_engine.urban.walk.source_bound import load_native_walk_export, verify_native_walk_replay
from audit_source_bound_walk import (
    copy_new,
    digest,
    reserve_run_directory,
    validate_graph_inputs,
    write_new,
)

BUILDER = "sha256:829fa84fde0e664a34fba73dc1c18e95a2a30309b6cf7ca4d3e45b5492439753"
LIBRARY = "fe4ba40ce91c0cede90e04c33c47a2897bce60b774ddbc49ead025741d14546f"


def reference(root: Path, ref: dict[str, str]) -> Path:
    relative = Path(ref["path"])
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or any(parent.is_symlink() for parent in (root / relative, *(root / relative).parents))
    ):
        raise ValueError("GEOMETRY_LOCAL_IMMUTABLE_REFERENCE_REQUIRED")
    path = root / relative
    if digest(path) != ref["sha256"]:
        raise ValueError("GEOMETRY_REFERENCED_FILE_CHANGED")
    return path


def execute(args: argparse.Namespace, output: Path) -> None:
    root, assets = args.model_dir.resolve(), args.assets_dir.resolve()
    binding = json.loads((root / "proof-binding.json").read_text())
    native_path = reference(root, binding["native_receipt"])
    physical_path = reference(root, binding["physical_access"])
    native = json.loads(native_path.read_text())
    native_root = native_path.parent
    # Verify all original certificate artifacts, not just the selected shape IDs.
    for name, expected in native["files_sha256"].items():
        reference(native_root, {"path": name, "sha256": expected})
    artifact = json.loads((native_root / "artifact.json").read_text())
    validate_graph_inputs(args.graph_dir.resolve(), artifact)
    if (
        digest(args.graph_dir / "valhalla-build-manifest.json")
        != native["files_sha256"]["build-manifest.json"]
    ):
        raise ValueError("GEOMETRY_GRAPH_CERTIFICATE_MISMATCH")
    for name in ("compatibility", "policy", "official_sources"):
        reference(root, binding[name])
    compiler = json.loads((assets / "compiler-manifest.json").read_text())
    if (
        compiler["builder_image_id"] != BUILDER
        or compiler["runtime_library_sha256"] != LIBRARY
        or compiler["source_sha256"] != digest(assets / "export_certified_geometry.cc")
        or compiler["binary_sha256"] != digest(assets / "export-certified-geometry")
    ):
        raise ValueError("GEOMETRY_COMPILER_BINDING_CHANGED")
    for name, expected in compiler["headers_sha256"].items():
        reference(assets, {"path": name, "sha256": expected})
    current_source = Path(__file__).parents[1] / "native/export_certified_geometry.cc"
    if digest(current_source) != compiler["source_sha256"]:
        raise ValueError("GEOMETRY_NATIVE_SOURCE_NOT_CURRENT")
    # The physical model attestation lives in the original independently reviewed
    # run. Its name/hash are explicit executor inputs, never inferred from a URL.
    model_json = args.model_receipt.read_text()
    access, minimum = load_reviewed_common_access_model(
        native_path.read_text(),
        model_json,
        physical_path.read_text(),
        (native_root / "artifact.json").read_text(),
        native_receipt_sha256=binding["native_receipt"]["sha256"],
        model_receipt_sha256=args.model_receipt_sha256,
        physical_receipt_sha256=binding["physical_access"]["sha256"],
    )
    candidate = minimum.candidate.network_path
    graph = load_native_walk_export(
        (native_root / "export.jsonl").read_text(),
        expected_sha256=native["files_sha256"]["export.jsonl"],
    )
    if (
        verify_native_walk_replay(candidate, (native_root / "turn-replay.jsonl").read_text())
        != candidate.lower_seconds
    ):
        raise ValueError("GEOMETRY_CERTIFIED_PATH_REPLAY_FAILED")
    plan = make_certified_geometry_plan(
        native_path.read_text(),
        (native_root / "turn-candidate.json").read_text(),
        (native_root / "request.json").read_text(),
        native_receipt_sha256=binding["native_receipt"]["sha256"],
    )
    package = Path(__file__).parents[1]
    for source, name in (
        (root / "proof-binding.json", "proof-binding.json"),
        (native_path, "native-receipt.json"),
        (physical_path, "physical-receipt.json"),
        (args.model_receipt, "model-receipt.json"),
        (native_root / "build-manifest.json", "build-manifest.json"),
        (native_root / "valhalla.json", "valhalla.json"),
        (native_root / "walking-profile.json", "walking-profile.json"),
        (native_root / "turn-candidate.json", "turn-candidate.json"),
        (assets / "compiler-manifest.json", "compiler-manifest.json"),
        (assets / "export-certified-geometry", "export-certified-geometry"),
        (current_source, "export_certified_geometry.cc"),
        (Path(__file__), "export_certified_walk_geometry.py"),
        (package / "examples/audit_source_bound_walk.py", "audit_source_bound_walk.py"),
        (package / "src/a2_engine/urban/walk/geometry.py", "geometry.py"),
        (package / "src/a2_engine/urban/walk/physical_certificate.py", "physical_certificate.py"),
        (package / "src/a2_engine/urban/walk/canonical_point.py", "canonical_point.py"),
        (package / "src/a2_engine/urban/walk/source_bound.py", "source_bound.py"),
    ):
        copy_new(source, output / name)
    for name in compiler["headers_sha256"]:
        (output / name).parent.mkdir(parents=True, exist_ok=True)
        copy_new(assets / name, output / name)
    write_new(output / "plan.json", plan)
    image_id = subprocess.check_output(
        ["docker", "image", "inspect", "waydex-walk-proof-builder:3.5.1", "--format", "{{.Id}}"],
        text=True,
    ).strip()
    if image_id != BUILDER:
        raise ValueError("GEOMETRY_BUILDER_IMAGE_CHANGED")
    command = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--name",
        "waydex-geometry-" + output.name,
        "--mount",
        f"type=bind,src={args.graph_dir.resolve()},dst=/data,readonly",
        "--mount",
        f"type=bind,src={output},dst=/proof,readonly",
        "--entrypoint",
        "/proof/export-certified-geometry",
        BUILDER,
        "/proof/valhalla.json",
        "/proof/plan.json",
    ]
    with (
        (output / "export.jsonl").open("xb") as stdout,
        (output / "stderr.txt").open("xb") as stderr,
    ):
        completed = subprocess.run(command, stdout=stdout, stderr=stderr, check=False)
    if completed.returncode:
        raise ValueError("GEOMETRY_NATIVE_EXPORT_FAILED")
    # Detect graph changes during execution as well as before it.
    validate_graph_inputs(args.graph_dir.resolve(), artifact)
    result = load_certified_walk_geometry(
        (output / "export.jsonl").read_text(),
        expected_sha256=digest(output / "export.jsonl"),
        plan_json=plan,
        graph=graph,
        candidate=candidate,
        access=access,
    )
    write_new(output / "geometry.json", json.dumps(asdict(result), indent=2, allow_nan=False))
    write_new(output / "route.geojson", json.dumps(result.physical_geojson, allow_nan=False))
    receipt = {
        "schema": "waydex-certified-walk-geometry/1",
        "geometry_only": True,
        "promotes_minimum_or_coverage": False,
        "orientation_authority": "actual DirectedEdge.forward() from pinned native exporter; original bits retained",
        "node_precision_check": "necessary seventh-digit to sixth-digit lattice sanity only; decimal half-microdegree permits adjacent representations; never changes coordinates or proves direction/connectivity",
        "node_precision_counterexample": {
            "native_node_longitude": 20.5290215,
            "actual_shape_longitude": 20.529021,
            "reencoding_reconstructed_node_longitude": 20.529021999999998,
        },
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "native_receipt_sha256": binding["native_receipt"]["sha256"],
        "proof_binding_sha256": digest(root / "proof-binding.json"),
        "physical_receipt_sha256": access.content_sha256,
        "model_receipt_sha256": args.model_receipt_sha256,
        "source_scope_id": result.source_scope_id,
        "candidate_sha256": result.candidate_sha256,
        "source_files_sha256": native["files_sha256"],
        "source_build_manifest_sha256": digest(native_root / "build-manifest.json"),
        "snapshot_id": binding["snapshot_id"],
        "snapshot_hash": binding["snapshot_hash"],
        "builder_image_id": image_id,
        "runtime_library_sha256": LIBRARY,
        "native_replay_seconds": str(candidate.lower_seconds),
        "unchanged_model_total_seconds": str(minimum.lower_seconds),
        "edge_count": len(result.edge_ids),
        "network_vertices": len(result.network_coordinates or ()),
        "physical_vertices": len(result.physical_coordinates or ()),
        "discontinuities": result.discontinuities,
        "limitations": result.limitations,
        "files_sha256": {
            str(p.relative_to(output)): digest(p) for p in output.rglob("*") if p.is_file()
        },
    }
    write_new(output / "receipt.json", json.dumps(receipt, indent=2))
    print(
        json.dumps(
            {
                "receipt": str(output / "receipt.json"),
                "sha256": digest(output / "receipt.json"),
                "edges": len(result.edge_ids),
                "physical_vertices": len(result.physical_coordinates or ()),
                "discontinuities": result.discontinuities,
            }
        )
    )


def run(args: argparse.Namespace) -> Path:
    output = reserve_run_directory(args.output_dir, args.run_id)
    try:
        execute(args, output)
    except Exception as error:
        write_new(
            output / "failure.json",
            json.dumps({"error_type": type(error).__name__, "error": str(error)}),
        )
        raise
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("model-dir", "graph-dir", "assets-dir", "output-dir", "model-receipt"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--model-receipt-sha256", required=True)
    parser.add_argument("--run-id", required=True)
    run(parser.parse_args())
