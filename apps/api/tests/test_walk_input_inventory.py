"""Byte revalidation cannot bless replacement inputs or redo semantic parsing."""

import hashlib
import json
import os
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from itertools import count
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from a2_api.adapters import walk_input_inventory as inventory
from a2_api.adapters.product_runtime import FileRef
from a2_contracts.common import canonical_hash
from a2_data_pipeline.bundle import BundleManifest
from a2_data_pipeline.bundle import FileRef as ProducerFileRef

EXECUTOR = "a" * 64
NOW = datetime.now(UTC)


def _write(root: Path, name: str, value: bytes | dict[str, Any]) -> FileRef:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = value if isinstance(value, bytes) else json.dumps(value).encode()
    path.write_bytes(raw)
    return FileRef(path=name, sha256=hashlib.sha256(raw).hexdigest())


class ValidatedInputs:
    """Small post-semantic object substitutes; the full loader owns semantics."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.root = tmp_path / "runtime"
        self.producers = tmp_path / "producers"
        template = Path(__file__).resolve().parents[3] / "data/bundles"
        original = BundleManifest.model_validate_json(
            next(iter(sorted(template.glob("*/manifest.json")))).read_bytes()
        )
        self.producer = self.producers / original.bundle_id
        self.data_ref = _write(self.producer, "tables/points.json", b"[]")
        self.manifest = original.model_copy(
            update={
                "files": {
                    "sample_points": ProducerFileRef(
                        path=self.data_ref.path, sha256=self.data_ref.sha256, rows=0
                    )
                }
            }
        )
        _write(self.producer, "manifest.json", self.manifest.model_dump(mode="json"))
        source = _write(tmp_path / "registered", "source.json", {"accepted": True})
        _write(self.root, "graph/source-manifest.json", {"accepted": True})
        self.graph: dict[str, Any] = {"source_manifest_sha256": source.sha256}
        monkeypatch.setattr(inventory, "bundles_dir", lambda: self.producers)
        monkeypatch.setattr(
            inventory, "dataset_manifest_path", lambda _: tmp_path / "registered/source.json"
        )
        self.runtime = SimpleNamespace(
            bundle_id=self.manifest.bundle_id,
            build=SimpleNamespace(
                source_graph_version=self.manifest.source_versions[0].dataset_id + ":walk"
            ),
        )
        self.approval = SimpleNamespace(
            valid_until=NOW + timedelta(hours=1),
            bundle_manifest_hash=canonical_hash(self.manifest.model_dump(mode="json")),
            routing_binding=SimpleNamespace(),
        )
        self.binding = SimpleNamespace(
            artifact=_write(self.root, "artifact.json", {"already_validated": True}),
            approval=_write(self.root, "approval.json", {"already_validated": True}),
            map_features=None,
        )
        self.runtime.digest = self.binding.artifact.sha256
        self.bundle = SimpleNamespace(manifest=self.manifest)
        self.refresh_graph()

    def refresh_graph(self) -> None:
        self.approval.routing_binding.graph_manifest = _write(
            self.root, "graph/valhalla-build-manifest.json", self.graph
        )
        self.binding_ref = _write(
            self.root,
            "binding.json",
            {
                "artifact": self.binding.artifact.model_dump(),
                "approval": self.binding.approval.model_dump(),
            },
        )

    def derive(self) -> None:
        graph = self.root / "graph"
        receipt: dict[str, Any] = {"tool_binary_sha256": "b" * 64}
        for name, key in (
            ("restriction-scope.osm.pbf", "restriction_scope_sha256"),
            ("way-refs-check.txt", "way_refs_report_sha256"),
            ("restriction-refs-check.txt", "restriction_refs_report_sha256"),
            ("extract-fileinfo.json", "fileinfo_sha256"),
        ):
            receipt[key] = _write(graph, name, b"reviewed bytes").sha256
        old = _write(self.producers / "old-snapshot", "manifest.json", {"reviewed": True})
        endpoint = _write(
            graph,
            "endpoint-inventory.json",
            {"snapshot_id": "old-snapshot", "bundle_manifest_sha256": old.sha256},
        )
        receipt["endpoint_inventory_sha256"] = endpoint.sha256
        packages = []
        for name in ("tool.deb", "dependency.deb"):
            ref = _write(graph, "tool-packages/" + name, b"reviewed package")
            packages.append({"path": name, "sha256": ref.sha256, "bytes": 16})
        self.graph.update(
            {
                "derivation": _write(graph, "extract-receipt.json", receipt).model_dump(),
                "tool_installation": _write(
                    graph, "tool-install-receipt.json", {"packages": packages}
                ).model_dump(),
                "upstream_source": {"reviewed": True},
            }
        )
        _write(graph, "tool-binary-sha256.txt", ("b" * 64 + "  /usr/bin/osmium\n").encode())
        self.refresh_graph()

    def capture(self) -> inventory.WalkInputInventory:
        return inventory.capture_walk_inventory(
            binding_root=self.root,
            binding_ref=self.binding_ref,
            binding=cast(Any, self.binding),
            approval_root=self.root,
            approval=cast(Any, self.approval),
            runtime=cast(Any, self.runtime),
            bundle=cast(Any, self.bundle),
            graph=self.graph,
            executor_fingerprint=EXECUTOR,
        )


@pytest.fixture
def inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ValidatedInputs:
    return ValidatedInputs(tmp_path, monkeypatch)


def test_revalidation_does_not_parse_json(
    inputs: ValidatedInputs, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs.derive()
    pinned = inputs.capture()
    monkeypatch.setattr(inventory, "_json", lambda _: pytest.fail("per-chunk JSON parsing"))
    pinned.assert_current(executor_fingerprint=EXECUTOR, now=NOW)
    with pytest.raises(FrozenInstanceError):
        pinned.executor_fingerprint = "c" * 64  # type: ignore[misc]


@pytest.mark.parametrize(
    "name",
    [
        "binding.json",
        "artifact.json",
        "approval.json",
        "graph/valhalla-build-manifest.json",
        "graph/source-manifest.json",
    ],
)
def test_same_size_metadata_replacement_fails(inputs: ValidatedInputs, name: str) -> None:
    pinned = inputs.capture()
    file = inputs.root / name
    raw = file.read_bytes()
    file.write_bytes(b"x" * len(raw))
    with pytest.raises(ValueError, match="CHANGED"):
        pinned.assert_current(executor_fingerprint=EXECUTOR, now=NOW)


@pytest.mark.parametrize("kind", ["content", "grow", "truncate", "missing", "symlink", "fifo"])
def test_producer_replacement_fails(inputs: ValidatedInputs, kind: str) -> None:
    pinned = inputs.capture()
    file = inputs.producer / inputs.data_ref.path
    if kind == "content":
        file.write_bytes(b"{}")
    elif kind == "grow":
        file.write_bytes(b"[] ")
    elif kind == "truncate":
        file.write_bytes(b"")
    else:
        old = file.with_suffix(".preserved")
        file.rename(old)
        if kind == "symlink":
            file.symlink_to(old)
        elif kind == "fifo":
            os.mkfifo(file)
    with pytest.raises(ValueError, match="CHANGED"):
        pinned.assert_current(executor_fingerprint=EXECUTOR, now=NOW)


def test_ancestor_symlink_even_with_identical_bytes_fails(inputs: ValidatedInputs) -> None:
    pinned = inputs.capture()
    original = inputs.producer / "tables"
    saved = inputs.producer / "preserved-tables"
    original.rename(saved)
    original.symlink_to(saved, target_is_directory=True)
    with pytest.raises(ValueError, match="CHANGED"):
        pinned.assert_current(executor_fingerprint=EXECUTOR, now=NOW)


def test_mutation_between_validation_and_capture_fails(inputs: ValidatedInputs) -> None:
    (inputs.producer / inputs.data_ref.path).write_bytes(b"{}")
    with pytest.raises(ValueError, match="CHANGED"):
        inputs.capture()


def test_producer_manifest_semantic_change_before_capture_fails(inputs: ValidatedInputs) -> None:
    changed = inputs.manifest.model_dump(mode="json")
    changed["parser_version"] += "-different"
    _write(inputs.producer, "manifest.json", changed)
    with pytest.raises(ValueError, match="MANIFEST_CHANGED"):
        inputs.capture()


@pytest.mark.parametrize(
    "name",
    [
        "restriction-scope.osm.pbf",
        "tool-packages/tool.deb",
        "tool-binary-sha256.txt",
        "way-refs-check.txt",
        "restriction-refs-check.txt",
        "extract-fileinfo.json",
        "endpoint-inventory.json",
        "extract-receipt.json",
        "tool-install-receipt.json",
    ],
)
def test_every_derived_auxiliary_remains_pinned(inputs: ValidatedInputs, name: str) -> None:
    inputs.derive()
    pinned = inputs.capture()
    file = inputs.root / "graph" / name
    file.write_bytes(b"x" * file.stat().st_size)
    with pytest.raises(ValueError, match="CHANGED"):
        pinned.assert_current(executor_fingerprint=EXECUTOR, now=NOW)


@pytest.mark.parametrize("which", ["registered", "original"])
def test_registered_source_and_original_snapshot_remain_pinned(
    inputs: ValidatedInputs, which: str
) -> None:
    inputs.derive()
    pinned = inputs.capture()
    path = (
        inputs.producers / "old-snapshot/manifest.json"
        if which == "original"
        else inputs.root.parent / "registered/source.json"
    )
    path.write_text("{}")
    with pytest.raises(ValueError, match="CHANGED"):
        pinned.assert_current(executor_fingerprint=EXECUTOR, now=NOW)


def test_executor_and_expiry_checked_every_time(inputs: ValidatedInputs) -> None:
    pinned = inputs.capture()
    with pytest.raises(ValueError, match="IMPLEMENTATION_CHANGED"):
        pinned.assert_current(executor_fingerprint="c" * 64, now=NOW)
    with pytest.raises(ValueError, match="EXPIRED"):
        pinned.assert_current(executor_fingerprint=EXECUTOR, now=pinned.valid_until)
    with pytest.raises(ValueError, match="EXPIRED"):
        pinned.assert_current(executor_fingerprint=EXECUTOR, now=datetime.now())


def test_optional_map_selector_is_pinned(inputs: ValidatedInputs) -> None:
    inputs.binding.map_features = _write(inputs.root, "map.json", {"reviewed": True})
    pinned = inputs.capture()
    (inputs.root / "map.json").write_bytes(b"{}")
    with pytest.raises(ValueError, match="CHANGED"):
        pinned.assert_current(executor_fingerprint=EXECUTOR, now=NOW)


def _inventory_for(root: Path, data: bytes) -> inventory.WalkInputInventory:
    reference = _write(root, "bytes", data)
    pin = inventory.WalkInputPin(
        root.resolve(), ("bytes",), reference.sha256, len(data), 32 * 1024 * 1024
    )
    return inventory.WalkInputInventory((pin,), EXECUTOR, NOW + timedelta(hours=1))


def test_streamed_read_detects_atomic_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pinned = _inventory_for(tmp_path, b"a" * (2 * 1024 * 1024))
    original = os.read
    changed = False

    def mutate(descriptor: int, count: int) -> bytes:
        nonlocal changed
        result = original(descriptor, count)
        if not changed:
            changed = True
            replacement = tmp_path / "replacement"
            replacement.write_bytes(b"b" * (2 * 1024 * 1024))
            os.replace(replacement, tmp_path / "bytes")
        return result

    monkeypatch.setattr(inventory.os, "read", mutate)
    with pytest.raises(ValueError, match="CHANGED"):
        pinned.assert_current(executor_fingerprint=EXECUTOR, now=NOW)


def test_exact_cumulative_byte_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pinned = _inventory_for(tmp_path, b"12345678")
    monkeypatch.setattr(inventory, "MAX_RUNTIME_BYTES", 8)
    pinned.assert_current(executor_fingerprint=EXECUTOR, now=NOW)
    monkeypatch.setattr(inventory, "MAX_RUNTIME_BYTES", 7)
    with pytest.raises(ValueError, match="BYTE_BUDGET"):
        pinned.assert_current(executor_fingerprint=EXECUTOR, now=NOW)


def test_per_document_budget_cannot_widen(tmp_path: Path) -> None:
    pin = inventory.WalkInputPin(tmp_path, ("unused",), "a" * 64, 1, 32 * 1024 * 1024 + 1)
    pinned = inventory.WalkInputInventory((pin,), EXECUTOR, NOW + timedelta(hours=1))
    with pytest.raises(ValueError, match="BYTE_BUDGET"):
        pinned.assert_current(executor_fingerprint=EXECUTOR, now=NOW)


@pytest.mark.parametrize("timeout", [0, -1, 16, float("inf"), float("nan")])
def test_invalid_deadline_fails(inputs: ValidatedInputs, timeout: float) -> None:
    with pytest.raises(ValueError, match="TIMEOUT_INVALID"):
        inputs.capture().assert_current(
            executor_fingerprint=EXECUTOR, now=NOW, timeout_seconds=timeout
        )


def test_elapsed_deadline_fails(inputs: ValidatedInputs, monkeypatch: pytest.MonkeyPatch) -> None:
    pinned = inputs.capture()
    ticks = iter([0.0, 16.0])
    monkeypatch.setattr(inventory.time, "monotonic", lambda: next(ticks))
    with pytest.raises(ValueError, match="TIMEOUT"):
        pinned.assert_current(executor_fingerprint=EXECUTOR, now=NOW)


def test_null_declared_hash_cannot_be_blindly_pinned(inputs: ValidatedInputs) -> None:
    inputs.graph["source_manifest_sha256"] = None
    inputs.refresh_graph()
    with pytest.raises(ValueError, match="HASH_REQUIRED"):
        inputs.capture()


def test_missing_derived_marker_cannot_skip_inventory(inputs: ValidatedInputs) -> None:
    inputs.graph["derivation"] = {"path": "unused", "sha256": "b" * 64}
    inputs.refresh_graph()
    with pytest.raises(ValueError, match="COMPLETE_PROVENANCE"):
        inputs.capture()


def test_reference_escape_rejected(inputs: ValidatedInputs) -> None:
    inputs.binding.artifact = FileRef(path="../artifact.json", sha256=inputs.runtime.digest)
    with pytest.raises(ValueError, match="OUTSIDE_ROOT"):
        inputs.capture()


def test_root_directory_replacement_by_symlink_fails(inputs: ValidatedInputs) -> None:
    pinned = inputs.capture()
    saved = inputs.producer.with_name("preserved-producer")
    inputs.producer.rename(saved)
    inputs.producer.symlink_to(saved, target_is_directory=True)
    with pytest.raises(ValueError, match="CHANGED"):
        pinned.assert_current(executor_fingerprint=EXECUTOR, now=NOW)


def test_duplicate_producer_header_key_rejected(inputs: ValidatedInputs) -> None:
    file = inputs.producer / "manifest.json"
    raw = file.read_text()
    file.write_text('{"parser_version":"hidden",' + raw[1:])
    with pytest.raises(ValueError, match="DUPLICATE_JSON"):
        inputs.capture()


def test_initial_root_symlink_is_not_resolved_away(inputs: ValidatedInputs) -> None:
    saved = inputs.producer.with_name("preserved-producer")
    inputs.producer.rename(saved)
    inputs.producer.symlink_to(saved, target_is_directory=True)
    with pytest.raises((OSError, ValueError)):
        inputs.capture()


def test_expiry_during_streaming_is_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pinned = replace(_inventory_for(tmp_path, b"data"), valid_until=NOW + timedelta(seconds=0.5))
    ticks = count()
    monkeypatch.setattr(inventory.time, "monotonic", lambda: float(next(ticks)))
    with pytest.raises(ValueError, match="EXPIRED"):
        pinned.assert_current(executor_fingerprint=EXECUTOR, now=NOW)


def test_empty_inventory_cannot_claim_validation() -> None:
    pinned = inventory.WalkInputInventory((), EXECUTOR, NOW + timedelta(hours=1))
    with pytest.raises(ValueError, match="IDENTITY_REQUIRED"):
        pinned.assert_current(executor_fingerprint=EXECUTOR, now=NOW)


def test_reviewed_legacy_original_graph_needs_no_invented_source_pin(
    inputs: ValidatedInputs,
) -> None:
    inputs.graph.pop("source_manifest_sha256")
    inputs.refresh_graph()
    pinned = inputs.capture()
    # These files were never references of the approved legacy graph.
    (inputs.root.parent / "registered/source.json").write_text("unreferenced")
    (inputs.root / "graph/source-manifest.json").write_text("unreferenced")
    pinned.assert_current(executor_fingerprint=EXECUTOR, now=NOW)
    assert not any(pin.parts[-1] == "source-manifest.json" for pin in pinned.pins)


def test_derived_graph_cannot_use_legacy_missing_source_pin(inputs: ValidatedInputs) -> None:
    inputs.derive()
    inputs.graph.pop("source_manifest_sha256")
    inputs.refresh_graph()
    with pytest.raises(ValueError, match="SOURCE_MANIFEST_PIN_REQUIRED"):
        inputs.capture()
