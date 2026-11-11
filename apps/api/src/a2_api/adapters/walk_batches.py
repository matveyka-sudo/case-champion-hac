"""Approved physical OD subsets over the existing real pedestrian executor."""

import asyncio
import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

import httpx
from a2_contracts.common import canonical_hash
from a2_contracts.v2.api import TerritoryOption
from a2_contracts.v2.base import Category, ContractModel, Identifier, Timestamp
from a2_contracts.v2.dataset import AcceptedData
from a2_contracts.v2.requests import AnalysisRequest, UrbanPassengerRequest
from a2_contracts.v2.results import AnalysisResult, EvidenceRef
from a2_data_pipeline.contract_v2 import load_accepted_data, registry_options
from a2_data_pipeline.store import load_bundle
from a2_engine import (
    WalkPlanIssue,
    WalkRoutePlan,
    WalkRouteRequest,
    analyze_normalized_walk_routes,
    load_urban_config,
    load_urban_journey_rules,
    normalize_walk_response,
    plan_walk_routes,
    prepare_walk_inputs,
)
from pydantic import Field, TypeAdapter, field_validator

from a2_api.adapters import walk_pilot
from a2_api.adapters.product_projection import Projection, project_urban
from a2_api.adapters.product_runtime import FileRef, read_document
from a2_api.core.batches import (
    MAX_EXECUTION_PAIRS,
    MAX_RETAINED_ROUTE_BYTES,
    BatchCheckpoint,
    BatchPlan,
    PairObservation,
    PairObservationRef,
    PlannedPair,
    RawObservation,
)
from a2_api.core.config import RuntimeSettings
from a2_api.core.errors import DomainError
from a2_api.core.jobs import ClaimedJob, V2JobContext
from a2_api.core.registry import ProductRegistry
from a2_api.core.walk_route_artifacts import (
    RouteArtifactContext,
    resolve_checkpoint,
    write_route_artifact,
)
from a2_api.workers.checkpoint_channel import CheckpointChannel

if TYPE_CHECKING:
    from a2_api.adapters.walk_runtime import BoundWalkBinding


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
    categories: list[Category] = Field(min_length=1, max_length=9)
    # The city/category catalog is not a single executable job. Request-level
    # selection below retains the existing 4096-pair and 8 MiB execution limits.
    pairs: list[PhysicalPair] = Field(min_length=1, max_length=32768)
    batch_size: int = Field(ge=1, le=16)
    max_attempts: int = Field(ge=1, le=3)
    max_provider_calls: int = Field(ge=1, le=100000)
    max_checkpoint_bytes: int = Field(ge=1, le=16 * 1024 * 1024)
    observation_retention_seconds: int = Field(ge=1, le=2592000)
    valid_until: Timestamp
    checkpoint_format: Literal["artifact_refs_v2"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    max_route_artifact_bytes: int | None = Field(
        default=None, ge=1, le=MAX_RETAINED_ROUTE_BYTES, exclude_if=lambda value: value is None
    )

    @field_validator("categories")
    @classmethod
    def canonical_categories(cls, categories: list[Category]) -> list[Category]:
        if categories != sorted(set(categories)):
            raise ValueError("APPROVED_CATEGORIES_MUST_BE_UNIQUE_AND_SORTED")
        return categories


def _unavailable(reason: str) -> DomainError:
    return DomainError(503, "engine_unavailable", reason, retryable=True)


def selected_pairs(
    pairs: list[PhysicalPair], destinations: set[str]
) -> set[tuple[str, str]]:
    selected = {(p.point_id, p.destination_id) for p in pairs if p.destination_id in destinations}
    if len(selected) > MAX_EXECUTION_PAIRS:
        raise _unavailable("WALK_BATCH_PAIR_BUDGET_EXCEEDED")
    return selected


def load_approval(
    *, bound: "BoundWalkBinding | None" = None, snapshot: AcceptedData | None = None
) -> tuple[Approval, str, walk_pilot.Runtime]:
    from a2_api.adapters.walk_runtime import load_binding, validate_approval_sources

    if bound is not None:
        if snapshot is None:
            raise _unavailable("BOUND_WALK_SNAPSHOT_REQUIRED")
        try:
            if bound.inventory is not None:
                from a2_api.adapters.walk_runtime import executor_fingerprint

                if (
                    snapshot.snapshot_id != bound.scope.snapshot_id
                    or canonical_hash(snapshot.model_dump(mode="json")) != bound.scope.snapshot_hash
                ):
                    raise ValueError("APPROVED_WALK_SNAPSHOT_CHANGED")
                bound.inventory.assert_current(
                    executor_fingerprint=executor_fingerprint(), now=datetime.now(UTC)
                )
                return bound.approval, bound.approval_sha256, bound.runtime
            current = load_binding(
                bound.root, bound.reference, snapshot=snapshot, scope=bound.scope
            )
            if current.executor_fingerprint != bound.executor_fingerprint:
                raise ValueError("WALK_EXECUTOR_IMPLEMENTATION_CHANGED")
            return current.approval, current.approval_sha256, current.runtime
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise _unavailable("WALK_BATCH_APPROVAL_OR_BINDING_UNAVAILABLE") from exc
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
        validate_approval_sources(approval, runtime, root=Path(path).parent)
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
    request: AnalysisRequest,
    snapshot: AcceptedData,
    *,
    bound: "BoundWalkBinding | None" = None,
) -> tuple[BatchPlan, WalkRoutePlan, walk_pilot.Runtime]:
    approval, digest, runtime = load_approval(bound=bound, snapshot=snapshot)
    if not isinstance(request, UrbanPassengerRequest) or (
        request.city_id != approval.city_id
        or request.passenger_mode != "walk"
        or request.traffic_mode != "static"
        or not set(request.categories) <= set(approval.categories)
        or request.area_id is not None
        or (
            bound is not None
            and not bound.scope.valid_from <= request.departure_at < bound.scope.valid_to
        )
    ):
        raise _unavailable("REQUEST_OUTSIDE_APPROVED_WALK_BATCH_SCOPE")
    if (
        snapshot.snapshot_id != approval.snapshot_id
        or canonical_hash(snapshot.model_dump(mode="json")) != approval.snapshot_hash
    ):
        raise _unavailable("APPROVED_WALK_SNAPSHOT_CHANGED")
    code = RuntimeSettings().code_version or ""
    if bound is None and (digest not in code or runtime.digest not in code):
        raise _unavailable("WALK_BATCH_CODE_IDENTITY_MISMATCH")
    current = ProductRegistry()
    inputs = prepare_walk_inputs(
        snapshot.model_dump(mode="json"),
        city_id=request.city_id,
        departure_at=request.departure_at,
        methodology_version=current.methodology.version,
        config_hash=current.config_hash,
        search_parameters_id=(
            "waydex-walk-batches-" + digest
            if bound is None
            else "waydex-bound-walk-" + bound.binding_sha256 + ":" + bound.executor_fingerprint
        ),
        build=runtime.build,
        connectors=runtime.connectors,
        correlations=runtime.correlations,
    )
    selected_destinations = {
        d.destination_id
        for d in snapshot.destinations
        if set(d.categories) & set(request.categories)
    }
    approved = selected_pairs(approval.pairs, selected_destinations)
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
        tuple(sorted({point for point, _ in approved})),
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
    if (approval.checkpoint_format is None) != (approval.max_route_artifact_bytes is None):
        raise _unavailable("WALK_COMPACT_CHECKPOINT_BUDGET_REQUIRED")
    if approval.checkpoint_format is not None and bound is None:
        raise _unavailable("WALK_COMPACT_CHECKPOINT_REQUIRES_BOUND_PROFILE")
    batch = BatchPlan(
        schema_version=(
            "waydex-walk-execution/3"
            if approval.checkpoint_format == "artifact_refs_v2"
            else "waydex-walk-execution/1"
            if bound is None
            else "waydex-walk-execution/2"
        ),
        checkpoint_format=approval.checkpoint_format,
        max_route_artifact_bytes=approval.max_route_artifact_bytes,
        runtime_binding_hash=bound.binding_sha256 if bound is not None else None,
        executor_fingerprint=bound.executor_fingerprint if bound is not None else None,
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


async def preflight(
    request: AnalysisRequest,
    snapshot: AcceptedData,
    *,
    bound: "BoundWalkBinding | None" = None,
) -> BatchPlan:
    # Provider readiness is checked only after the parent's fenced attempt has
    # reserved its call budget. Repeated HTTP submissions cannot spend free calls.
    batch, _, _ = await asyncio.to_thread(_prepare, request, snapshot, bound=bound)
    return batch


def calculate(
    claim: ClaimedJob,
    channel: CheckpointChannel,
    *,
    bound: "BoundWalkBinding | None" = None,
    snapshot: AcceptedData | None = None,
) -> AnalysisResult:
    if not isinstance(claim.context, V2JobContext) or claim.context.batch_execution is None:
        raise _unavailable("SAVED_WALK_BATCH_EXECUTION_REQUIRED")
    context = claim.context
    if bound is not None and context.code_version != RuntimeSettings().code_version:
        raise _unavailable("BOUND_WALK_SAVED_CODE_IDENTITY_CHANGED")
    execution = context.batch_execution
    assert execution is not None
    request = context.normalized_request
    assert isinstance(request, UrbanPassengerRequest)
    if snapshot is None:
        if bound is not None:
            raise _unavailable("BOUND_WALK_SNAPSHOT_REQUIRED")
        snapshot = cast(AcceptedData, load_accepted_data(context.snapshot_id))
    batch, plan, runtime = _prepare(context.normalized_request, snapshot, bound=bound)
    if batch.model_dump(mode="json") != execution.model_dump(
        mode="json", exclude={"access_scope", "context_fingerprint"}
    ):
        raise _unavailable("QUEUED_WALK_BATCH_INPUTS_CHANGED")
    completed = {checkpoint.batch_id: checkpoint for checkpoint in channel.completed}
    artifact_context = RouteArtifactContext(execution) if execution.checkpoint_format else None
    retained_bytes = sum(checkpoint.retained_route_bytes for checkpoint in completed.values())
    with httpx.Client(
        timeout=15, headers={"Accept-Encoding": "identity"}, trust_env=False
    ) as client:
        for batch_id, pairs in execution.batches:
            if batch_id in completed:
                continue
            load_approval(bound=bound, snapshot=snapshot)
            walk_pilot.check_status(runtime)
            observations: list[PairObservation | PairObservationRef] = []
            for pair in pairs:
                envelope, pair_evidence = walk_pilot.execute_route(client, runtime, pair.request)
                raw = []
                for ref in pair_evidence:
                    digest = ref.evidence_id.removeprefix("valhalla-observation-")
                    source = runtime.evidence_dir / (digest + ".json")
                    raw.append(RawObservation(sha256=digest, size_bytes=source.stat().st_size))
                observation = PairObservation(
                    pair_id=pair.pair_id,
                    envelope=envelope,
                    evidence=pair_evidence,
                    raw_observations=raw,
                )
                if artifact_context is not None:
                    reference = write_route_artifact(
                        observation,
                        artifact_context,
                        remaining_bytes=artifact_context.max_retained_bytes - retained_bytes,
                    )
                    retained_bytes += reference.retained_bytes
                    if retained_bytes > artifact_context.max_retained_bytes:
                        raise _unavailable("WALK_RETAINED_ROUTE_ARTIFACT_BYTE_BUDGET_EXCEEDED")
                    observations.append(reference)
                else:
                    observations.append(observation)
            checkpoint = BatchCheckpoint(
                schema_version="waydex-walk-checkpoint/2"
                if artifact_context
                else "waydex-walk-checkpoint/1",
                execution_identity=execution.identity,
                batch_id=batch_id,
                observations=observations,
            )
            channel.commit(checkpoint)
            completed[batch_id] = checkpoint
    load_approval(bound=bound, snapshot=snapshot)
    walk_pilot.check_status(runtime)
    planned = {pair.pair_id: pair.request for pair in execution.pairs}
    normalized_routes = []
    observed_evidence: dict[str, EvidenceRef] = {}
    # Resolve and validate at most one existing 16-pair window before releasing
    # raw JSON. Only normalized public ML1 route values survive reduction.
    for batch_id, _ in execution.batches:
        window = resolve_checkpoint(completed[batch_id], execution, context=artifact_context)
        for observation in window:
            normalized_routes.append(
                normalize_walk_response(planned[observation.pair_id], observation.envelope)
            )
            for evidence_ref in observation.evidence:
                previous = observed_evidence.get(evidence_ref.evidence_id)
                if previous is None or evidence_ref.retrieved_at < previous.retrieved_at:
                    observed_evidence[evidence_ref.evidence_id] = evidence_ref
        del window
    calculated = analyze_normalized_walk_routes(
        plan,
        normalized_routes,
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
        *observed_evidence.values(),
    ):
        previous = evidence.get(row.evidence_id)
        if previous is None or row.retrieved_at < previous.retrieved_at:
            evidence[row.evidence_id] = row
    output = Projection(context, list(evidence.values()))
    project_urban(output, calculated.analysis)
    if bound is not None:
        from a2_api.adapters.walk_observed import project_observed_walk

        project_observed_walk(output, calculated)
    output.limitations.update({"APPROVED_PARTIAL_OD_EXECUTION", "MINIMUM_TIME_UNPROVEN"})
    if bound is not None:
        output.limitations.add("WALK_CONNECTOR_TIME_0_1_SECOND_PRECISION")
        output.limitations.update(bound.graph_limitations)
    result = output.finish()
    if bound is not None:
        from a2_api.adapters.evidence_groups import pack_analysis_evidence
        from a2_api.adapters.result_evidence import compact_result_evidence
        from a2_api.adapters.walk_map import attach_walk_map

        result = attach_walk_map(
            result, snapshot, calculated, bound.destination_names, map_features=bound.map_features
        )
        return pack_analysis_evidence(compact_result_evidence(result))
    return result
