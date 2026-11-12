"""One gated, scoped documentary lookup through the existing read-only backend."""

from a2_contracts.v2.assistant import ExplanationRequest
from a2_contracts.v2.evidence import EvidenceResolver
from a2_contracts.v2.results import AnalysisResult, EvidenceRef
from a2_contracts.v2.retrieval import EvidenceSearchRequest, EvidenceSearchResult
from pydantic import JsonValue

from ..tools import AccessScope, ToolBackend, validate_tool


def needs_documents(context: ExplanationRequest | None) -> bool:
    question = (context.question or "").casefold() if context else ""
    return any(
        word in question for word in ("источник", "методик", "основан", "документ", "source")
    )


def document_ref(hit: dict[str, JsonValue]) -> EvidenceRef:
    return EvidenceRef.model_validate(
        {key: hit[key] for key in ("evidence_id", "source_id", "url", "retrieved_at", "section")}
        | {"observed_at": None}
    )


async def retrieve(
    backend: ToolBackend, saved: AnalysisResult, scope: AccessScope, context: ExplanationRequest
) -> EvidenceSearchResult:
    day = saved.request.departure_at.date()
    request = EvidenceSearchRequest(
        query=(context.question or "")[:500],
        territory_ids=sorted(scope.territory_ids)[:20],
        source_ids=sorted(scope.source_ids)[:20],
        start_date=day,
        end_date=day,
    )
    name, args = validate_tool("search_evidence", request.model_dump_json(), scope)
    if saved.analysis_id not in scope.analysis_ids:
        raise ValueError("Unauthorized saved analysis")
    result = EvidenceSearchResult.model_validate(await backend.call(name, args, scope))
    prior = {ref.evidence_id: ref for ref in saved.evidence_refs}
    for hit in result.hits:
        EvidenceResolver((hit.evidence_id,))
        if (
            hit.snapshot_id != saved.snapshot_id
            or hit.snapshot_id not in scope.snapshot_ids
            or hit.source_id not in (request.source_ids or [])
            or not set(hit.territory_ids) <= set(request.territory_ids)
            or not hit.valid_from <= day <= hit.valid_to
        ):
            raise ValueError("Document outside authorized scope or period")
        ref = document_ref(hit.model_dump(mode="json"))
        old = prior.get(ref.evidence_id)
        if old is not None and (old.source_id, old.url, old.retrieved_at) != (
            ref.source_id,
            ref.url,
            ref.retrieved_at,
        ):
            raise ValueError("Conflicting document provenance")
        prior[ref.evidence_id] = ref
    return result
