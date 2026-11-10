"""Trusted per-profile walking inputs; cities never select code or provider URLs."""

from __future__ import annotations

import hashlib
import importlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal

from a2_contracts.common import canonical_hash
from a2_contracts.v2.base import Category, ContractModel, Identifier
from a2_contracts.v2.dataset import AcceptedData
from a2_contracts.v2.results import EvidenceRef
from a2_data_pipeline.bundle import BundleData, BundleManifest
from a2_data_pipeline.contract_v2 import to_accepted_data_v2
from a2_data_pipeline.paths import bundles_dir
from a2_data_pipeline.store import BundleError, load_bundle
from a2_engine import prepare_walk_inputs
from a2_engine.urban.valhalla.models import (
    ValhallaBuild,
    ValhallaCorrelation,
    WalkConnector,
    WalkCoordinate,
)
from pydantic import Field, HttpUrl, TypeAdapter

from a2_api.adapters import walk_pilot
from a2_api.adapters.product_runtime import MAX_RUNTIME_BYTES, FileRef, read_document
from a2_api.core.registry import ProductRegistry
from a2_api.core.walk_observations import observation_directory

if TYPE_CHECKING:
    from a2_api.adapters.walk_batches import Approval


class WalkBinding(ContractModel):
    schema_version: Literal["waydex-urban-walk-binding/1"]
    artifact: FileRef
    approval: FileRef
    route_executor: Literal["valhalla_http_observation/1"]


class CorrelationInput(ContractModel):
    access_node_id: Identifier
    source_location: WalkCoordinate
    correlated_location: WalkCoordinate
    correlation_id: Identifier
    evidence_refs: list[Identifier] = Field(min_length=1, max_length=64)
    coordinate_precision_digits: Literal[6] | None = None
    serialization_evidence_refs: list[Identifier] = Field(default_factory=list, max_length=64)


class StatusExpectation(ContractModel):
    version: Literal["3.5.1"]
    tileset_last_modified: int = Field(strict=True, ge=0)
    has_tiles: Literal[True] | None = None


class RuntimeArtifact(ContractModel):
    schema_version: Literal["waydex-urban-walk-runtime/1"]
    snapshot_id: Identifier
    city_id: Identifier
    endpoint_url: HttpUrl
    point_ids: list[Identifier] = Field(min_length=1, max_length=4096)
    destination_ids: list[Identifier] = Field(min_length=1, max_length=4096)
    build: ValhallaBuild
    connectors: list[WalkConnector] = Field(max_length=16384)
    correlations: list[CorrelationInput] = Field(max_length=16384)
    evidence_refs: list[EvidenceRef] = Field(max_length=4096)
    status_expectation: StatusExpectation


@dataclass(frozen=True)
class WalkProfileScope:
    profile_id: str
    city_id: str
    snapshot_id: str
    snapshot_hash: str
    categories: tuple[str, ...]
    graph_fingerprint: str
    routing_profile_fingerprint: str
    valid_from: datetime
    valid_to: datetime
    max_route_pairs: int


@dataclass(frozen=True)
class BoundWalkBinding:
    root: Path
    reference: FileRef
    scope: WalkProfileScope
    binding: WalkBinding
    approval: Approval
    approval_sha256: str
    runtime: walk_pilot.Runtime
    executor_fingerprint: str
    resolved_pairs: frozenset[tuple[str, str]]
    destination_names: Mapping[str, str]
    graph_limitations: tuple[str, ...] = ()
    derivation_snapshot_id: str | None = None

    @property
    def binding_sha256(self) -> str:
        return self.reference.sha256


def executor_fingerprint() -> str:
    """Fixed server implementation, never a caller-supplied attestation."""
    modules = (
        "a2_api.adapters.walk_runtime",
        "a2_api.adapters.walk_graph_source",
        "a2_api.adapters.walk_batches",
        "a2_api.adapters.walk_observed",
        "a2_engine.urban.walk.observed",
        "a2_api.adapters.walk_pilot",
        "a2_engine.urban.walk.inputs",
        "a2_engine.urban.walk.analysis",
        "a2_engine.urban.valhalla.requests",
        "a2_engine.urban.valhalla.responses",
        "a2_data_pipeline.contract_v2",
        "a2_data_pipeline.walk_projection",
    )
    files = {}
    for name in modules:
        source = importlib.import_module(name).__file__
        if source is None:
            raise ValueError("WALK_EXECUTOR_SOURCE_IDENTITY_REQUIRED")
        files[name] = hashlib.sha256(Path(source).read_bytes()).hexdigest()
    return canonical_hash({"executor": "valhalla_http_observation/1", "implementation": files})


def _read(root: Path, reference: FileRef, budget: list[int], limit: int) -> tuple[Path, object]:
    path = (root / reference.path).resolve()
    if not path.is_relative_to(root.resolve()) or path.stat().st_size > limit:
        raise ValueError("WALK_REFERENCE_OUTSIDE_ROOT_OR_BYTE_BUDGET")
    return path, read_document(path, reference.sha256, budget)


def validate_approval_sources(
    approval: Approval,
    runtime: walk_pilot.Runtime,
    *,
    root: Path,
    budget: list[int] | None = None,
    graph_out: dict[str, Any] | None = None,
) -> BundleData:
    """Keep legacy and per-profile approvals on one graph/provenance validator."""
    binding = approval.routing_binding
    _, graph = _read(root, binding.graph_manifest, budget or [MAX_RUNTIME_BYTES], 8 * 1024 * 1024)
    if not isinstance(graph, dict):
        raise ValueError("WALK_GRAPH_MANIFEST_REQUIRED")
    if graph_out is not None:
        graph_out.update(graph)
    tiles = graph.get("files", [])
    if (
        not isinstance(tiles, list)
        or not tiles
        or any(
            not isinstance(row, dict)
            or not str(row.get("path", "")).startswith("tiles/")
            or len(str(row.get("sha256", ""))) != 64
            or type(row.get("bytes")) is not int
            or row["bytes"] <= 0
            for row in tiles
        )
        or len({row["path"] for row in tiles}) != len(tiles)
        or binding.tile_manifest_sha256
        != canonical_hash(sorted(tiles, key=lambda row: row["path"]))
        or binding.source_pbf_sha256 != graph.get("source_sha256")
        or binding.build_configuration_hash != graph.get("config_sha256")
        or binding.build_image_digest
        not in (graph.get("image_id"), *graph.get("image_repo_digests", []))
    ):
        raise ValueError("ACTUAL_ROUTING_BUILD_MANIFEST_BINDING_MISMATCH")
    if (
        runtime.digest != approval.runtime_artifact_hash
        or runtime.bundle_id != approval.snapshot_id
        or binding.runtime_build_id != runtime.build.build_id
        or binding.source_graph_version != runtime.build.source_graph_version
        or binding.endpoint_url.rstrip("/") != runtime.endpoint_url
        or binding.profile_fingerprint != runtime.build.profile_hash
        or not runtime.build.graph_version.endswith(binding.graph_manifest.sha256)
        or datetime.now(UTC) >= approval.valid_until
    ):
        raise ValueError("WALK_BATCH_ACTUAL_BUILD_PROFILE_OR_VALIDITY_MISMATCH")
    remaining = budget if budget is not None else [MAX_RUNTIME_BYTES]
    source_root = (bundles_dir() / runtime.bundle_id).resolve()
    with (source_root / "manifest.json").open("rb") as source:
        header = source.read(1024 * 1024 + 1)
    if len(header) > 1024 * 1024:
        raise ValueError("WALK_SOURCE_BUNDLE_BYTE_BUDGET_EXCEEDED")
    manifest = BundleManifest.model_validate_json(header)
    if len(header) > remaining[0]:
        raise ValueError("WALK_SOURCE_BUNDLE_BYTE_BUDGET_EXCEEDED")
    remaining[0] -= len(header)
    for reference in manifest.files.values():
        file = (source_root / reference.path).resolve()
        if not file.is_relative_to(source_root):
            raise ValueError("WALK_SOURCE_BUNDLE_FILE_CHANGED")
        digest = hashlib.sha256()
        with file.open("rb") as source:
            while chunk := source.read(min(1024 * 1024, remaining[0] + 1)):
                remaining[0] -= len(chunk)
                if remaining[0] < 0:
                    raise ValueError("WALK_SOURCE_BUNDLE_BYTE_BUDGET_EXCEEDED")
                digest.update(chunk)
        if digest.hexdigest() != reference.sha256:
            raise ValueError("WALK_SOURCE_BUNDLE_FILE_CHANGED")
    if canonical_hash(manifest.model_dump(mode="json")) != approval.bundle_manifest_hash:
        raise ValueError("ACCEPTED_BUNDLE_MANIFEST_CHANGED")
    try:
        bundle = load_bundle(runtime.bundle_id)
    except BundleError as exc:
        raise ValueError("IMMUTABLE_WALK_SOURCE_BUNDLE_UNAVAILABLE") from exc
    if canonical_hash(bundle.manifest.model_dump(mode="json")) != approval.bundle_manifest_hash:
        raise ValueError("ACCEPTED_BUNDLE_MANIFEST_CHANGED")
    return bundle


def load_binding(
    root: Path,
    reference: FileRef,
    *,
    snapshot: AcceptedData,
    scope: WalkProfileScope,
    budget: list[int] | None = None,
) -> BoundWalkBinding:
    from a2_api.adapters.walk_batches import Approval

    remaining = budget if budget is not None else [MAX_RUNTIME_BYTES]
    path, value = _read(root, reference, remaining, 256 * 1024)
    binding = WalkBinding.model_validate(value)
    _, value = _read(path.parent, binding.artifact, remaining, walk_pilot.MAX_ARTIFACT_BYTES)
    artifact = RuntimeArtifact.model_validate(value)
    approval_path, value = _read(path.parent, binding.approval, remaining, 8 * 1024 * 1024)
    approval = Approval.model_validate(value)
    categories = TypeAdapter(list[Category]).validate_python(list(scope.categories))
    pairs = {(p.point_id, p.destination_id) for p in approval.pairs}
    if (
        not categories
        or tuple(sorted(set(categories))) != scope.categories
        or approval.categories != list(scope.categories)
        or scope.valid_from >= scope.valid_to
        or scope.snapshot_id != snapshot.snapshot_id
        or scope.snapshot_hash != canonical_hash(snapshot.model_dump(mode="json"))
        or approval.snapshot_id != scope.snapshot_id
        or approval.snapshot_hash != scope.snapshot_hash
        or artifact.snapshot_id != scope.snapshot_id
        or artifact.city_id != scope.city_id
        or approval.city_id != scope.city_id
        or scope.city_id not in snapshot.geographic_scope
        or artifact.build.snapshot_id != scope.snapshot_id
        or artifact.build.source_graph_version != snapshot.provenance.graph_version
        or approval.runtime_artifact_hash != binding.artifact.sha256
        or scope.graph_fingerprint != approval.routing_binding.graph_manifest.sha256
        or scope.routing_profile_fingerprint != artifact.build.profile_hash
        or len(pairs) != len(approval.pairs)
        or len(pairs) > scope.max_route_pairs
        or len(set(artifact.point_ids)) != len(artifact.point_ids)
        or len(set(artifact.destination_ids)) != len(artifact.destination_ids)
        or set(artifact.point_ids) != {p for p, _ in pairs}
        or set(artifact.destination_ids) != {d for _, d in pairs}
    ):
        raise ValueError("WALK_PROFILE_SNAPSHOT_CITY_CATEGORY_OR_PAIR_BINDING_MISMATCH")
    points = {p.point_id for p in snapshot.sample_points if p.territory_id == scope.city_id}
    destinations = {
        d.destination_id: d for d in snapshot.destinations if d.territory_id == scope.city_id
    }
    if not set(artifact.point_ids) <= points or any(
        identity not in destinations or not set(destinations[identity].categories) & set(categories)
        for identity in artifact.destination_ids
    ):
        raise ValueError("WALK_APPROVED_ENDPOINT_OUTSIDE_CANONICAL_SCOPE")
    options = json.loads(artifact.build.pedestrian_options_json)
    if (
        artifact.build.profile_version != "waydex-walk-static-5kmh-v1"
        or options.get("walking_speed") != 5
        or options.get("type") != "foot"
    ):
        raise ValueError("WALK_APPROVED_5_KMH_FOOT_MODEL_REQUIRED")
    correlations = tuple(
        ValhallaCorrelation(
            build=artifact.build,
            access_node_id=row.access_node_id,
            source_location=row.source_location,
            correlated_location=row.correlated_location,
            correlation_id=row.correlation_id,
            evidence_refs=tuple(row.evidence_refs),
            coordinate_precision_digits=row.coordinate_precision_digits,
            serialization_evidence_refs=tuple(row.serialization_evidence_refs),
        )
        for row in artifact.correlations
    )
    runtime = walk_pilot.Runtime(
        binding.artifact.sha256,
        artifact.snapshot_id,
        str(artifact.endpoint_url).rstrip("/"),
        tuple(artifact.point_ids),
        tuple(artifact.destination_ids),
        artifact.build,
        tuple(artifact.connectors),
        correlations,
        tuple(artifact.evidence_refs),
        observation_directory(),
        artifact.status_expectation.model_dump(exclude_none=True),
    )
    graph: dict[str, Any] = {}
    bundle = validate_approval_sources(
        approval, runtime, root=approval_path.parent, budget=remaining, graph_out=graph
    )
    # This public pure projection is the same source rule used by ML2's producer.
    # Request data can neither manufacture connector times nor promote unknowns.
    sources = [
        row
        for row in bundle.manifest.source_versions
        if runtime.build.source_graph_version.startswith(row.dataset_id + ":")
    ]
    if (
        len(sources) != 1
        or snapshot.source_versions.get(sources[0].source_id) != sources[0].dataset_id
        or bundle.manifest.graph_version != runtime.build.source_graph_version
        or canonical_hash(
            AcceptedData.model_validate(to_accepted_data_v2(bundle)).model_dump(mode="json")
        )
        != scope.snapshot_hash
    ):
        raise ValueError("WALK_GRAPH_OR_CANONICAL_SNAPSHOT_DIFFERS_FROM_IMMUTABLE_SOURCE")
    from a2_api.adapters.walk_graph_source import MARKERS, verify_derived_graph

    derivation = None
    if MARKERS & graph.keys():
        derivation = verify_derived_graph(
            graph,
            (approval_path.parent / approval.routing_binding.graph_manifest.path).resolve().parent,
            snapshot=snapshot,
            city_id=scope.city_id,
            source_id=sources[0].source_id,
            dataset_id=sources[0].dataset_id,
            original_sha256=sources[0].checksum.value,
            original_bytes=sources[0].checksum.size_bytes,
            budget=remaining,
        )
    elif sources[0].checksum.value != approval.routing_binding.source_pbf_sha256:
        raise ValueError("WALK_GRAPH_OR_CANONICAL_SNAPSHOT_DIFFERS_FROM_IMMUTABLE_SOURCE")
    from a2_data_pipeline.walk_projection import project_walk_connectors

    expected = {
        row["physical_node_id"]: TypeAdapter(WalkConnector).validate_python(row)
        for row in project_walk_connectors(
            bundle,
            walking_speed_kmh=5,
            speed_evidence_refs=list(artifact.build.evidence_refs),
            profile_version=artifact.build.profile_version,
        )
    }
    if any(expected.get(row.physical_node_id) != row for row in runtime.connectors):
        raise ValueError("WALK_CONNECTOR_DIFFERS_FROM_IMMUTABLE_SOURCE_PROJECTION")
    evidence = {row.evidence_id for row in runtime.evidence}
    known = evidence | {row.evidence_id for row in snapshot.evidence}
    required = set(runtime.build.evidence_refs)
    for row in runtime.connectors:
        required.update(row.evidence_refs)
    for correlation in runtime.correlations:
        required.update(correlation.evidence_refs)
        required.update(correlation.serialization_evidence_refs)
    if len(evidence) != len(runtime.evidence) or not required <= known:
        raise ValueError("WALK_RUNTIME_EVIDENCE_UNRESOLVED")
    registry = ProductRegistry()
    prepared = prepare_walk_inputs(
        snapshot.model_dump(mode="json"),
        city_id=scope.city_id,
        departure_at=scope.valid_from,
        methodology_version=registry.methodology.version,
        config_hash=registry.config_hash,
        search_parameters_id="bound-walk-validation:" + reference.sha256,
        build=runtime.build,
        connectors=runtime.connectors,
        correlations=runtime.correlations,
    )
    origins = {row.entity_id: row for row in prepared.point_endpoints}
    targets = {row.entity_id: row for row in prepared.destination_endpoints}
    resolved = frozenset(
        (point, destination)
        for point, destination in pairs
        if all(
            endpoint.connector is not None
            and endpoint.connector.confirmed
            and endpoint.correlation is not None
            for endpoint in (origins[point], targets[destination])
        )
    )
    if any(
        not any(category in destinations[destination].categories for _, destination in resolved)
        for category in categories
    ):
        raise ValueError("WALK_APPROVED_CORRELATIONS_OR_CONNECTORS_UNAVAILABLE")
    return BoundWalkBinding(
        root.resolve(),
        reference,
        scope,
        binding,
        approval,
        binding.approval.sha256,
        runtime,
        executor_fingerprint(),
        resolved,
        MappingProxyType(
            {
                row.destination_id: row.name
                for row in bundle.destinations
                if row.status == "accepted"
                and row.destination_id in destinations
                and row.name is not None
            }
        ),
        derivation.limitations if derivation is not None else (),
        derivation.source_snapshot_id if derivation is not None else None,
    )
