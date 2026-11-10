"""Remove only unused evidence definitions from an otherwise unchanged result."""

from typing import Any

from a2_contracts.common import canonical_hash
from a2_contracts.v2.results import AnalysisResult


def compact_result_evidence(result: AnalysisResult) -> AnalysisResult:
    payload = result.model_dump(mode="json")
    # Recheck the complete original DTO, including all nested reference closures.
    # model_copy/update must never bypass canonical result validation here.
    AnalysisResult.model_validate(payload)
    definitions = {row.evidence_id for row in result.evidence_refs}
    pending: list[Any] = [
        value for key, value in payload.items() if key not in {"evidence_refs", "result_hash"}
    ]
    referenced: set[str] = set()
    while pending:
        value = pending.pop()
        if isinstance(value, str):
            if value in definitions:
                referenced.add(value)
        elif isinstance(value, dict):
            pending.extend(value.keys())
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    # EvidenceRef contains provenance fields, and no other evidence references.
    # Scanning every saved scalar also retains future nested reference fields.
    if referenced == definitions:
        return result
    payload["evidence_refs"] = [
        row.model_dump(mode="json") for row in result.evidence_refs if row.evidence_id in referenced
    ]
    compacted = AnalysisResult.model_validate(payload)
    return compacted.model_copy(
        update={
            "result_hash": canonical_hash(
                compacted.model_dump(mode="json", exclude={"result_hash"})
            )
        }
    )
