"""Scoped orchestration for canonical analyses. No formulas or routing in HTTP."""

from __future__ import annotations

from typing import TYPE_CHECKING

from a2_assistant import AccessScope
from a2_assistant.context.retrieval import DocumentCitations, needs_documents, retrieve
from a2_assistant.core import template_explanation
from a2_contracts.v2.assistant import Explanation, ExplanationRequest
from a2_contracts.v2.evidence import require_result_evidence
from a2_contracts.v2.results import AnalysisResult, Metric, Score

from a2_api.core.errors import DomainError
from a2_api.core.scope import territory_ids

if TYPE_CHECKING:
    from a2_api.services.analyses import AnalysisService


async def explain(
    service: AnalysisService,
    analysis_id: str,
    scope: str,
    request_id: str,
    body: ExplanationRequest | None = None,
) -> Explanation:
    result = await service.analysis(analysis_id, scope)
    require_result_evidence(result)
    context = body or ExplanationRequest()
    if context.selection is not None:
        kind, identifier = context.selection.entity_kind, context.selection.entity_id
        # Only IDs actually belonging to this immutable result can focus the chat.
        allowed: dict[str, set[str]] = {
            "cell": set(),
            "sample_point": set(),
            "destination": set(),
            "freight_site": set(),
            "freight_node": set(),
            "access_node": set(),
            "territory": set(territory_ids(result.request)),
        }
        items: list[Metric | Score] = [*result.metrics, *result.scores]
        for item in items:
            subject = item.subject
            for field, target in (
                ("point_id", "sample_point"),
                ("destination_id", "destination"),
                ("site_id", "freight_site"),
                ("territory_id", "territory"),
            ):
                value = getattr(subject, field)
                if field == "destination_id" and subject.kind in {
                    "connection",
                    "city_pair",
                    "pipeline",
                }:
                    continue
                if value is not None:
                    allowed[target].add(value)
            if subject.kind == "freight_node" and subject.node_id is not None:
                allowed["freight_node"].add(subject.node_id)
        for journey in result.journeys:
            if journey.point_id is not None:
                allowed["sample_point"].add(journey.point_id)
            if journey.destination_object_id is not None:
                allowed["destination"].add(journey.destination_object_id)
            allowed["access_node"].update([journey.origin_node_id, journey.destination_node_id])
        for feature in result.map_data.features:
            allowed[feature.entity_kind].add(feature.entity_id)
        if identifier not in allowed[kind]:
            raise DomainError(
                404, "unknown_id", "Selected entity is not part of the saved analysis"
            )
    if service.assistant is None:
        explanation = template_explanation(result, request_id, "DISABLED", context=context)
        if (
            service.repository is not None
            and not await service._repository().private_source_bindings(analysis_id, scope)
        ):
            explanation = await _with_documents(
                service, result, await _access(service, result, scope), context, explanation
            )
        await service.analysis(analysis_id, scope)
        return explanation
    bindings = await service._repository().private_source_bindings(analysis_id, scope)
    # A grant opt-in cannot fence budget waits, retries and every model attempt.
    # Keep all private facts local until that separate egress gate is reviewed.
    if bindings:
        explanation = template_explanation(
            result, request_id, "PRIVATE_SOURCE_EXTERNAL_MODEL_DISABLED", context=context
        )
        await service.analysis(analysis_id, scope)
        return explanation
    access = await _access(service, result, scope)
    proposals = await service.proposals(analysis_id, scope)
    explanation = await service.assistant.explain(
        result,
        access_scope=access,
        registered_template_ids={p.template_id for p in proposals},
        request_id=request_id,
        context=context,
    )
    if explanation.status == "template":
        explanation = await _with_documents(service, result, access, context, explanation)
    await service.analysis(analysis_id, scope)
    return explanation


async def _access(service: AnalysisService, result: AnalysisResult, scope: str) -> AccessScope:
    snapshot = await service.accepted_snapshot(result, scope)
    permitted_sources = {e.source_id for e in result.evidence_refs}
    snapshot_ids = {result.snapshot_id}
    if snapshot is not None:
        permitted_sources.update(e.source_id for e in snapshot.evidence if e.verified)
        # Accepted documents of this very snapshot, never of another one.
        corpus = service.document_corpora.get(snapshot.snapshot_id)
        if corpus is not None:
            snapshot_ids.add(corpus.snapshot_id)
            permitted_sources.update(corpus.source_ids)
    return AccessScope(
        principal_id=scope,
        analysis_ids=frozenset({result.analysis_id}),
        territory_ids=territory_ids(result.request),
        source_ids=frozenset(permitted_sources),
        snapshot_ids=frozenset(snapshot_ids),
    )


async def _with_documents(
    service: AnalysisService,
    result: AnalysisResult,
    access: AccessScope,
    context: ExplanationRequest,
    explanation: Explanation,
) -> Explanation:
    """A template answer still lists the found documents under its sources.

    The same bounded, scoped server search as the model path; the documents stay
    context and never change the template's findings or numbers.
    """
    if (
        explanation.retrieved_evidence
        or not service.document_corpora
        or not needs_documents(context)
        or not access.source_ids
        or not access.territory_ids
    ):
        return explanation
    from a2_api.assistant.tools import SavedTools

    try:
        found = await retrieve(SavedTools(service), result, access, context)
        citations = DocumentCitations(result)
        for hit in found.hits:
            citations.admit(hit)
    except Exception:
        return explanation
    if not citations.ids:
        return explanation
    return explanation.model_copy(update={"retrieved_evidence": citations.references()})
