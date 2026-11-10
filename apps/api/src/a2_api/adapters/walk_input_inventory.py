"""Job-local byte pins for an already semantically validated walking binding."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from a2_contracts.common import canonical_hash
from a2_data_pipeline.bundle import BundleManifest
from a2_data_pipeline.manifest import dataset_manifest_path
from a2_data_pipeline.paths import bundles_dir

from a2_api.adapters.product_runtime import MAX_DOCUMENT_BYTES
from a2_api.core.runtime_budget import runtime_source_budget_bytes

if TYPE_CHECKING:
    from a2_data_pipeline.bundle import BundleData

    from a2_api.adapters.product_runtime import FileRef
    from a2_api.adapters.walk_batches import Approval
    from a2_api.adapters.walk_pilot import Runtime
    from a2_api.adapters.walk_runtime import WalkBinding

_MIB = 1024 * 1024
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")


def _parts(name: str) -> tuple[str, ...]:
    parts = tuple(name.split("/"))
    if not parts or any(part in {"", ".", ".."} for part in parts) or "\\" in name:
        raise ValueError("WALK_INPUT_REFERENCE_OUTSIDE_ROOT")
    return parts


def _open_file(root: Path, parts: tuple[str, ...]) -> int:
    """Use openat/no-follow for every directory and the final regular file."""
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    current = os.open(root.anchor, directory_flags)
    try:
        for part in (*root.parts[1:], *parts[:-1]):
            child = os.open(part, directory_flags, dir_fd=current)
            os.close(current)
            current = child
        return os.open(
            parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=current
        )
    finally:
        os.close(current)


def _identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


@dataclass(frozen=True)
class WalkInputPin:
    root: Path
    parts: tuple[str, ...]
    sha256: str
    size_bytes: int
    limit_bytes: int


class _Reader:
    def __init__(self, timeout_seconds: float) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 15
        ):
            raise ValueError("WALK_INPUT_TIMEOUT_INVALID")
        self.started = time.monotonic()
        self.deadline = self.started + timeout_seconds
        self.remaining = runtime_source_budget_bytes()

    def check_time(self) -> None:
        if time.monotonic() >= self.deadline:
            raise ValueError("WALK_INPUT_REVALIDATION_TIMEOUT")

    def read(self, pin: WalkInputPin, *, content: bool = False, unpinned: bool = False) -> bytes:
        self.check_time()
        if (
            not pin.root.is_absolute()
            or ".." in pin.root.parts
            or pin.parts != _parts("/".join(pin.parts))
        ):
            raise ValueError("WALK_INPUT_REFERENCE_OUTSIDE_ROOT")
        if (
            not 0 <= pin.size_bytes <= pin.limit_bytes <= MAX_DOCUMENT_BYTES
            or (not unpinned and _DIGEST.fullmatch(pin.sha256) is None)
            or pin.size_bytes > self.remaining
        ):
            raise ValueError("WALK_INPUT_BYTE_BUDGET_EXCEEDED")
        descriptor = _open_file(pin.root, pin.parts)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_size != pin.size_bytes:
                raise ValueError("WALK_INPUT_FILE_CHANGED")
            digest = hashlib.sha256()
            chunks = []
            count = 0
            while True:
                self.check_time()
                chunk = os.read(descriptor, min(_MIB, self.remaining + 1))
                if not chunk:
                    break
                count += len(chunk)
                self.remaining -= len(chunk)
                if self.remaining < 0 or count > pin.size_bytes or count > pin.limit_bytes:
                    raise ValueError("WALK_INPUT_BYTE_BUDGET_EXCEEDED")
                digest.update(chunk)
                if content:
                    chunks.append(chunk)
            if count != pin.size_bytes or _identity(before) != _identity(os.fstat(descriptor)):
                raise ValueError("WALK_INPUT_FILE_CHANGED")
            check = _open_file(pin.root, pin.parts)
            try:
                if _identity(before) != _identity(os.fstat(check)):
                    raise ValueError("WALK_INPUT_FILE_CHANGED")
            finally:
                os.close(check)
            if not unpinned and digest.hexdigest() != pin.sha256:
                raise ValueError("WALK_INPUT_FILE_CHANGED")
            self.check_time()
            return b"".join(chunks)
        finally:
            os.close(descriptor)


@dataclass(frozen=True)
class WalkInputInventory:
    pins: tuple[WalkInputPin, ...]
    executor_fingerprint: str
    valid_until: datetime

    def assert_current(
        self, *, executor_fingerprint: str, now: datetime, timeout_seconds: float = 15
    ) -> None:
        if not self.pins or _DIGEST.fullmatch(self.executor_fingerprint) is None:
            raise ValueError("WALK_INPUT_IDENTITY_REQUIRED")
        if executor_fingerprint != self.executor_fingerprint:
            raise ValueError("WALK_EXECUTOR_IMPLEMENTATION_CHANGED")
        if (
            now.utcoffset() is None
            or self.valid_until.utcoffset() is None
            or now >= self.valid_until
        ):
            raise ValueError("WALK_INPUT_APPROVAL_EXPIRED")
        reader = _Reader(timeout_seconds)
        try:
            for pin in self.pins:
                reader.read(pin)
        except OSError as exc:
            raise ValueError("WALK_INPUT_FILE_CHANGED") from exc
        if now + timedelta(seconds=time.monotonic() - reader.started) >= self.valid_until:
            raise ValueError("WALK_INPUT_APPROVAL_EXPIRED")


def _json(raw: bytes) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("WALK_INPUT_DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    def invalid(value: str) -> None:
        raise ValueError("WALK_INPUT_NONFINITE_JSON")

    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)


class _Capture:
    def __init__(self) -> None:
        self.reader = _Reader(15)
        self.pins: list[WalkInputPin] = []

    def add(
        self,
        root: Path,
        name: str,
        expected: str | None,
        *,
        limit: int = MAX_DOCUMENT_BYTES,
        content: bool = False,
        size: int | None = None,
        unhashed: bool = False,
    ) -> bytes:
        if expected is None and not unhashed:
            raise ValueError("WALK_INPUT_HASH_REQUIRED")
        parts = _parts(name)
        # Do not resolve away an initially symlinked root before openat checks it.
        root = root.absolute()
        if ".." in root.parts:
            raise ValueError("WALK_INPUT_REFERENCE_OUTSIDE_ROOT")
        descriptor = _open_file(root, parts)
        try:
            actual_size = os.fstat(descriptor).st_size
        finally:
            os.close(descriptor)
        if size is not None and size != actual_size:
            raise ValueError("WALK_INPUT_FILE_CHANGED")
        pin = WalkInputPin(root, parts, expected or "", actual_size, limit)
        raw = self.reader.read(pin, content=content or expected is None, unpinned=expected is None)
        # Unhashed manifest/binary inputs are checked semantically by the caller
        # before the inventory is returned; every other pin has a trusted hash.
        if expected is None:
            pin = WalkInputPin(root, parts, hashlib.sha256(raw).hexdigest(), actual_size, limit)
        self.pins.append(pin)
        return raw

    def reference(
        self, root: Path, reference: Any, *, limit: int = MAX_DOCUMENT_BYTES, content: bool = False
    ) -> bytes:
        name = reference["path"] if isinstance(reference, Mapping) else reference.path
        expected = reference["sha256"] if isinstance(reference, Mapping) else reference.sha256
        return self.add(root, name, expected, limit=limit, content=content)


def capture_walk_inventory(
    *,
    binding_root: Path,
    binding_ref: FileRef,
    binding: WalkBinding,
    approval_root: Path,
    approval: Approval,
    runtime: Runtime,
    bundle: BundleData,
    graph: Mapping[str, Any],
    executor_fingerprint: str,
) -> WalkInputInventory:
    """Capture only after the caller has completed every semantic/source check."""
    if _DIGEST.fullmatch(executor_fingerprint) is None or approval.valid_until.utcoffset() is None:
        raise ValueError("WALK_INPUT_IDENTITY_REQUIRED")
    if (
        bundle.manifest.bundle_id != runtime.bundle_id
        or canonical_hash(bundle.manifest.model_dump(mode="json")) != approval.bundle_manifest_hash
        or binding.artifact.sha256 != runtime.digest
    ):
        raise ValueError("WALK_INPUT_VALIDATED_SOURCE_MISMATCH")
    capture = _Capture()
    capture.reference(binding_root, binding_ref, limit=256 * 1024)
    artifact_root = binding_root.joinpath(*_parts(binding_ref.path)).parent
    capture.reference(artifact_root, binding.artifact)
    capture.reference(artifact_root, binding.approval, limit=8 * _MIB)
    map_features = getattr(binding, "map_features", None)
    if map_features is not None:
        capture.reference(artifact_root, map_features)
    graph_ref = approval.routing_binding.graph_manifest
    capture.reference(approval_root, graph_ref, limit=8 * _MIB)
    graph_root = approval_root.joinpath(*_parts(graph_ref.path)).parent
    if len(_parts(runtime.bundle_id)) != 1:
        raise ValueError("WALK_INPUT_REFERENCE_OUTSIDE_ROOT")
    producer_root = bundles_dir() / runtime.bundle_id
    raw = capture.add(producer_root, "manifest.json", None, limit=_MIB, content=True, unhashed=True)
    current = BundleManifest.model_validate(_json(raw))
    if canonical_hash(current.model_dump(mode="json")) != approval.bundle_manifest_hash:
        raise ValueError("ACCEPTED_BUNDLE_MANIFEST_CHANGED")
    for reference in bundle.manifest.files.values():
        capture.reference(producer_root, reference)
    markers = {"derivation", "upstream_source", "tool_installation"}
    if "source_manifest_sha256" in graph:
        sources = [
            row
            for row in bundle.manifest.source_versions
            if runtime.build.source_graph_version.startswith(row.dataset_id + ":")
        ]
        if len(sources) != 1:
            raise ValueError("WALK_INPUT_SOURCE_VERSION_REQUIRED")
        registered = dataset_manifest_path(sources[0].dataset_id)
        source_hash = graph["source_manifest_sha256"]
        capture.add(registered.parent, registered.name, source_hash, limit=_MIB)
        capture.add(graph_root, "source-manifest.json", source_hash, limit=_MIB)
    elif markers & graph.keys():
        raise ValueError("DERIVED_GRAPH_SOURCE_MANIFEST_PIN_REQUIRED")
    # Reviewed legacy original-PBF graphs have no source-manifest FileRef.
    # Their producer checksum/graph binding was already checked by load_binding;
    # do not invent a new authority or rewrite that original graph manifest.
    if markers & graph.keys():
        if not markers <= graph.keys():
            raise ValueError("DERIVED_GRAPH_COMPLETE_PROVENANCE_REQUIRED")
        receipt = _json(
            capture.reference(graph_root, graph["derivation"], limit=_MIB, content=True)
        )
        tool = _json(
            capture.reference(graph_root, graph["tool_installation"], limit=_MIB, content=True)
        )
        for name, key, limit in (
            ("restriction-scope.osm.pbf", "restriction_scope_sha256", 8 * _MIB),
            ("way-refs-check.txt", "way_refs_report_sha256", 128 * 1024),
            ("restriction-refs-check.txt", "restriction_refs_report_sha256", 128 * 1024),
            ("extract-fileinfo.json", "fileinfo_sha256", _MIB),
        ):
            capture.add(graph_root, name, receipt[key], limit=limit)
        inventory = _json(
            capture.add(
                graph_root,
                "endpoint-inventory.json",
                receipt["endpoint_inventory_sha256"],
                limit=8 * _MIB,
                content=True,
            )
        )
        old_id = inventory["snapshot_id"]
        old_parts = _parts(old_id)
        if len(old_parts) != 1:
            raise ValueError("DERIVED_GRAPH_SOURCE_SNAPSHOT_OUTSIDE_REGISTRY")
        capture.add(
            bundles_dir() / old_id,
            "manifest.json",
            inventory["bundle_manifest_sha256"],
            limit=_MIB,
        )
        binary = capture.add(
            graph_root, "tool-binary-sha256.txt", None, limit=128, content=True, unhashed=True
        )
        if binary.decode().split() != [receipt["tool_binary_sha256"], "/usr/bin/osmium"]:
            raise ValueError("DERIVED_GRAPH_TOOL_BINDING_CHANGED")
        if len(tool["packages"]) != 2:
            raise ValueError("DERIVED_GRAPH_TOOL_PACKAGE_BINDING_CHANGED")
        for package in tool["packages"]:
            if len(_parts(package["path"])) != 1:
                raise ValueError("DERIVED_GRAPH_TOOL_PACKAGE_BINDING_CHANGED")
            capture.add(
                graph_root,
                "tool-packages/" + package["path"],
                package["sha256"],
                limit=8 * _MIB,
                size=package["bytes"],
            )
    result = WalkInputInventory(tuple(capture.pins), executor_fingerprint, approval.valid_until)
    result.assert_current(executor_fingerprint=executor_fingerprint, now=datetime.now(UTC))
    return result
