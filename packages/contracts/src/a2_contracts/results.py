"""Engine outputs: validate supplied results without computing the metrics."""

from math import isclose
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from .common import (
    ConstraintState,
    ContractModel,
    Count,
    Feasibility,
    Identifier,
    Positive,
    ProfileId,
    Provenance,
    RunStatus,
    Version,
    validate_feasibility,
)

Score = Annotated[float, Field(ge=0, le=100)]


class CityScore(ContractModel):
    origin_id: Identifier
    score: Score | None
    score_lower: Score
    score_upper: Score
    task_count: Annotated[int, Field(strict=True, gt=0)]
    resolved_weight_share: Annotated[float, Field(ge=0, le=1)]

    @model_validator(mode="after")
    def valid_interval(self) -> Self:
        if self.score_lower > self.score_upper:
            raise ValueError("Reversed score interval")
        exact = self.score_lower == self.score_upper
        if exact and self.score != self.score_lower:
            raise ValueError("Exact bounds require the same scalar score")
        if not exact and self.score is not None:
            raise ValueError("Partial bounds require score=null")
        return self


class CaseResult(ContractModel):
    case_id: Identifier
    weight: Positive
    overall_feasibility: Feasibility
    constraint_results: dict[Identifier, ConstraintState]
    reason_codes: list[Identifier]
    journey_ids: list[Identifier]
    evidence_ids: list[Identifier]
    assumption_ids: list[Identifier]

    @model_validator(mode="after")
    def consistent_outcome(self) -> Self:
        validate_feasibility(self.overall_feasibility, self.constraint_results)
        if self.overall_feasibility == "pass" and not self.journey_ids:
            raise ValueError("A passed case requires confirmed journey IDs")
        return self


class ProfileResult(ContractModel):
    run_id: Identifier
    status: RunStatus
    bundle_id: Identifier
    profile_id: ProfileId
    profile_version: Version
    task_set_hash: Identifier
    code_version: Identifier
    params_hash: Identifier
    result_hash: Identifier
    unit: Literal["points_0_100"]
    provenance: Provenance
    evidence_ids: list[Identifier]
    assumption_ids: list[Identifier]
    warnings: list[Identifier]
    cities: list[CityScore]
    case_results: list[CaseResult] | None = None
    reason_codes: list[Identifier] = Field(default_factory=list)
    missing_fields: list[Identifier] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_result(self) -> Self:
        for name in ("bundle_id", "profile_id", "profile_version", "task_set_hash", "code_version"):
            if getattr(self, name) != getattr(self.provenance, name):
                raise ValueError(f"Result/provenance mismatch: {name}")
        if len({city.origin_id for city in self.cities}) != len(self.cities):
            raise ValueError("Duplicate origin in result")
        if self.status == "insufficient_data":
            if self.cities or self.case_results or not self.reason_codes or not self.missing_fields:
                raise ValueError("Insufficient data has no scores and requires gaps and reasons")
        elif not self.cities:
            raise ValueError("Calculated results require cities")
        if self.status == "ok" and any(c.score is None for c in self.cities):
            raise ValueError("ok requires exact scores")
        return self


class ComparisonEntry(ContractModel):
    profile_id: ProfileId
    profile_version: Version
    task_set_hash: Identifier
    base_run_id: Identifier
    scenario_run_id: Identifier | None
    origin_id: Identifier | None
    status: RunStatus
    before: tuple[Score, Score] | None
    after: tuple[Score, Score] | None
    lower_bound_change: float | None
    upper_bound_change: float | None
    delta_interval_conservative: tuple[float, float] | None
    confirmed_improved_cases: Count | None
    confirmed_worse_cases: Count | None
    unresolved_cases: Count | None
    worst_worsened_case_ids: list[Identifier]
    reason: Identifier | None

    @model_validator(mode="after")
    def consistent_comparison(self) -> Self:
        numbers = (
            self.before,
            self.after,
            self.lower_bound_change,
            self.upper_bound_change,
            self.delta_interval_conservative,
            self.confirmed_improved_cases,
            self.confirmed_worse_cases,
            self.unresolved_cases,
        )
        if self.status == "insufficient_data":
            if self.scenario_run_id is not None or any(n is not None for n in numbers):
                raise ValueError("Uncomputed comparison cannot contain effects")
            if not self.reason:
                raise ValueError("Uncomputed comparison requires a reason")
        else:
            if (
                self.origin_id is None
                or self.scenario_run_id is None
                or any(n is None for n in numbers)
            ):
                raise ValueError("Computed comparison requires versions, runs and effects")
            assert self.before is not None and self.after is not None
            if self.before[0] > self.before[1] or self.after[0] > self.after[1]:
                raise ValueError("Reversed comparison interval")
            expected = (
                self.after[0] - self.before[0],
                self.after[1] - self.before[1],
                self.after[0] - self.before[1],
                self.after[1] - self.before[0],
            )
            assert self.lower_bound_change is not None and self.upper_bound_change is not None
            assert self.delta_interval_conservative is not None
            actual = (
                self.lower_bound_change,
                self.upper_bound_change,
                *self.delta_interval_conservative,
            )
            if not all(
                isclose(a, b, rel_tol=1e-12, abs_tol=1e-12) for a, b in zip(actual, expected)
            ):
                raise ValueError("Supplied comparison effects disagree with score intervals")
            if self.status == "ok" and (
                self.before[0] != self.before[1] or self.after[0] != self.after[1]
            ):
                raise ValueError("Uncertain intervals require partial status")
        return self
