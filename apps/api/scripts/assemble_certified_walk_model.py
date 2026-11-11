"""Assemble a relocatable, hash-pinned archive; never route or trust result flags."""

import argparse
import hashlib
import json
import os
import shutil
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from a2_api.adapters.certified_walk_model import ProofBinding, load_binding
from a2_api.adapters.product_runtime import Manifest, load_runtime
from a2_api.core.registry import ProductRegistry
from a2_contracts.common import canonical_hash
from a2_data_pipeline.contract_v2 import load_accepted_data, registry_options
from a2_data_pipeline.store import load_bundle


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def write(path: Path, value: object) -> str:
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n")
    return sha(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-run", type=Path, required=True)
    parser.add_argument("--physical-receipt", type=Path, required=True)
    parser.add_argument("--compatibility", type=Path, required=True)
    parser.add_argument("--access-policy", type=Path, required=True)
    parser.add_argument("--official-sources", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--code-version", required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    for directory in ("native", "physical", "metadata"):
        (output / directory).mkdir()
    native = json.loads((args.native_run / "receipt.json").read_text())
    for name, expected in native["files_sha256"].items():
        if Path(name).name != name or sha(args.native_run / name) != expected:
            raise ValueError("Native archive hash/inventory changed")
        shutil.copyfile(args.native_run / name, output / "native" / name)
    shutil.copyfile(args.native_run / "receipt.json", output / "native/receipt.json")
    references = {}
    for key, source, relative in (
        ("native_receipt", args.native_run / "receipt.json", "native/receipt.json"),
        ("physical_access", args.physical_receipt, "physical/physical-receipt.json"),
        ("compatibility", args.compatibility, "metadata/compatibility.json"),
        ("policy", args.access_policy, "metadata/access-policy.json"),
        ("official_sources", args.official_sources, "metadata/official-sources.json"),
    ):
        if key != "native_receipt":
            shutil.copyfile(source, output / relative)
        references[key] = dict(path=relative, sha256=sha(source))
    compatibility = json.loads(args.compatibility.read_text())
    physical = json.loads(args.physical_receipt.read_text())
    artifact = json.loads((args.native_run / "artifact.json").read_text())
    snapshot = load_accepted_data(compatibility["target_snapshot_id"])
    known = {row.evidence_id for row in snapshot.evidence}
    known.update(row["evidence_id"] for row in artifact["evidence_refs"])
    needed = {
        ref
        for side in physical["endpoints"]
        for alternative in side["native_alternatives"]
        for ref in alternative["evidence_refs"]
    } | {
        "snapshot-compatibility:" + references["compatibility"]["sha256"],
        "canonical-access-policy:" + references["policy"]["sha256"],
        "physical-access-proof:" + references["physical_access"]["sha256"],
        "native-scope:" + physical["source_scope_id"],
    }
    source = next(
        row
        for row in artifact["evidence_refs"]
        if row["evidence_id"].startswith("osm-geofabrik-pbf-")
    )
    evidence = [
        dict(
            evidence_id=ref,
            source_id="waydex-locally-preserved-certified-model",
            url=source["url"],
            retrieved_at=datetime.now(UTC).isoformat(),
            observed_at=None,
            section="Locally preserved immutable "
            + ref
            + "; source URL identifies originating PBF data, not a receipt download. Native and physical minima are recomputed by the worker.",
        )
        for ref in sorted(needed - known)
    ]
    facts = compatibility["selected_pair_invariants"]
    binding = ProofBinding.model_validate(
        dict(
            schema_version="waydex-certified-walk-model/1",
            snapshot_id=snapshot.snapshot_id,
            snapshot_hash=canonical_hash(snapshot.model_dump(mode="json")),
            city_id=facts["destination"]["serving_city_id"],
            point_id=facts["point"]["point_id"],
            destination_id=facts["destination"]["destination_id"],
            service_date=compatibility["official_roster_full_only_date"],
            evidence=evidence,
            **references,
        )
    )
    binding_hash = write(output / "proof-binding.json", binding.model_dump(mode="json"))
    os.environ["A2_CERTIFIED_WALK_MODEL_PATH"] = str(output / "proof-binding.json")
    os.environ["A2_CERTIFIED_WALK_MODEL_SHA256"] = binding_hash
    registry = ProductRegistry()
    proof = load_binding(registry.config_hash)
    snapshot_file_hash = write(output / "accepted-data.json", snapshot.model_dump(mode="json"))
    city = next(row for row in snapshot.territories if row.territory_id == binding.city_id)
    start = datetime.combine(binding.service_date, time.min, tzinfo=ZoneInfo(city.timezone))
    options = registry_options(load_bundle(snapshot.snapshot_id))
    manifest = Manifest.model_validate(
        dict(
            schema_version="waydex-product-runtime/1",
            snapshots=[
                dict(
                    path="accepted-data.json",
                    sha256=snapshot_file_hash,
                    snapshot_id=snapshot.snapshot_id,
                    content_hash=binding.snapshot_hash,
                )
            ],
            territories=options["territories"],
            national_catalog_complete=False,
            profiles=[
                dict(
                    profile_id="canonical-access-airport-" + binding.service_date.isoformat(),
                    analysis_kind="urban_passenger",
                    city_id=binding.city_id,
                    passenger_modes=["walk"],
                    categories=["airport"],
                    traffic_modes=["static"],
                    valid_from=start.isoformat(),
                    valid_to=(start + timedelta(days=1)).isoformat(),
                    snapshot_id=snapshot.snapshot_id,
                    snapshot_hash=binding.snapshot_hash,
                    graph_fingerprint=proof.graph_fingerprint,
                    routing_profile_fingerprint=proof.profile_fingerprint,
                    executor="certified_walk_model",
                    static_observations=True,
                    max_route_pairs=1,
                )
            ],
        )
    )
    manifest_hash = write(output / "manifest.json", manifest.model_dump(mode="json"))
    os.environ["A2_PRODUCT_RUNTIME_PATH"] = str(output / "manifest.json")
    os.environ["A2_PRODUCT_RUNTIME_SHA256"] = manifest_hash
    code = args.code_version + ":" + binding_hash + ":" + manifest_hash
    os.environ["A2_CODE_VERSION"] = code
    runtime = load_runtime()
    if len(runtime.profiles) != 1:
        raise ValueError("Assembled certified profile is unavailable")
    settings = dict(
        A2_PRODUCT_RUNTIME_PATH=str(output / "manifest.json"),
        A2_PRODUCT_RUNTIME_SHA256=manifest_hash,
        A2_CERTIFIED_WALK_MODEL_PATH=str(output / "proof-binding.json"),
        A2_CERTIFIED_WALK_MODEL_SHA256=binding_hash,
        A2_CODE_VERSION=code,
    )
    write(output / "runtime-settings.json", settings)
    print(json.dumps(settings, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
