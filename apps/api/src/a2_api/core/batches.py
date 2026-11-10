"""Private, versioned OD execution identities and complete batch observations."""

from typing import Literal, Self

from a2_contracts.common import canonical_hash
from a2_contracts.v2.base import ContractModel, Identifier, Timestamp
from a2_contracts.v2.results import EvidenceRef
from a2_engine import WalkResponseEnvelope, WalkRouteRequest, normalize_walk_response
from pydantic import Field, model_validator

MAX_CHECKPOINT_BYTES = 8 * 1024 * 1024
MAX_ROUTE_ARTIFACT_BYTES = 8 * 1024 * 1024
MAX_RETAINED_ROUTE_BYTES = 256 * 1024 * 1024


class PlannedPair(ContractModel):
    pair_id: str = Field(pattern="^[a-f0-9]{64}$")
    request: WalkRouteRequest

    @model_validator(mode="after")
    def exact_identity(self) -> Self:
        if self.pair_id != canonical_hash(self.model_dump(mode="json", exclude={"pair_id"})):
            raise ValueError("OD_PAIR_IDENTITY_MISMATCH")
        return self


class BatchPlan(ContractModel):
    schema_version: Literal[
        "waydex-walk-execution/1", "waydex-walk-execution/2", "waydex-walk-execution/3"
    ]
    checkpoint_format: Literal["artifact_refs_v2"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    max_route_artifact_bytes: int | None = Field(
        default=None, ge=1, le=MAX_RETAINED_ROUTE_BYTES, exclude_if=lambda value: value is None
    )
    runtime_binding_hash: str | None = Field(
        default=None, pattern="^[a-f0-9]{64}$", exclude_if=lambda value: value is None
    )
    executor_fingerprint: str | None = Field(
        default=None, pattern="^[a-f0-9]{64}$", exclude_if=lambda value: value is None
    )
    snapshot_id: Identifier
    snapshot_hash: str = Field(pattern="^[a-f0-9]{64}$")
    bundle_manifest_hash: str = Field(pattern="^[a-f0-9]{64}$")
    source_graph_version: Identifier
    actual_graph_fingerprint: str = Field(pattern="^[a-f0-9]{64}$")
    profile_fingerprint: str = Field(pattern="^[a-f0-9]{64}$")
    runtime_artifact_hash: str = Field(pattern="^[a-f0-9]{64}$")
    approval_manifest_hash: str = Field(pattern="^[a-f0-9]{64}$")
    pairs: list[PlannedPair] = Field(min_length=1, max_length=4096)
    approved_pair_count: int = Field(ge=1, le=4096)
    unresolved_pair_count: int = Field(ge=0, le=4096)
    batch_size: int = Field(ge=1, le=16)
    max_attempts: int = Field(ge=1, le=3)
    max_provider_calls: int = Field(ge=1, le=100000)
    max_checkpoint_bytes: int = Field(ge=1, le=16 * 1024 * 1024)
    observation_retention_seconds: int = Field(ge=1, le=2592000)
    valid_until: Timestamp

    @property
    def identity(self) -> str:
        return canonical_hash(self.model_dump(mode="json"))

    @property
    def batches(self) -> list[tuple[str, list[PlannedPair]]]:
        result = []
        execution_identity = self.identity
        for offset in range(0, len(self.pairs), self.batch_size):
            pairs = self.pairs[offset : offset + self.batch_size]
            identity = canonical_hash(
                {
                    "execution": execution_identity,
                    "offset": offset,
                    "pairs": [p.pair_id for p in pairs],
                }
            )
            result.append((identity, pairs))
        return result

    @model_validator(mode="after")
    def bounded_inventory(self) -> Self:
        bound = self.runtime_binding_hash is not None and self.executor_fingerprint is not None
        if self.schema_version in {"waydex-walk-execution/2", "waydex-walk-execution/3"}:
            if not bound:
                raise ValueError("OD_PROFILE_BINDING_AND_EXECUTOR_REQUIRED")
        elif self.runtime_binding_hash is not None or self.executor_fingerprint is not None:
            raise ValueError("OD_LEGACY_EXECUTION_CANNOT_CLAIM_PROFILE_BINDING")
        if self.schema_version == "waydex-walk-execution/3":
            if (
                self.checkpoint_format != "artifact_refs_v2"
                or self.max_route_artifact_bytes is None
            ):
                raise ValueError("OD_COMPACT_ARTIFACT_BUDGET_REQUIRED")
        elif self.checkpoint_format is not None or self.max_route_artifact_bytes is not None:
            raise ValueError("OD_INLINE_EXECUTION_CANNOT_CLAIM_ARTIFACT_REFS")
        if self.approved_pair_count != len(self.pairs) + self.unresolved_pair_count:
            raise ValueError("APPROVED_PHYSICAL_PAIR_INVENTORY_MISMATCH")
        identities = [pair.pair_id for pair in self.pairs]
        if identities != sorted(set(identities)):
            raise ValueError("OD_PAIRS_MUST_BE_UNIQUE_AND_SORTED")
        requests = [pair.request.request_id for pair in self.pairs]
        if len(set(requests)) != len(requests):
            raise ValueError("OD_DUPLICATE_REQUEST_ID")
        if any(
            pair.request.build.snapshot_id != self.snapshot_id
            or pair.request.build.source_graph_version != self.source_graph_version
            or pair.request.build.profile_hash != self.profile_fingerprint
            for pair in self.pairs
        ):
            raise ValueError("OD_BUILD_OR_PROFILE_BINDING_MISMATCH")
        minimum_calls = (
            2 * len(self.pairs) + (len(self.pairs) + self.batch_size - 1) // self.batch_size + 2
        )
        if self.max_provider_calls < minimum_calls:
            raise ValueError("APPROVED_PROVIDER_CALL_BUDGET_CANNOT_COMPLETE_ONE_ATTEMPT")
        return self


class BatchExecution(BatchPlan):
    context_fingerprint: str = Field(pattern="^[a-f0-9]{64}$")
    access_scope: Identifier


class RawObservation(ContractModel):
    sha256: str = Field(pattern="^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=1, le=8 * 1024 * 1024)


class PairObservation(ContractModel):
    pair_id: str = Field(pattern="^[a-f0-9]{64}$")
    envelope: WalkResponseEnvelope
    evidence: list[EvidenceRef] = Field(max_length=8)
    raw_observations: list[RawObservation] = Field(max_length=8)


class PairObservationRef(ContractModel):
    pair_id: str = Field(pattern="^[a-f0-9]{64}$")
    artifact_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=1, le=MAX_ROUTE_ARTIFACT_BYTES)
    # Count the immutable envelope and its complete raw observation closure.
    retained_bytes: int = Field(ge=1, le=9 * MAX_ROUTE_ARTIFACT_BYTES)


class BatchCheckpoint(ContractModel):
    schema_version: Literal["waydex-walk-checkpoint/1", "waydex-walk-checkpoint/2"]
    execution_identity: str = Field(pattern="^[a-f0-9]{64}$")
    batch_id: str = Field(pattern="^[a-f0-9]{64}$")
    observations: list[PairObservation | PairObservationRef] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def exact_protocol(self) -> Self:
        expected = PairObservationRef if self.schema_version.endswith("/2") else PairObservation
        if any(not isinstance(observation, expected) for observation in self.observations):
            raise ValueError("OD_CHECKPOINT_OBSERVATION_PROTOCOL_MISMATCH")
        return self

    @property
    def retained_route_bytes(self) -> int:
        return sum(
            observation.retained_bytes
            for observation in self.observations
            if isinstance(observation, PairObservationRef)
        )

    @property
    def content_hash(self) -> str:
        return canonical_hash(self.model_dump(mode="json"))

    def validate_pair_inventory(self, execution: BatchExecution) -> None:
        batches = dict(execution.batches)
        if self.execution_identity != execution.identity or self.batch_id not in batches:
            raise ValueError("OD_CHECKPOINT_EXECUTION_MISMATCH")
        planned = batches[self.batch_id]
        if [o.pair_id for o in self.observations] != [p.pair_id for p in planned]:
            raise ValueError("OD_CHECKPOINT_PAIR_INVENTORY_MISMATCH")
        compact = execution.checkpoint_format == "artifact_refs_v2"
        if compact != self.schema_version.endswith("/2"):
            raise ValueError("OD_CHECKPOINT_EXECUTION_PROTOCOL_MISMATCH")

    def validate_inventory(
        self, execution: BatchExecution, *, resolved: list[PairObservation] | None = None
    ) -> None:
        self.validate_pair_inventory(execution)
        if resolved is None:
            if any(
                isinstance(observation, PairObservationRef) for observation in self.observations
            ):
                raise ValueError("OD_ARTIFACT_REFERENCES_REQUIRE_RESOLUTION")
            observations = [o for o in self.observations if isinstance(o, PairObservation)]
        else:
            observations = resolved
            if self.schema_version.endswith("/1") and observations != self.observations:
                raise ValueError("OD_INLINE_RESOLUTION_MISMATCH")
        planned = dict(execution.batches)[self.batch_id]
        if [o.pair_id for o in observations] != [p.pair_id for p in planned]:
            raise ValueError("OD_RESOLVED_PAIR_INVENTORY_MISMATCH")
        for expected, observation in zip(planned, observations, strict=True):
            # This public boundary checks request/payload/build/correlation identities
            # and conservatively retains provider failures and unproven minima.
            normalize_walk_response(expected.request, observation.envelope)
            refs = {ref.evidence_id for ref in observation.evidence}
            envelope = observation.envelope
            required = (
                set(envelope.evidence_refs)
                | set(envelope.endpoint_evidence_refs)
                | set(envelope.pure_walk_evidence_refs)
                | set(envelope.minimum_time_evidence_refs)
            )
            required.update(envelope.unreachable_evidence_refs)
            if not required <= refs:
                raise ValueError("OD_CHECKPOINT_EVIDENCE_UNRESOLVED")
            raw = {
                "valhalla-observation-" + record.sha256 for record in observation.raw_observations
            }
            if len(raw) != len(observation.raw_observations) or refs != raw:
                raise ValueError("OD_CHECKPOINT_RAW_INVENTORY_MISMATCH")
