"""Verify an ML2 access receipt against a preserved exact native network proof.

The output is a declared canonical-access model minimum. Category completeness
and production index approval are deliberately separate from this proof.
"""

import argparse
import hashlib
import json
import subprocess
from dataclasses import asdict
from pathlib import Path

from a2_engine.shared.scoring import category_index
from a2_engine.urban.walk.physical_certificate import (
    load_designated_access_receipt,
    verify_common_designated_access_minimum,
)
from a2_engine.urban.walk.source_bound import load_native_walk_export, solve_native_walk_relaxation
from a2_engine.urban.walk.turn_certificate import (
    load_native_turn_weights,
    make_native_turn_incumbent,
    plan_native_turn_pairs,
    solve_native_turn_plan,
)
from audit_source_bound_walk import copy_new, digest, reserve_run_directory, write_new


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-run", type=Path, required=True)
    parser.add_argument("--physical-receipt", type=Path, required=True)
    parser.add_argument("--physical-receipt-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--h-seconds", type=float)
    args = parser.parse_args()
    source: Path = args.native_run.resolve()
    output = reserve_run_directory(args.output_dir.resolve(), args.run_id)
    package = Path(__file__).parents[1]
    try:
        for path, name in (
            (source / "receipt.json", "native-receipt.json"),
            (args.physical_receipt, "physical-receipt.json"),
            (Path(__file__), "check_physical_access.py"),
            *(
                (package / f"src/a2_engine/urban/walk/{name}", name)
                for name in ("source_bound.py", "turn_certificate.py", "physical_certificate.py")
            ),
        ):
            copy_new(path, output / name)
        native = json.loads((output / "native-receipt.json").read_text())
        for name, expected in native["files_sha256"].items():
            if digest(source / name) != expected:
                raise ValueError("PRESERVED_NATIVE_PROOF_CHANGED:" + name)
        binding = native["closed_turn_certificate"]["source_binding"]
        for name in (
            "export.jsonl",
            "request.json",
            "valhalla.json",
            "walking-profile.json",
            "build-manifest.json",
            "source-bound-walk",
            "artifact.json",
        ):
            if digest(source / name) != binding[name]:
                raise ValueError("NATIVE_SCOPE_BINDING_CHANGED")
        scope_id = hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()
        if scope_id != native["closed_turn_certificate"]["source_scope_id"]:
            raise ValueError("NATIVE_EXECUTION_SCOPE_CHANGED")
        graph = load_native_walk_export(
            (source / "export.jsonl").read_text(), expected_sha256=binding["export.jsonl"]
        )
        artifact = json.loads((source / "artifact.json").read_text())
        manifest = json.loads((source / "build-manifest.json").read_text())
        expected = {
            "source_scope_id": scope_id,
            "artifact_sha256": binding["artifact.json"],
            "profile_sha256": binding["walking-profile.json"],
            "request_sha256": binding["request.json"],
            "pbf_sha256": manifest["source_sha256"],
        }
        access = load_designated_access_receipt(
            (output / "physical-receipt.json").read_text(),
            expected_sha256=args.physical_receipt_sha256,
            graph=graph,
            expected_bindings=expected,
            artifact=artifact,
        )
        initial = solve_native_walk_relaxation(graph)
        incumbent = make_native_turn_incumbent(
            graph, scope_id, initial, (source / "replay.jsonl").read_text()
        )
        plan = plan_native_turn_pairs(graph, scope_id, incumbent)
        if plan.request_json() != (source / "turn-plan.json").read_text():
            raise ValueError("PRESERVED_NETWORK_PLAN_CHANGED")
        weights = load_native_turn_weights(
            (source / "turn-weights.jsonl").read_text(),
            plan,
            expected_sha256=native["files_sha256"]["turn-weights.jsonl"],
        )
        optimum = solve_native_turn_plan(graph, plan, weights)
        result = verify_common_designated_access_minimum(
            graph, access, plan, weights, optimum, (source / "turn-replay.jsonl").read_text()
        )
        reference_index = category_index(float(result.lower_seconds), args.h_seconds)
        report = {
            "git_commit": subprocess.check_output(
                ["git", "-C", str(package.parents[1]), "rev-parse", "HEAD"], text=True
            ).strip(),
            "native_run": str(source),
            "native_receipt_sha256": digest(output / "native-receipt.json"),
            "physical_receipt_sha256": access.content_sha256,
            "source_scope_id": scope_id,
            "model_scope_id": access.scope_id,
            "result": asdict(result),
            "total_seconds_approx": float(result.lower_seconds),
            "model_minimum_proven": result.minimum_in_declared_scope,
            "full_physical_minimum_proven": result.full_physical_minimum_proven,
            "product_index_eligible": result.product_index_eligible,
            "reference_formula_only": {"h_seconds": args.h_seconds, "value": reference_index.value},
            "index": None,
            "files_sha256": {
                path.name: digest(path) for path in output.iterdir() if path.is_file()
            },
        }
        write_new(
            output / "receipt.json", json.dumps(report, ensure_ascii=False, indent=2, default=str)
        )
        print(
            json.dumps(
                {
                    "receipt": str(output / "receipt.json"),
                    "total_seconds": str(result.lower_seconds),
                    "total_seconds_approx": float(result.lower_seconds),
                    "minimum_in_declared_scope": result.minimum_in_declared_scope,
                    "product_index_eligible": False,
                    "index": None,
                    "reference_formula_only": reference_index.value,
                }
            )
        )
    except Exception as error:
        write_new(
            output / "failure.json",
            json.dumps({"error_type": type(error).__name__, "error": str(error)}),
        )
        raise


if __name__ == "__main__":
    main()
