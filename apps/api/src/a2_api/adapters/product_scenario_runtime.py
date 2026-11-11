"""Hash-pinned scenario documents and read-only saved-baseline worker dispatch.

Loading a catalog never routes. Only the fenced calculation worker generates
proposals or effects. A public scenario command supplies an authorized saved ID;
its baseline is fetched from the server database, never supplied by the client.
"""

import asyncio
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from a2_contracts.common import canonical_hash
from a2_contracts.v2.dataset import AcceptedData
from a2_contracts.v2.requests import AnalysisRequest
from a2_contracts.v2.results import AnalysisResult
from a2_contracts.v2.scenarios import ScenarioResult, ScenarioRunRequest

from a2_api.adapters import product_scenarios as bridge
from a2_api.adapters.demo_transport import DEMO_LIMITATIONS
from a2_api.adapters.product_inputs import (
    DeliveryInputs,
    EngineInputs,
    IntercityInputs,
    UrbanInputs,
)
from a2_api.adapters.product_scenario_inputs import (
    ScenarioCatalogDocument,
    document_matches_request,
    parse_scenario_document,
    source_inputs_hash,
)
from a2_api.core.config import RuntimeSettings
from a2_api.core.errors import DomainError
from a2_api.core.jobs import CalculationOutput, ClaimedJob, V2JobContext
from a2_api.core.registry import ProductRegistry
from a2_api.db.connection import open_pool
from a2_api.db.repositories.jobs import JobRepository


class FileReference(Protocol):
    @property
    def path(self) -> str: ...

    @property
    def sha256(self) -> str: ...


def load_profile_documents(
    root: Path,
    references: Sequence[FileReference],
    *,
    profile_id: str,
    snapshot: AcceptedData,
    inputs: EngineInputs,
    budget: list[int],
    request_allowed: Callable[[AnalysisRequest], bool],
) -> tuple[ScenarioCatalogDocument, ...]:
    """Validate source/catalog identities without calculating routes or feasibility.

    Actual fixed-operation validation and exact before equivalence happen only in
    the worker, before publishing any proposal. No configured catalog implies no
    product proposals or template availability.
    """
    from a2_api.adapters.product_runtime import _walk, read_document

    if len(references) > 16:
        raise ValueError("SCENARIO_DOCUMENT_LIMIT_EXCEEDED")
    if not isinstance(inputs, (UrbanInputs, IntercityInputs, DeliveryInputs)):
        raise ValueError("SCENARIO_MUTABLE_TRANSPORT_MODEL_UNAVAILABLE")
    current = ProductRegistry()
    snapshot_hash = canonical_hash(snapshot.model_dump(mode="json"))
    inputs_hash = source_inputs_hash(inputs)
    documents: list[ScenarioCatalogDocument] = []
    seen: list[AnalysisRequest] = []
    for reference in references:
        path = (root / reference.path).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError("SCENARIO_REFERENCE_OUTSIDE_MANIFEST_DIRECTORY")
        document = parse_scenario_document(read_document(path, reference.sha256, budget))
        request = document.normalized_request
        if (
            document.profile_id != profile_id
            or document.snapshot_id != snapshot.snapshot_id
            or document.snapshot_hash != snapshot_hash
            or document.source_inputs_hash != inputs_hash
            or request is None
            or request.analysis_kind != inputs.kind
            or request.methodology != current.methodology
            or request in seen
            or not request_allowed(request)
        ):
            raise ValueError("SCENARIO_CATALOG_SOURCE_BINDING_MISMATCH")
        known_evidence = {row.evidence_id for row in snapshot.evidence} | {
            row.evidence_id for row in document.evidence_refs
        }
        from a2_api.adapters.cargo_scenarios import CargoScenarioDocument, validate_plan_inputs

        if isinstance(document, CargoScenarioDocument):
            validate_plan_inputs(document, inputs)
            verified_evidence = {row.evidence_id for row in snapshot.evidence if row.verified}
            if any(
                not set((*plan.demand_evidence, *plan.resource_evidence)) <= verified_evidence
                for plan in document.plans
            ) or any(
                not set(resource.evidence_refs) <= verified_evidence
                for resource in document.resources
            ):
                raise ValueError("CARGO_HANDLING_VERIFIED_CANONICAL_EVIDENCE_REQUIRED")
            seen.append(request)
            documents.append(document)
            continue
        for item in _walk(document):
            if hasattr(item, "snapshot_id") and item.snapshot_id != snapshot.snapshot_id:
                raise ValueError("SCENARIO_DOCUMENT_SNAPSHOT_MISMATCH")
            for name, expected in (
                ("graph_version", snapshot.provenance.graph_version),
                ("calendar_version", snapshot.provenance.calendar_version),
                ("methodology_version", current.methodology.version),
            ):
                if hasattr(item, name) and getattr(item, name) != expected:
                    raise ValueError("SCENARIO_DOCUMENT_VERSION_MISMATCH")
            for name in ("evidence_refs", "source_refs", "demand_evidence_refs"):
                refs = getattr(item, name, ())
                if (
                    refs
                    and all(isinstance(ref, str) for ref in refs)
                    and not set(refs) <= known_evidence
                ):
                    raise ValueError("SCENARIO_DOCUMENT_EVIDENCE_UNRESOLVED")
        accepted_sites = {row.site_id for row in snapshot.freight_sites}
        if any(
            site.site_id not in accepted_sites
            for job in document.inputs.cargo
            for site in job.physical_sites
        ):
            raise ValueError("SCENARIO_PHYSICAL_SITE_OUTSIDE_ACCEPTED_SNAPSHOT")
        for plan in document.plans:
            operation = plan.operation
            if (
                operation.template_id not in {"coordinate_transfer", "additional_departure"}
                or operation.timing_model != "whole_execution"
            ):
                raise ValueError("SCENARIO_OPERATION_NOT_ALLOWLISTED")
            if operation.template_id == "additional_departure" and (
                operation.new_run is None
                or operation.new_physical_service is None
                or not operation.demand_evidence_refs
                or plan.feasibility is None
                or not plan.feasibility.blocks
            ):
                raise ValueError("SCENARIO_ADDITIONAL_EXECUTION_RESOURCE_BINDINGS_REQUIRED")
        seen.append(request)
        documents.append(document)
    return tuple(documents)


def template_ids(documents: dict[str, tuple[ScenarioCatalogDocument, ...]]) -> frozenset[str]:
    return frozenset(
        plan.template_id
        for group in documents.values()
        for document in group
        for plan in document.plans
    )


def _limited(analysis: AnalysisResult, reason: str) -> AnalysisResult:
    # An invalid optional experiment must not erase a valid calculated baseline.
    saved = analysis.model_copy(
        update={"limitations": sorted(set(analysis.limitations) | {reason})}
    )
    return saved.model_copy(
        update={
            "result_hash": canonical_hash(saved.model_dump(mode="json", exclude={"result_hash"}))
        }
    )


def finish_analysis(
    claim: ClaimedJob,
    analysis: AnalysisResult,
    inputs: EngineInputs,
    *,
    profile_id: str,
    documents: Sequence[ScenarioCatalogDocument],
    configured: bool,
) -> AnalysisResult | CalculationOutput:
    if not configured:
        return analysis
    if claim.kind != "analysis" or not isinstance(claim.context, V2JobContext):
        raise DomainError(422, "unsupported_scenario", "SCENARIO_ANALYSIS_CONTEXT_REQUIRED")
    if not documents:
        return _limited(analysis, "SCENARIO_DOCUMENT_UNAVAILABLE")
    matches = [
        document
        for document in documents
        if document_matches_request(document, claim.context.normalized_request)
    ]
    if not matches:
        return analysis
    if len(matches) != 1:
        return _limited(analysis, "SCENARIO_DOCUMENT_SCOPE_AMBIGUOUS")
    try:
        proposals = bridge.propose(
            claim.context, inputs, matches[0], analysis, profile_id=profile_id
        )
    except DomainError as error:
        reason = (
            error.message
            if error.message.startswith("SCENARIO_INFEASIBLE:")
            else "SCENARIO_PROPOSAL_UNAVAILABLE"
        )
        return _limited(analysis, reason)
    return CalculationOutput(analysis=analysis, proposals=proposals)


async def _saved_baseline(claim: ClaimedJob) -> AnalysisResult:
    context = claim.context
    if not isinstance(context, V2JobContext) or context.scenario_proposal is None:
        raise DomainError(422, "unsupported_scenario", "SAVED_SCENARIO_PROPOSAL_REQUIRED")
    settings = RuntimeSettings()
    if settings.database_url is None:
        raise DomainError(503, "data_unavailable", "SAVED_SCENARIO_DATABASE_UNAVAILABLE")
    try:
        pool = await open_pool(settings)
        try:
            return await JobRepository(pool).load_scenario_baseline(claim)
        finally:
            await pool.close()
    except DomainError:
        raise
    except Exception:
        raise DomainError(
            503, "data_unavailable", "SAVED_SCENARIO_BASELINE_UNAVAILABLE", retryable=True
        ) from None


def calculate_saved(
    claim: ClaimedJob,
    inputs: EngineInputs,
    *,
    profile_id: str,
    documents: Sequence[ScenarioCatalogDocument],
    synthetic: bool = False,
) -> ScenarioResult:
    if (
        claim.kind != "scenario"
        or not isinstance(claim.context, V2JobContext)
        or not isinstance(claim.request, ScenarioRunRequest)
        or claim.context.scenario_proposal is None
    ):
        raise DomainError(422, "unsupported_scenario", "SAVED_SCENARIO_PROPOSAL_REQUIRED")
    context = claim.context
    assert context.scenario_proposal is not None
    document_ids = set(context.scenario_proposal.assumptions)
    matches = [
        document
        for document in documents
        if document_matches_request(document, context.normalized_request)
        and "scenario-document:" + canonical_hash(document.model_dump(mode="json")) in document_ids
    ]
    if len(matches) != 1:
        raise DomainError(409, "incompatible_versions", "SAVED_SCENARIO_DOCUMENT_UNAVAILABLE")
    # This synchronous hook runs in the isolated spawned calculation process.
    baseline = asyncio.run(_saved_baseline(claim))
    baseline_is_synthetic = DEMO_LIMITATIONS <= set(baseline.limitations)
    if synthetic != baseline_is_synthetic or (
        synthetic and any(score.ranking_eligible for score in baseline.scores)
    ):
        raise DomainError(
            409, "incompatible_versions", "SCENARIO_SAVED_BASELINE_PROVENANCE_DIFFERS"
        )
    return bridge.calculate(
        claim, baseline=baseline, inputs=inputs, document=matches[0], profile_id=profile_id
    )
