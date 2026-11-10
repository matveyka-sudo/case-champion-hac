"""Approved physical OD subsets over the existing real pedestrian executor."""

import asyncio
import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

import httpx
from a2_contracts.common import canonical_hash
from a2_contracts.v2.api import TerritoryOption
from a2_contracts.v2.base import ContractModel, Identifier, Timestamp
from a2_contracts.v2.dataset import AcceptedData
from a2_contracts.v2.requests import AnalysisRequest, UrbanPassengerRequest
from a2_contracts.v2.results import AnalysisResult, EvidenceRef
from a2_data_pipeline.contract_v2 import load_accepted_data, registry_options
from a2_data_pipeline.store import load_bundle
from a2_engine import (
    WalkPlanIssue,
    WalkRoutePlan,
    WalkRouteRequest,
    analyze_walk_responses,
    load_urban_config,
    load_urban_journey_rules,
    plan_walk_routes,
    prepare_walk_inputs,
)
from pydantic import Field, TypeAdapter

from a2_api.adapters import walk_pilot
from a2_api.adapters.product_projection import Projection, project_urban
from a2_api.adapters.product_runtime import FileRef, read_document
from a2_api.core.batches import (
    BatchCheckpoint,
    BatchPlan,
    PairObservation,
    PlannedPair,
    RawObservation,
)
from a2_api.core.config import RuntimeSettings
from a2_api.core.errors import DomainError
from a2_api.core.jobs import ClaimedJob, V2JobContext
from a2_api.core.registry import ProductRegistry
from a2_api.workers.checkpoint_channel import CheckpointChannel


class RoutingBinding(ContractModel):
    schema_version: Literal["waydex-routing-binding/1"]
    graph_manifest: FileRef
    source_pbf_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    tile_manifest_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    build_image_digest: Identifier
    build_configuration_hash: str = Field(pattern="^[a-f0-9]{64}$")
    runtime_build_id: Identifier
    source_graph_version: Identifier
    endpoint_url: str
    profile_fingerprint: str = Field(pattern="^[a-f0-9]{64}$")
    immutable_deployment_attested: Literal[True]
    attested_by: Identifier


class PhysicalPair(ContractModel):
    point_id: Identifier
    destination_id: Identifier


class Approval(ContractModel):
    schema_version: Literal["waydex-walk-batch-approval/1"]
    snapshot_id: Identifier
    snapshot_hash: str = Field(pattern="^[a-f0-9]{64}$")
    bundle_manifest_hash: str = Field(pattern="^[a-f0-9]{64}$")
    runtime_artifact_hash: str = Field(pattern="^[a-f0-9]{64}$")
    routing_binding: RoutingBinding
    city_id: Identifier
    categories: list[Literal["school"]] = Field(min_length=1, max_length=1)
    pairs: list[PhysicalPair] = Field(min_length=1, max_length=4096)
    batch_size: int = Field(ge=1, le=16)
    max_attempts: int = Field(ge=1, le=3)
    max_provider_calls: int = Field(ge=1, le=100000)
    max_checkpoint_bytes: int = Field(ge=1, le=16 * 1024 * 1024)
    observation_retention_seconds: int = Field(ge=1, le=2592000)
    valid_until: Timestamp


def _unavailable(reason: str) -> DomainError:
    return DomainError(503, "engine_unavailable", reason, retryable=True)


def load_approval() -> tuple[Approval, str, walk_pilot.Runtime]:
    path = os.environ.get("A2_WALK_BATCH_APPROVAL_PATH", "")
    digest = os.environ.get("A2_WALK_BATCH_APPROVAL_SHA256", "")
    if not path or len(digest) != 64:
        raise _unavailable("PINNED_WALK_BATCH_APPROVAL_REQUIRED")
    try:
        approval = Approval.model_validate(read_document(Path(path), digest))
        pairs = frozenset((p.point_id, p.destination_id) for p in approval.pairs)
        if len(pairs) != len(approval.pairs):
            raise ValueError("DUPLICATE_APPROVED_PHYSICAL_PAIR")
        runtime = walk_pilot.load_runtime(approved_pairs=pairs)
        binding = approval.routing_binding
        graph_path = (Path(path).parent / binding.graph_manifest.path).resolve()
        if not graph_path.is_relative_to(Path(path).parent.resolve()):
            raise ValueError("GRAPH_MANIFEST_OUTSIDE_APPROVAL_DIRECTORY")
        graph = read_document(graph_path, binding.graph_manifest.sha256)
        tiles = graph.get("files", [])
        if (
            not isinstance(tiles, list)
            or not tiles
            or any(
                not isinstance(row, dict)
                or not str(row.get("path", "")).startswith("tiles/")
                or len(str(row.get("sha256", ""))) != 64
                or not isinstance(row.get("bytes"), int)
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
        bundle = load_bundle(runtime.bundle_id)
        if canonical_hash(bundle.manifest.model_dump(mode="json")) != approval.bundle_manifest_hash:
            raise ValueError("ACCEPTED_BUNDLE_MANIFEST_CHANGED")
        return approval, digest, runtime
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise _unavailable("WALK_BATCH_APPROVAL_OR_BINDING_UNAVAILABLE") from exc


def registry() -> ProductRegistry:
    approval, _, runtime = load_approval()
    snapshot = cast(AcceptedData, load_accepted_data(runtime.bundle_id))
    options = registry_options(load_bundle(runtime.bundle_id))
    return ProductRegistry(
        territories=[TerritoryOption.model_validate(t) for t in options["territories"]],
        snapshots=[snapshot],
        ready_kinds=["urban_passenger"],
    )


def _prepare(
    request: AnalysisRequest, snapshot: AcceptedData
) -> tuple[BatchPlan, WalkRoutePlan, walk_pilot.Runtime]:
    approval, digest, runtime = load_approval()
    if not isinstance(request, UrbanPassengerRequest) or (
        request.city_id != approval.city_id
        or request.passenger_mode != "walk"
        or request.traffic_mode != "static"
        or request.categories != approval.categories
        or request.area_id is not None
    ):
        raise _unavailable("REQUEST_OUTSIDE_APPROVED_WALK_BATCH_SCOPE")
    if (
        snapshot.snapshot_id != approval.snapshot_id
        or canonical_hash(snapshot.model_dump(mode="json")) != approval.snapshot_hash
    ):
        raise _unavailable("APPROVED_WALK_SNAPSHOT_CHANGED")
    code = RuntimeSettings().code_version or ""
    if digest not in code or runtime.digest not in code:
        raise _unavailable("WALK_BATCH_CODE_IDENTITY_MISMATCH")
    current = ProductRegistry()
    inputs = prepare_walk_inputs(
        snapshot.model_dump(mode="json"),
        city_id=request.city_id,
        departure_at=request.departure_at,
        methodology_version=current.methodology.version,
        config_hash=current.config_hash,
        search_parameters_id="waydex-walk-batches-" + digest,
        build=runtime.build,
        connectors=runtime.connectors,
        correlations=runtime.correlations,
    )
    approved = {(p.point_id, p.destination_id) for p in approval.pairs}
    # Keep the full canonical catalog for final unknown/completeness semantics,
    # but ask the public planner only about explicitly approved physical pairs.
    # No city-wide Cartesian request inventory is materialized in preflight.
    endpoints = {endpoint.entity_id: endpoint for endpoint in inputs.destination_endpoints}
    requests: list[WalkRouteRequest] = []
    issues: list[WalkPlanIssue] = []
    selected: dict[str, list[str]] = {}
    for point_id, destination_id in approved:
        selected.setdefault(point_id, []).append(destination_id)
    for point_id in sorted(selected):
        sparse = replace(
            inputs,
            destination_endpoints=tuple(endpoints[dest] for dest in sorted(selected[point_id])),
        )
        part = plan_walk_routes(sparse, point_ids=[point_id], categories=request.categories)
        requests.extend(part.requests)
        issues.extend(part.issues)
    plan = WalkRoutePlan(
        inputs,
        tuple(sorted(runtime.point_ids)),
        tuple(sorted(request.categories)),
        tuple(requests),
        tuple(issues),
    )
    pairs = []
    for route in plan.requests:
        serialized = {"request": TypeAdapter(WalkRouteRequest).dump_python(route, mode="json")}
        pairs.append(PlannedPair(pair_id=canonical_hash(serialized), request=route))
    if not pairs:
        raise _unavailable("NO_APPROVED_PHYSICAL_PAIR_HAS_CONFIRMED_CONNECTORS")
    batch = BatchPlan(
        schema_version="waydex-walk-execution/1",
        snapshot_id=snapshot.snapshot_id,
        snapshot_hash=approval.snapshot_hash,
        bundle_manifest_hash=approval.bundle_manifest_hash,
        source_graph_version=runtime.build.source_graph_version,
        actual_graph_fingerprint=approval.routing_binding.graph_manifest.sha256,
        profile_fingerprint=runtime.build.profile_hash,
        runtime_artifact_hash=runtime.digest,
        approval_manifest_hash=digest,
        pairs=sorted(pairs, key=lambda p: p.pair_id),
        approved_pair_count=len(approved),
        unresolved_pair_count=len(approved) - len(pairs),
        batch_size=approval.batch_size,
        max_attempts=approval.max_attempts,
        max_provider_calls=approval.max_provider_calls,
        max_checkpoint_bytes=approval.max_checkpoint_bytes,
        observation_retention_seconds=approval.observation_retention_seconds,
        valid_until=approval.valid_until,
    )
    if len(batch.model_dump_json().encode()) > 8 * 1024 * 1024:
        raise _unavailable("WALK_BATCH_MANIFEST_BYTE_BUDGET_EXCEEDED")
    return batch, plan, runtime


async def preflight(request: AnalysisRequest, snapshot: AcceptedData) -> BatchPlan:
    # Provider readiness is checked only after the parent's fenced attempt has
    # reserved its call budget. Repeated HTTP submissions cannot spend free calls.
    batch, _, _ = await asyncio.to_thread(_prepare, request, snapshot)
    return batch


def calculate(claim: ClaimedJob, channel: CheckpointChannel) -> AnalysisResult:
    if not isinstance(claim.context, V2JobContext) or claim.context.batch_execution is None:
        raise _unavailable("SAVED_WALK_BATCH_EXECUTION_REQUIRED")
    context = claim.context
    execution = context.batch_execution
    assert execution is not None
    request = context.normalized_request
    assert isinstance(request, UrbanPassengerRequest)
    snapshot = cast(AcceptedData, load_accepted_data(context.snapshot_id))
    batch, plan, runtime = _prepare(context.normalized_request, snapshot)
    if batch.model_dump(mode="json") != execution.model_dump(
        mode="json", exclude={"access_scope", "context_fingerprint"}
    ):
        raise _unavailable("QUEUED_WALK_BATCH_INPUTS_CHANGED")
    completed = {checkpoint.batch_id: checkpoint for checkpoint in channel.completed}
    with httpx.Client(
        timeout=15, headers={"Accept-Encoding": "identity"}, trust_env=False
    ) as client:
        for batch_id, pairs in execution.batches:
            if batch_id in completed:
                continue
            load_approval()
            walk_pilot.check_status(runtime)
            observations = []
            for pair in pairs:
                envelope, pair_evidence = walk_pilot.execute_route(client, runtime, pair.request)
                raw = []
                for ref in pair_evidence:
                    digest = ref.evidence_id.removeprefix("valhalla-observation-")
                    source = runtime.evidence_dir / (digest + ".json")
                    raw.append(RawObservation(sha256=digest, size_bytes=source.stat().st_size))
                observations.append(
                    PairObservation(
                        pair_id=pair.pair_id,
                        envelope=envelope,
                        evidence=pair_evidence,
                        raw_observations=raw,
                    )
                )
            checkpoint = BatchCheckpoint(
                schema_version="waydex-walk-checkpoint/1",
                execution_identity=execution.identity,
                batch_id=batch_id,
                observations=observations,
            )
            channel.commit(checkpoint)
            completed[batch_id] = checkpoint
    load_approval()
    walk_pilot.check_status(runtime)
    responses = [
        o.envelope for batch_id, _ in execution.batches for o in completed[batch_id].observations
    ]
    calculated = analyze_walk_responses(
        plan,
        responses,
        selected_categories={str(k): str(v) for k, v in request.priorities.items()},
        config=load_urban_config(),
        rules=load_urban_journey_rules(),
    )
    evidence = {
        row.evidence_id: EvidenceRef(
            evidence_id=row.evidence_id,
            source_id=row.source_id,
            url=row.url,
            retrieved_at=row.retrieved_at,
            observed_at=None,
            section="accepted-snapshot",
        )
        for row in snapshot.evidence
    }
    for row in (
        *runtime.evidence,
        *(
            ref
            for batch_id, _ in execution.batches
            for o in completed[batch_id].observations
            for ref in o.evidence
        ),
    ):
        previous = evidence.get(row.evidence_id)
        if previous is None or row.retrieved_at < previous.retrieved_at:
            evidence[row.evidence_id] = row
    output = Projection(context, list(evidence.values()))
    project_urban(output, calculated.analysis)
    output.limitations.update({"APPROVED_PARTIAL_OD_EXECUTION", "MINIMUM_TIME_UNPROVEN"})
    return output.finish()
