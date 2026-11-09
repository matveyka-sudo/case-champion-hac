"""Accepted public document chunks and bounded read-only evidence retrieval.

ML2 supplies reviewed text/provenance. These DTOs never fetch a URL, ingest an
unreviewed source or assert that documentary context is a calculated metric.
"""

from datetime import date
from typing import Annotated, Literal, Self

from pydantic import Field, HttpUrl, model_validator

from a2_contracts.common import canonical_hash

from .base import ContractModel, Identifier, Timestamp
from .dataset import Confirmed


class EvidenceSearchRequest(ContractModel):
    query: Annotated[str, Field(min_length=1, max_length=500)]
    territory_ids: list[Identifier] = Field(min_length=1, max_length=20)
    source_ids: list[Identifier] | None = Field(default=None, min_length=1, max_length=20)
    year: Annotated[int, Field(strict=True, ge=1, le=9999)] | None = None
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def explicit_filters(self) -> Self:
        if not self.query.strip():
            raise ValueError("Search query must not be blank")
        for values in (self.territory_ids, self.source_ids):
            if values is not None and len(values) != len(set(values)):
                raise ValueError("Search filters must be unique")
        if (self.start_date is None) != (self.end_date is None):
            raise ValueError("Both period boundaries are required")
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("Search period is reversed")
        return self


class EvidenceProvenance(ContractModel):
    chunk_id: Identifier
    evidence_id: Identifier
    snapshot_id: Identifier
    source_id: Identifier
    source_version: Identifier
    url: HttpUrl
    retrieved_at: Timestamp
    title: Annotated[str, Field(min_length=1, max_length=300)]
    section: Annotated[str, Field(min_length=1, max_length=300)]
    document_date: date | None
    valid_from: date
    valid_to: date
    territory_ids: list[Identifier] = Field(min_length=1, max_length=20)
    content_hash: Identifier

    @model_validator(mode="after")
    def coherent_provenance(self) -> Self:
        if self.valid_from > self.valid_to:
            raise ValueError("Document validity period is reversed")
        if len(set(self.territory_ids)) != len(self.territory_ids):
            raise ValueError("Document territory references must be unique")
        return self


class EvidenceChunk(EvidenceProvenance):
    schema_version: Literal["2"]
    text: Annotated[str, Field(min_length=1, max_length=12000)]
    public_nonpersonal_reviewed: Confirmed

    @model_validator(mode="after")
    def intact_reviewed_text(self) -> Self:
        if not self.text.strip() or self.content_hash != canonical_hash(self.text):
            raise ValueError("Reviewed chunk text/hash mismatch")
        return self


class EvidenceHit(EvidenceProvenance):
    excerpt: Annotated[str, Field(min_length=1, max_length=1200)]


class EvidenceSearchResult(ContractModel):
    status: Literal["available", "unknown"]
    completeness: Literal["partial"] = "partial"
    reason_code: Identifier | None
    hits: list[EvidenceHit] = Field(max_length=5)
    truncated: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def explicit_unavailable(self) -> Self:
        if self.status == "unknown" and (not self.reason_code or self.hits):
            raise ValueError("Unavailable evidence index requires a reason and no invented hits")
        return self
