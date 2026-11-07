"""Persisted calculation inputs. Context comes from trusted server-side preflight."""

from typing import Literal

from a2_contracts.api import AnalysisRequest, Job
from a2_contracts.common import ContractModel, Identifier, ProfileId, Version, canonical_hash
from a2_contracts.scenarios import ScenarioRequest

JobKind = Literal["analysis", "scenario"]
JobRequest = AnalysisRequest | ScenarioRequest


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


class ClaimedJob(ContractModel):
    job: Job
    kind: JobKind
    request: JobRequest
    context: JobContext
    cache_key: Identifier
    claim_token: Identifier


def request_hash(request: JobRequest) -> str:
    return canonical_hash(request.model_dump(mode="json"))


def cache_fingerprint(kind: JobKind, request: JobRequest, context: JobContext) -> str:
    return canonical_hash(
        {
            "kind": kind,
            "request": request.model_dump(mode="json"),
            "context": context.model_dump(mode="json"),
        }
    )
