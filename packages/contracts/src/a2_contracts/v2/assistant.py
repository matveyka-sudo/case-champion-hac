"""Grounded explanations of an immutable saved analysis, never calculation commands."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from .base import ContractModel, Identifier
from .map_data import MapEntityKind
from .results import EvidenceRef

Text = Annotated[str, Field(min_length=1, max_length=1000)]


class ExplanationSelection(ContractModel):
    entity_kind: MapEntityKind
    entity_id: Annotated[str, Field(min_length=1, max_length=200)]


class ExplanationRequest(ContractModel):
    schema_version: Literal["2"] = "2"
    question: Annotated[str, Field(min_length=1, max_length=2000)] | None = None
    selection: ExplanationSelection | None = None

    @model_validator(mode="after")
    def nonblank_question(self) -> Self:
        if self.question is not None and (
            not self.question.strip() or any(ord(c) < 32 and c not in "\n\t" for c in self.question)
        ):
            raise ValueError("Question must be nonblank text without control characters")
        return self


class NumericClaim(ContractModel):
    metric_id: Identifier
    value: Annotated[float, Field(strict=True)] | None
    unit: Identifier


class ScoreClaim(ContractModel):
    score_id: Identifier
    value: Annotated[float, Field(strict=True, ge=0, le=100)] | None


class Finding(ContractModel):
    text: Text
    metric_ids: list[Identifier] = Field(max_length=20)
    evidence_refs: list[Identifier] = Field(max_length=50)
    claims: list[NumericClaim] = Field(max_length=20)
    score_ids: list[Identifier] = Field(
        default_factory=list, max_length=20, exclude_if=lambda value: not value
    )
    score_claims: list[ScoreClaim] = Field(
        default_factory=list, max_length=20, exclude_if=lambda value: not value
    )

    @model_validator(mode="after")
    def referenced_claims(self) -> Self:
        ids = [claim.metric_id for claim in self.claims]
        if len(set(ids)) != len(ids) or not set(ids) <= set(self.metric_ids):
            raise ValueError("Claims require unique referenced metric identities")
        if len(set(self.metric_ids)) != len(self.metric_ids):
            raise ValueError("Duplicate metric reference")
        if not self.metric_ids and not self.score_ids:
            raise ValueError("Finding requires saved metric or score references")
        score_ids = [claim.score_id for claim in self.score_claims]
        if len(set(score_ids)) != len(score_ids) or not set(score_ids) <= set(self.score_ids):
            raise ValueError("Score claims require unique referenced score identities")
        if len(set(self.score_ids)) != len(self.score_ids):
            raise ValueError("Duplicate score reference")
        return self


class ScenarioSuggestion(ContractModel):
    template_id: Identifier
    rationale: Text
    evidence_refs: list[Identifier] = Field(max_length=50)


class ExplanationContent(ContractModel):
    analysis_id: Identifier
    findings: list[Finding] = Field(max_length=20)
    proposals: list[ScenarioSuggestion] = Field(max_length=10)
    evidence_refs: list[Identifier] = Field(max_length=50)
    limitations: list[Text] = Field(max_length=20)


class Explanation(ExplanationContent):
    schema_version: Literal["2"] = "2"
    analysis_result_hash: Identifier
    request_id: Identifier
    status: Literal["generated", "template"]
    model_id: Identifier | None
    reason_code: Identifier | None
    # Collected by the server from scoped retrieval tools, never supplied by the model.
    retrieved_evidence: list[EvidenceRef] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def explicit_fallback(self) -> Self:
        if self.status == "template" and (self.model_id is not None or not self.reason_code):
            raise ValueError("Template fallback requires a reason and no model attribution")
        if self.status == "generated" and (self.model_id is None or self.reason_code is not None):
            raise ValueError("Generated explanation requires its model and no fallback reason")
        if self.status == "generated" and not self.findings:
            raise ValueError("Generated explanation requires grounded findings")
        return self
