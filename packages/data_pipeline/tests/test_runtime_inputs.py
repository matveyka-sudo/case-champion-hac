"""Immutable external artifact installation; all fixtures are synthetic."""

import hashlib
import json
import multiprocessing
from pathlib import Path
from typing import Any

import pytest
from a2_data_pipeline.runtime_inputs import (
    DEFAULT_MANIFEST,
    artifact_manifest,
    inspect_artifacts,
    restore_handoff,
)


def write_json(path: Path, value: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value).encode()
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def inputs(tmp_path: Path) -> tuple[Path, Path]:
    root, handoff = tmp_path / "data", tmp_path / "handoff"
    archive = handoff / "gtfs/feed.zip"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"synthetic-archive")
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    write_json(
        root / "manifests/timetable.json", {"sha256": checksum, "path": "processed/feed.zip"}
    )
    binding_root = handoff / "rasp/bindings"
    files = {
        name: {"sha256": write_json(binding_root / name, rows), "rows": len(rows)}
        for name, rows in {
            "bindings.json": [
                {"territory_id": "synthetic-city", "provider_code": "c-test-private"}
            ],
            "node_codes.json": [{"node_id": "synthetic-node", "provider_code": "s-test-private"}],
        }.items()
    }
    reference = {
        "output_id": "synthetic-bindings",
        "source": {"dataset_id": "synthetic-source"},
        "inputs": {"nodes": "synthetic-nodes"},
        "files": files,
    }
    binding_hash = write_json(binding_root / "manifest.json", reference)
    write_json(root / "manifests/bindings.json", reference)
    write_json(
        root / DEFAULT_MANIFEST,
        {
            "schema_version": "waydex-local-artifacts/1",
            "artifacts": [
                {
                    "artifact_id": "synthetic-feed",
                    "kind": "gtfs",
                    "handoff_path": "gtfs/feed.zip",
                    "target_path": "processed/feed.zip",
                    "sha256": checksum,
                    "verification_path": "manifests/timetable.json",
                },
                {
                    "artifact_id": "synthetic-bindings",
                    "kind": "internal_rasp_bindings",
                    "handoff_path": "rasp/bindings",
                    "target_path": "processed/yandex_rasp/bindings",
                    "sha256": binding_hash,
                    "verification_path": "manifests/bindings.json",
                },
            ],
        },
    )
    return root, handoff


def test_restore_is_verified_repeatable_and_reports_no_private_codes(tmp_path: Path) -> None:
    root, handoff = inputs(tmp_path)
    assert {r["state"] for r in inspect_artifacts(root)} == {"missing"}
    first = restore_handoff(handoff, root=root)
    archive = root / "processed/feed.zip"
    modified = archive.stat().st_mtime_ns
    assert {r["state"] for r in first} == {"verified"}
    assert restore_handoff(handoff, root=root) == first
    assert archive.stat().st_mtime_ns == modified
    assert "c-test-private" not in json.dumps(first) and "s-test-private" not in json.dumps(first)


def test_all_handoff_bytes_are_checked_before_publication(tmp_path: Path) -> None:
    root, handoff = inputs(tmp_path)
    (handoff / "rasp/bindings/bindings.json").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="SHA256"):
        restore_handoff(handoff, root=root)
    assert not (root / "processed").exists()


def test_existing_corrupt_artifact_is_never_replaced(tmp_path: Path) -> None:
    root, handoff = inputs(tmp_path)
    restore_handoff(handoff, root=root)
    target = root / "processed/feed.zip"
    target.write_bytes(b"local-different-bytes")
    assert inspect_artifacts(root)[0]["state"] == "invalid"
    with pytest.raises(ValueError, match="SHA256"):
        restore_handoff(handoff, root=root)
    assert target.read_bytes() == b"local-different-bytes"


@pytest.mark.parametrize("relative", ["/tmp/escape", "../escape", "processed/../../escape"])
def test_manifest_rejects_path_escape(tmp_path: Path, relative: str) -> None:
    root, _ = inputs(tmp_path)
    path = root / DEFAULT_MANIFEST
    manifest = json.loads(path.read_bytes())
    manifest["artifacts"][0]["target_path"] = relative
    write_json(path, manifest)
    with pytest.raises(ValueError, match="PROCESSED|RELATIVE"):
        artifact_manifest(root)


def test_manifest_rejects_symlink_escape(tmp_path: Path) -> None:
    root, handoff = inputs(tmp_path)
    (root / "processed").symlink_to(handoff, target_is_directory=True)
    with pytest.raises(ValueError, match="ESCAPES_ROOT"):
        restore_handoff(handoff, root=root)


def test_binding_rows_checked_independently_of_file_hash(tmp_path: Path) -> None:
    root, handoff = inputs(tmp_path)
    path = handoff / "rasp/bindings/manifest.json"
    reference = json.loads(path.read_bytes())
    reference["files"]["bindings.json"]["rows"] = 2
    new_hash = write_json(path, reference)
    write_json(root / "manifests/bindings.json", reference)
    manifest_path = root / DEFAULT_MANIFEST
    manifest = json.loads(manifest_path.read_bytes())
    manifest["artifacts"][1]["sha256"] = new_hash
    write_json(manifest_path, manifest)
    with pytest.raises(ValueError, match="ROW_COUNT"):
        restore_handoff(handoff, root=root)
    assert not (root / "processed").exists()


def _killed_before_directory_rename(root: Path, handoff: Path, ready: Any) -> None:
    from a2_data_pipeline import runtime_inputs as runtime

    original = runtime.os.rename

    def pause(source: Any, target: Any) -> None:
        ready.set()  # the kernel lock is held; the complete staging directory exists
        multiprocessing.Event().wait(30)
        original(source, target)

    runtime.os.rename = pause
    runtime.restore_handoff(handoff, root=root)


def _concurrent_directory_restore(root: Path, handoff: Path, barrier: Any) -> None:
    from a2_data_pipeline import runtime_inputs as runtime

    original = runtime._publish_directory

    def synchronize(staging: Any, target: Any, verify: Any) -> None:
        barrier.wait(timeout=10)  # both processes have complete, verified staging directories
        original(staging, target, verify)

    runtime._publish_directory = synchronize
    runtime.restore_handoff(handoff, root=root)


def test_sigkill_before_publication_leaves_no_final_reservation_and_retry_succeeds(
    tmp_path: Path,
) -> None:
    root, handoff = inputs(tmp_path)
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    process = context.Process(target=_killed_before_directory_rename, args=(root, handoff, ready))
    process.start()
    try:
        assert ready.wait(timeout=10)
        assert not (root / "processed/yandex_rasp/bindings").exists()
        process.kill()
        process.join(timeout=5)
        assert not process.is_alive()
        assert {r["state"] for r in restore_handoff(handoff, root=root)} == {"verified"}
    finally:
        if process.is_alive():
            process.kill()
            process.join(timeout=5)


def test_restore_recovers_an_empty_legacy_reservation(tmp_path: Path) -> None:
    root, handoff = inputs(tmp_path)
    (root / "processed/yandex_rasp/bindings").mkdir(parents=True)
    assert {r["state"] for r in restore_handoff(handoff, root=root)} == {"verified"}


def test_existing_unverified_directory_content_is_never_replaced(tmp_path: Path) -> None:
    root, handoff = inputs(tmp_path)
    target = root / "processed/yandex_rasp/bindings"
    target.mkdir(parents=True)
    (target / "existing.txt").write_bytes(b"preserve")
    with pytest.raises(FileNotFoundError):
        restore_handoff(handoff, root=root)
    assert (target / "existing.txt").read_bytes() == b"preserve"
    assert list(target.iterdir()) == [target / "existing.txt"]


def test_concurrent_directory_restores_both_reuse_the_same_verified_result(tmp_path: Path) -> None:
    root, handoff = inputs(tmp_path)
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    processes = [
        context.Process(target=_concurrent_directory_restore, args=(root, handoff, barrier))
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    try:
        for process in processes:
            process.join(timeout=15)
        assert [process.exitcode for process in processes] == [0, 0]
        assert {r["state"] for r in inspect_artifacts(root)} == {"verified"}
    finally:
        for process in processes:
            if process.is_alive():
                process.kill()
                process.join(timeout=5)
