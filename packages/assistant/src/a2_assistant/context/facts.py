"""Compact saved facts; no calculations or source acquisition."""

from a2_contracts.v2.evidence import EvidenceResolver
from a2_contracts.v2.results import AnalysisResult, Metric, Score

from ..evidence import leaf_references


def _request_note(saved: AnalysisResult) -> dict[str, object]:
    """What the user asked to calculate, without internal version fields."""

    request = saved.request.model_dump(mode="json", exclude_none=True)
    return {
        key: value
        for key, value in request.items()
        if key not in {"schema_version", "methodology", "snapshot_id", "priorities"}
    }


def _source_notes(
    saved: AnalysisResult, facts: list[Metric | Score], resolver: EvidenceResolver
) -> list[dict[str, object]]:
    """Short descriptions of the saved sources behind the selected facts (data only)."""

    wanted: list[str] = []
    for fact in facts:
        refs = fact.source_refs if isinstance(fact, Metric) else fact.evidence_refs
        wanted += [ref for ref in leaf_references(refs, resolver) if ref not in wanted]
    by_id = {ref.evidence_id: ref for ref in saved.evidence_refs}
    return [
        {
            "evidence_id": ref.evidence_id,
            "source_id": ref.source_id,
            "section": (ref.section or "")[:200],
            "url": str(ref.url),
            "retrieved_at": ref.retrieved_at.isoformat(),
            "observed_at": ref.observed_at.isoformat() if ref.observed_at else None,
        }
        for ref in (by_id[item] for item in wanted[:10] if item in by_id)
    ]


def _fact(value: Metric | Score, resolver: EvidenceResolver) -> dict[str, object]:
    result: dict[str, object] = {
        "metric_id" if isinstance(value, Metric) else "score_id": (
            value.metric_id if isinstance(value, Metric) else value.score_id
        ),
        "name": value.name,
        "status": value.status,
        "completeness": value.completeness,
        "reason_code": value.reason_code,
        "subject": value.subject.model_dump(mode="json", exclude_none=True),
        "evidence_ids": leaf_references(
            value.source_refs if isinstance(value, Metric) else value.evidence_refs, resolver
        )[:5],
    }
    if isinstance(value, Metric):
        result["unit"] = value.unit
        result["journey_saved"] = value.journey_id is not None
        result["time_kind"] = value.time_kind
    else:
        result["ranking_eligible"] = value.ranking_eligible
    return result
