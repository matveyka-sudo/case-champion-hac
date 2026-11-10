"""Restore immutable local runtime artifacts and report source coverage.

This command makes no HTTP calls. Provider bindings stay in ignored processed/
storage; reports contain their counts and hashes, never provider codes. Presence
of an archive, a terminal or a frequency table does not establish routability.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import tempfile
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .paths import data_dir
from .store import load_bundle

DEFAULT_MANIFEST = "manifests/external/runtime-handoff-2026-11-10.json"
CURRENT_BUNDLES = (
    "kaliningrad-urban-2026-11-10.5",
    "novorossiysk-urban-2026-11-10.2",
    "moscow-urban-2026-11-10.2",
)


class LocalArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: str = Field(min_length=1)
    kind: Literal["gtfs", "internal_rasp_bindings"]
    handoff_path: str
    target_path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    verification_path: str


class ArtifactManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["waydex-local-artifacts/1"]
    artifacts: tuple[LocalArtifact, ...]


def _path(root: Path, relative: str) -> Path:
    parts = PurePosixPath(relative)
    if parts.is_absolute() or not parts.parts or ".." in parts.parts:
        raise ValueError("ARTIFACT_PATH_MUST_BE_RELATIVE")
    target = root.joinpath(*parts.parts)
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("ARTIFACT_PATH_ESCAPES_ROOT")
    return target


def _hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _empty_directory(path: Path) -> bool:
    return not path.is_symlink() and path.is_dir() and not any(path.iterdir())


@contextmanager
def _publication_lock(target: Path) -> Iterator[None]:
    """A persistent sibling lock; the kernel releases ownership after SIGKILL.

    The file is deliberately retained: removing it would let another publisher
    lock a different inode while an earlier waiter still holds the old inode.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_name(f".{target.name}.publish.lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _publish_directory(
    staging: Path, target: Path, verify_existing: Callable[[Path], object]
) -> None:
    """Publish a complete directory under the cooperative publication lock.

    No reservation is created at the final path. Empty directories left by the
    previous publisher version are recovered; existing content is only verified
    and reused. All cooperating writers use this same persistent sibling lock.
    """
    with _publication_lock(target):
        if os.path.lexists(target) and not _empty_directory(target):
            verify_existing(target)
            return
        os.rename(staging, target)


def artifact_manifest(root: Path | None = None) -> ArtifactManifest:
    root = data_dir() if root is None else root
    manifest = ArtifactManifest.model_validate_json((root / DEFAULT_MANIFEST).read_bytes())
    ids = [a.artifact_id for a in manifest.artifacts]
    targets = [a.target_path for a in manifest.artifacts]
    if len(ids) != len(set(ids)) or len(targets) != len(set(targets)):
        raise ValueError("DUPLICATE_LOCAL_ARTIFACT")
    for item in manifest.artifacts:
        if PurePosixPath(item.target_path).parts[:1] != ("processed",):
            raise ValueError("LOCAL_ARTIFACT_MUST_STAY_IN_PROCESSED")
        for value in (item.handoff_path, item.target_path, item.verification_path):
            _path(root, value)
    return manifest


def _verify(item: LocalArtifact, path: Path, root: Path) -> dict[str, Any]:
    reference = json.loads(_path(root, item.verification_path).read_bytes())
    if item.kind == "gtfs":
        if reference["sha256"] != item.sha256 or reference["path"] != item.target_path:
            raise ValueError("TIMETABLE_REFERENCE_MISMATCH")
        if _hash(path) != item.sha256:
            raise ValueError("ARTIFACT_SHA256_MISMATCH")
        return {"sha256": item.sha256, "size_bytes": path.stat().st_size}
    if _hash(path / "manifest.json") != item.sha256:
        raise ValueError("BINDING_MANIFEST_SHA256_MISMATCH")
    local = json.loads((path / "manifest.json").read_bytes())
    if (
        local["output_id"] != item.artifact_id
        or local["source"] != reference["source"]
        or local["inputs"] != reference["inputs"]
        or local["files"] != reference["files"]
        or set(local["files"]) != {"node_codes.json", "bindings.json"}
    ):
        raise ValueError("BINDING_PROVENANCE_MISMATCH")
    counts = {}
    for name, expected in local["files"].items():
        content = _path(path, name)
        if _hash(content) != expected["sha256"]:
            raise ValueError("BINDING_FILE_SHA256_MISMATCH")
        rows = json.loads(content.read_bytes())
        if not isinstance(rows, list) or len(rows) != expected["rows"]:
            raise ValueError("BINDING_ROW_COUNT_MISMATCH")
        counts[name] = len(rows)
    return {"sha256": item.sha256, "rows": counts, "internal_only": True}


def inspect_artifacts(root: Path | None = None) -> list[dict[str, Any]]:
    """Verify installed artifacts; a corrupt artifact remains unavailable."""
    root = data_dir() if root is None else root
    result = []
    for item in artifact_manifest(root).artifacts:
        path = _path(root, item.target_path)
        row: dict[str, Any] = {"artifact_id": item.artifact_id, "kind": item.kind}
        try:
            row.update(_verify(item, path, root))
            row["state"] = "verified"
        except FileNotFoundError:
            row["state"] = "missing"
        except (ValueError, KeyError, TypeError, IsADirectoryError, NotADirectoryError):
            row["state"] = "invalid"
        result.append(row)
    return result


def restore_handoff(handoff: Path, *, root: Path | None = None) -> list[dict[str, Any]]:
    """Verify all inputs first, then exclusively publish each immutable artifact.

    Identical existing installations are reused. Different bytes are refused.
    Each artifact is atomically published; an interrupted multi-artifact restore
    may have installed a verified subset and is safe to repeat.
    """
    root = data_dir() if root is None else root
    manifest = artifact_manifest(root)
    for item in manifest.artifacts:
        _verify(item, _path(handoff, item.handoff_path), root)
        target = _path(root, item.target_path)
        if target.exists() and not (
            item.kind == "internal_rasp_bindings" and _empty_directory(target)
        ):
            _verify(item, target, root)
    for item in manifest.artifacts:
        source, target = _path(handoff, item.handoff_path), _path(root, item.target_path)
        if target.exists() and not (
            item.kind == "internal_rasp_bindings" and _empty_directory(target)
        ):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".artifact-", dir=target.parent) as temporary:
            staging = Path(temporary) / "payload"
            if item.kind == "gtfs":
                shutil.copyfile(source, staging)
            else:
                staging.mkdir()
                for name in ("manifest.json", "node_codes.json", "bindings.json"):
                    shutil.copyfile(source / name, staging / name)
            _verify(item, staging, root)
            if item.kind == "gtfs":
                try:
                    os.link(staging, target)  # exclusive, atomic; never replaces an existing file
                except FileExistsError:
                    _verify(item, target, root)  # a concurrent identical publisher may have won
            else:
                _publish_directory(staging, target, lambda path: _verify(item, path, root))
    return inspect_artifacts(root)


def coverage_inventory(root: Path | None = None) -> dict[str, Any]:
    """Source facts for all product kinds; this inventory grants no readiness."""
    root = data_dir() if root is None else root
    cities = []
    for bundle_id in CURRENT_BUNDLES:
        bundle = load_bundle(bundle_id, root=root / "bundles")
        accepted = [d for d in bundle.destinations if d.status == "accepted"]
        nodes = [n for n in bundle.freight_nodes if n.status == "accepted"]
        sources = []
        for version in bundle.manifest.source_versions:
            source_manifest = json.loads(
                _path(root, f"manifests/datasets/{version.dataset_id}.json").read_bytes()
            )
            raw = _path(root / "raw", source_manifest["raw_path"])
            raw_state = "missing"
            if raw.exists():
                raw_state = "verified" if _hash(raw) == version.checksum.value else "invalid"
            sources.append(
                {
                    "dataset_id": version.dataset_id,
                    "sha256": version.checksum.value,
                    "source_url": source_manifest["source_url"],
                    "immutable_raw_state": raw_state,
                }
            )
        cities.append(
            {
                "bundle_id": bundle_id,
                "manifest_sha256": _hash(root / "bundles" / bundle_id / "manifest.json"),
                "sample_points": len(bundle.sample_points),
                "destinations": dict(sorted(Counter(d.category for d in accepted).items())),
                "quarantined_destinations": len(bundle.destinations) - len(accepted),
                "freight_sites": len(bundle.freight_sites),
                "accepted_freight_nodes": dict(sorted(Counter(n.kind for n in nodes).items())),
                "quarantined_freight_nodes": len(bundle.freight_nodes) - len(nodes),
                "timetables": [t.model_dump(mode="json") for t in bundle.manifest.timetables],
                "stop_frequency_records": len(bundle.stop_frequencies),
                "source_coverage": [c.model_dump(mode="json") for c in bundle.manifest.coverage],
                "immutable_sources": sources,
            }
        )
    return {
        "schema_version": "waydex-source-inventory/1",
        "cities": cities,
        "external_artifacts": inspect_artifacts(root),
        "kinds": {
            "urban_passenger": {
                "state": "partial",
                "reasons": [
                    "DESTINATION_COVERAGE_PARTIAL",
                    "WALK_MINIMUM_PROOF_REQUIRED",
                    "POPULATION_WITHOUT_SAMPLE_RETAINED",
                    "STOP_FREQUENCIES_NOT_ROUTABLE",
                ],
            },
            "intercity_passenger": {
                "state": "unknown",
                "reasons": [
                    "NODE_AND_PROVIDER_BINDINGS_ARE_NOT_SCHEDULES",
                    "DATED_SERVICES_REQUIRED",
                    "ACCEPTED_REFERENCE_REQUIRED_FOR_INDEX",
                ],
            },
            "urban_cargo": {
                "state": "partial",
                "reasons": [
                    "CARGO_COMPATIBILITY_UNKNOWN",
                    "TRUCK_ACCESS_NOT_A_ROUTE_PROOF",
                    "FREIGHT_NODE_CATALOGUE_PARTIAL",
                ],
            },
            "intercity_cargo": {
                "state": "unknown",
                "reasons": [
                    "COMPLETE_DELIVERY_CHAINS_NOT_COLLECTED",
                    "CARGO_COMPATIBILITY_UNKNOWN",
                ],
            },
            "pipeline": {
                "state": "unknown",
                "reasons": [
                    "TERMINAL_LOCATION_IS_NOT_DIRECTED_CONNECTION",
                    "PRODUCT_ENDPOINT_ROLES_REQUIRED",
                    "COMPARABLE_VOLUME_CAPACITY_NOT_COLLECTED",
                ],
            },
            "scenario": {
                "state": "unknown",
                "reasons": [
                    "EDITABLE_DATED_PHYSICAL_SERVICES_REQUIRED",
                    "RESOURCE_AND_TURNAROUND_EVIDENCE_REQUIRED",
                ],
            },
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, help="Data root (defaults to A2_DATA_DIR)")
    parser.add_argument(
        "--restore-handoff", type=Path, help="Local handoff directory; no downloads"
    )
    args = parser.parse_args()
    if args.restore_handoff is not None:
        restore_handoff(args.restore_handoff, root=args.root)
    print(json.dumps(coverage_inventory(args.root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
