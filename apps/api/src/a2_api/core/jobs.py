"""Persisted calculation inputs. Context comes from trusted server-side preflight."""

from typing import Literal, Self

from a2_contracts.api import AnalysisRequest, Job
from a2_contracts.common import ContractModel, Identifier, ProfileId, Version, canonical_hash
from a2_contracts.scenarios import ScenarioRequest
from a2_contracts.v2.base import Timestamp, VersionRefs
from a2_contracts.v2.requests import AnalysisRequest as V2AnalysisRequest
from a2_contracts.v2.requests import UrbanPassengerRequest
from a2_contracts.v2.results import AnalysisResult
from a2_contracts.v2.scenarios import ScenarioProposal, ScenarioRunRequest
from pydantic import Field, model_validator

from a2_api.core.batches import BatchExecution
from a2_api.core.scenario_scope import ScenarioEndpointScope

JobKind = Literal["analysis", "scenario"]
JobRequest = AnalysisRequest | ScenarioRequest | V2AnalysisRequest | ScenarioRunRequest


class JobContext(ContractModel):
    bundle_id: Identifier
    bundle_hash: Identifier
    code_version: Identifier
    profile_hashes: dict[ProfileId, Identifier]
    profile_versions: dict[ProfileId, Version]
    origin_ids: dict[ProfileId, list[Identifier]]
    task_hashes: dict[ProfileId, Identifier]
    baseline_run_ids: dict[ProfileId, Identifier]
    task_count: Version


class V2JobContext(ContractModel):
    schema_version: Literal["2"]
    snapshot_id: Identifier
    snapshot_hash: Identifier
    versions: VersionRefs
    analysis_id: Identifier
    normalized_request: V2AnalysisRequest
    access_scope: Identifier
    scenario_proposal: ScenarioProposal | None = None
    cache_valid_until: Timestamp | None = None
    scenario_endpoint_scope: ScenarioEndpointScope | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    batch_execution: BatchExecution | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @property
    def bundle_id(self) -> str:
        return self.snapshot_id

    @property
    def code_version(self) -> str:
        return self.versions.code_version

    @model_validator(mode="after")
    def immutable_inputs(self) -> Self:
        request = self.normalized_request
        if request.snapshot_id != self.snapshot_id or self.versions.snapshot_id != self.snapshot_id:
            raise ValueError("Normalized request and versions must bind the accepted snapshot")
        if request.methodology is None or (
            request.methodology.version != self.versions.methodology_version
            or request.methodology.config_hash != self.versions.config_hash
        ):
            raise ValueError("Normalized request must bind the accepted methodology/config")
        if self.scenario_proposal is not None and (
            self.scenario_proposal.baseline_analysis_id != self.analysis_id
            or self.scenario_proposal.versions != self.versions
        ):
            raise ValueError("Scenario proposal must bind the baseline identity and versions")
        endpoint_scope = self.scenario_endpoint_scope
        if endpoint_scope is not None and (
            self.scenario_proposal is None
            or not isinstance(request, UrbanPassengerRequest)
            or endpoint_scope.snapshot_id != self.snapshot_id
            or endpoint_scope.snapshot_hash != self.snapshot_hash
            or endpoint_scope.city_id != request.city_id
        ):
            raise ValueError("Scenario endpoint scope differs from its immutable baseline context")
        execution = self.batch_execution
        if execution is not None and (
            self.scenario_proposal is not None
            or execution.snapshot_id != self.snapshot_id
            or execution.snapshot_hash != self.snapshot_hash
            or execution.source_graph_version != self.versions.graph_version
            or execution.access_scope != self.access_scope
            or execution.context_fingerprint != batch_context_fingerprint(self)
        ):
            raise ValueError("Batch execution differs from its immutable job context")
        return self


def batch_context_fingerprint(context: V2JobContext) -> str:
    return canonical_hash(
        context.model_dump(
            mode="json",
            exclude={
                "analysis_id",
                "cache_valid_until",
                "batch_execution",
            },
        )
    )


JobContextUnion = JobContext | V2JobContext


class CalculationOutput(ContractModel):
    """Trusted handler result with optional server-generated candidate proposals.

    Repository rebinds proposal identity/hash and publishes the batch atomically.
    No proposal generation or additional calculation is performed by this DTO.
    """

    analysis: AnalysisResult
    proposals: list[ScenarioProposal] = Field(default_factory=list, max_length=10)


class ClaimedJob(ContractModel):
    job: Job
    kind: JobKind
    request: JobRequest
    context: JobContextUnion
    cache_key: Identifier
    claim_token: Identifier


def request_hash(request: JobRequest) -> str:
    return canonical_hash(request.model_dump(mode="json"))


def cache_fingerprint(kind: JobKind, request: JobRequest, context: JobContextUnion) -> str:
    if isinstance(context, V2JobContext):
        return canonical_hash(
            {
                "kind": kind,
                "context": context.model_dump(
                    mode="json", exclude={"analysis_id", "cache_valid_until"}
                ),
            }
        )
    return canonical_hash(
        {
            "kind": kind,
            "request": request.model_dump(mode="json"),
            "context": context.model_dump(mode="json"),
        }
    )
