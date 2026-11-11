"""Create a new immutable candidate runtime with reviewed geometry; no routing."""

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path

from a2_api.adapters.certified_walk_model import GeometrySupplement, ProofBinding, load_binding
from a2_api.adapters.product_runtime import Manifest, load_runtime
from a2_api.core.registry import ProductRegistry
from a2_contracts.common import canonical_hash
from a2_contracts.v2.dataset import AcceptedData


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, value: object) -> str:
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n")
    return sha(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-runtime", type=Path, required=True)
    parser.add_argument("--geometry-run", type=Path, required=True)
    parser.add_argument("--geometry-receipt-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--code-version", required=True)
    args = parser.parse_args()
    source = args.source_runtime.resolve()
    geometry = args.geometry_run.resolve()
    output = args.output_dir.resolve()
    if output == source or output.is_relative_to(source):
        raise ValueError("Candidate output must be outside the preserved source runtime")
    if sha(geometry / "receipt.json") != args.geometry_receipt_sha256:
        raise ValueError("Reviewed geometry receipt changed")
    receipt = json.loads((geometry / "receipt.json").read_text())
    if sha(source / "proof-binding.json") != receipt["proof_binding_sha256"]:
        raise ValueError("Geometry requires the original immutable model binding")
    binding = json.loads((source / "proof-binding.json").read_text())
    if binding.get("geometry") is not None:
        raise ValueError("Source runtime already has geometry")
    manifest = json.loads((source / "manifest.json").read_text())
    snapshots = [
        row for row in manifest["snapshots"] if row["snapshot_id"] == binding["snapshot_id"]
    ]
    if len(snapshots) != 1:
        raise ValueError("Original snapshot reference must be unique")
    snapshot_ref = snapshots[0]
    snapshot_path = (source / snapshot_ref["path"]).resolve()
    if not snapshot_path.is_relative_to(source) or sha(snapshot_path) != snapshot_ref["sha256"]:
        raise ValueError("Original snapshot file changed")
    snapshot = AcceptedData.model_validate_json(snapshot_path.read_bytes())
    if (
        canonical_hash(snapshot.model_dump(mode="json")) != snapshot_ref["content_hash"]
        or snapshot_ref["content_hash"] != binding["snapshot_hash"]
    ):
        raise ValueError("Original snapshot canonical identity changed")
    shutil.copytree(source, output)
    provenance = output / "provenance"
    provenance.mkdir(exist_ok=False)
    shutil.copyfile(source / "manifest.json", provenance / "original-product-manifest.json")
    shutil.copyfile(source / "runtime-settings.json", provenance / "original-runtime-settings.json")
    archive = output / "geometry"
    archive.mkdir(exist_ok=False)
    for name, expected in receipt["files_sha256"].items():
        original = (geometry / name).resolve()
        if (
            Path(name).is_absolute()
            or not original.is_relative_to(geometry)
            or sha(original) != expected
        ):
            raise ValueError("Geometry archive inventory changed")
        target = archive / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
    shutil.copyfile(geometry / "receipt.json", archive / "receipt.json")
    supplement = GeometrySupplement.model_validate(
        {
            "schema_version": "waydex-certified-walk-geometry-binding/1",
            "receipt": {"path": "geometry/receipt.json", "sha256": args.geometry_receipt_sha256},
            "original_snapshot": {"path": snapshot_ref["path"], "sha256": snapshot_ref["sha256"]},
        }
    )
    supplement_hash = write(output / "geometry-supplement.json", supplement.model_dump(mode="json"))
    binding["geometry"] = {"path": "geometry-supplement.json", "sha256": supplement_hash}
    ProofBinding.model_validate(binding)
    binding_hash = write(output / "proof-binding.json", binding)
    os.environ["A2_CERTIFIED_WALK_MODEL_PATH"] = str(output / "proof-binding.json")
    os.environ["A2_CERTIFIED_WALK_MODEL_SHA256"] = binding_hash
    proof = load_binding(ProductRegistry().config_hash)
    profiles = [row for row in manifest["profiles"] if row["executor"] == "certified_walk_model"]
    if len(profiles) != 1:
        raise ValueError("Candidate must contain the single approved certified scope")
    profiles[0]["graph_fingerprint"] = proof.graph_fingerprint
    profiles[0]["routing_profile_fingerprint"] = proof.profile_fingerprint
    Manifest.model_validate(manifest)
    manifest_hash = write(output / "manifest.json", manifest)
    settings = {
        "A2_CERTIFIED_WALK_MODEL_PATH": str(output / "proof-binding.json"),
        "A2_CERTIFIED_WALK_MODEL_SHA256": binding_hash,
        "A2_PRODUCT_RUNTIME_PATH": str(output / "manifest.json"),
        "A2_PRODUCT_RUNTIME_SHA256": manifest_hash,
        "A2_CODE_VERSION": args.code_version + ":" + binding_hash + ":" + manifest_hash,
    }
    os.environ.update(settings)
    runtime = load_runtime()
    if len(runtime.profiles) != 1:
        raise ValueError("Candidate profile did not validate")
    write(output / "runtime-settings.json", settings)
    write(
        output / "geometry-assembly.json",
        {
            "metadata_validated": True,
            "original_proof_binding_sha256": receipt["proof_binding_sha256"],
            "original_product_manifest_sha256": sha(source / "manifest.json"),
            "original_snapshot_file_sha256": snapshot_ref["sha256"],
            "original_snapshot_api_canonical_hash": snapshot_ref["content_hash"],
            "geometry_receipt_sha256": args.geometry_receipt_sha256,
            "geometry_supplement_sha256": supplement_hash,
            "native_minimum_recompute": "performed in the worker, never authorized by output flags",
        },
    )
    print(json.dumps(settings, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
