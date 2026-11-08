"""Server-controlled proposals and checked effects for PRODUCT_SPEC scenarios.

These DTOs validate supplied engine outputs; they do not run scenarios or decide
operational feasibility. HTTP authorization and baseline lookup remain backend work.
"""

from math import isclose
from typing import Annotated, Literal, Self

from pydantic import Field, StrictBool, model_validator

from .base import ContractModel, Count, Identifier, NonNegative, VersionRefs
from .results import EvidenceRef, Metric, Score

Feasibility = Literal["verified", "unverified"]


def _evidence_ids(evidence: list[EvidenceRef], references: list[Identifier]) -> None:
    identifiers = {item.evidence_id for item in evidence}
    if len(identifiers) != len(evidence) or not set(references) <= identifiers:
        raise ValueError("Duplicate evidence identity or unresolved evidence reference")


def _check_feasibility(
    feasibility: Feasibility,
    turnaround: list[Identifier],
    resources: list[Identifier],
    editable: bool,
    limitations: list[Identifier],
) -> None:
    if not turnaround and (
        feasibility != "unverified" or "TURNAROUND_UNVERIFIED" not in limitations
    ):
        raise ValueError("Missing turnaround evidence requires an explicit unverified limitation")
    if feasibility == "verified" and (not resources or not editable):
        raise ValueError(
            "Verified feasibility requires resource evidence and an editable local graph"
        )
    if not editable and "EDITABLE_GRAPH_UNAVAILABLE" not in limitations:
        raise ValueError("External route cannot support a local scenario run")
    if feasibility == "unverified" and not limitations:
        raise ValueError("Unverified feasibility requires visible limitations")


class ScenarioRunOptions(ContractModel):
    """Public body; IDs come from the path and saved server-owned proposal."""

    schema_version: Literal["2"]


class ScenarioRunRequest(ScenarioRunOptions):
    """Internal command after resolving the authorized path and proposal."""

    scenario_id: Identifier
    baseline_analysis_id: Identifier


class FixedParameters(ContractModel):
    service_ids: list[Identifier] = Field(min_length=1)
    shift_seconds: int = Field(strict=True)
    demand_evidence: list[Identifier]
    resource_evidence: list[Identifier]
    turnaround_evidence: list[Identifier]
    resource_constraints: list[Identifier]

    @model_validator(mode="after")
    def unique_services(self) -> Self:
        if len(set(self.service_ids)) != len(self.service_ids):
            raise ValueError("Changed service identities must be unique")
        return self


class ScenarioProposal(ContractModel):
    schema_version: Literal["2"]
    scenario_id: Identifier
    baseline_analysis_id: Identifier
    template_id: Identifier
    operation_type: Literal["coordinate_transfer", "additional_departure"]
    fixed_parameters: FixedParameters
    evidence_refs: list[EvidenceRef]
    assumptions: list[Identifier]
    status: Literal["proposed"]
    feasibility: Feasibility
    versions: VersionRefs
    baseline_result_hash: Identifier
    editable_local_graph: StrictBool
    limitations: list[Identifier]

    @model_validator(mode="after")
    def supported_proposal(self) -> Self:
        params = self.fixed_parameters
        _evidence_ids(
            self.evidence_refs,
            [
                *params.demand_evidence,
                *params.resource_evidence,
                *params.turnaround_evidence,
            ],
        )
        if self.operation_type == "additional_departure" and (
            not params.demand_evidence or not params.resource_evidence
        ):
            raise ValueError("Additional departure requires demonstrated demand and resources")
        _check_feasibility(
            self.feasibility,
            params.turnaround_evidence,
            params.resource_evidence,
            self.editable_local_graph,
            self.limitations,
        )
        return self


class ServiceChange(ContractModel):
    service_id: Identifier
    shift_seconds: int = Field(strict=True)
    whole_trip: StrictBool
    subsequent_stops_updated: StrictBool

    @model_validator(mode="after")
    def entire_service_changed(self) -> Self:
        if not self.whole_trip or not self.subsequent_stops_updated:
            raise ValueError("Schedule changes must affect the whole trip and subsequent stops")
        return self


class ScenarioValues(ContractModel):
    metrics: list[Metric]
    scores: list[Score]

    @model_validator(mode="after")
    def unique_value_ids(self) -> Self:
        if len({m.metric_id for m in self.metrics}) != len(self.metrics):
            raise ValueError("Duplicate metric identity in scenario values")
        scores = {s.score_id for s in self.scores}
        metrics = {m.metric_id for m in self.metrics}
        if len(scores) != len(self.scores):
            raise ValueError("Duplicate score identity in scenario values")
        for score in self.scores:
            for component in score.components:
                if component.id not in (metrics if component.kind == "metric" else scores):
                    raise ValueError("Unresolved scenario score component")
        return self


class ScenarioDelta(ContractModel):
    effect_id: Identifier
    kind: Literal["metric", "score"]
    before_id: Identifier | None
    after_id: Identifier | None
    unit: Identifier
    delta: Annotated[float, Field(strict=True)] | None
    sign_convention: Literal["before_minus_after", "after_minus_before"]
    outcome: Literal["improved", "worsened", "unchanged", "changed", "unknown", "new_connection"]
    reason_code: Identifier | None
    scope: Literal["transport", "pipeline"]

    @model_validator(mode="after")
    def explicit_effect(self) -> Self:
        if self.delta is None:
            if not self.reason_code or self.outcome not in {"unknown", "new_connection"}:
                raise ValueError("Uncomputed delta requires a reason and a nonnumeric outcome")
        elif self.outcome in {"unknown", "new_connection"}:
            raise ValueError(
                "Unknown or newly established connection cannot assert a numeric saving"
            )
        elif (
            (self.outcome == "improved" and self.delta <= 0)
            or (self.outcome == "worsened" and self.delta >= 0)
            or (self.outcome == "unchanged" and self.delta != 0)
            or (self.outcome == "changed" and self.delta == 0)
        ):
            raise ValueError("Effect label contradicts the declared improvement-oriented delta")
        return self


class ScenarioResult(ContractModel):
    schema_version: Literal["2"]
    run_id: Identifier
    scenario_id: Identifier
    baseline_analysis_id: Identifier
    status: Literal["available", "partial", "unknown"]
    before: ScenarioValues
    after: ScenarioValues
    deltas: list[ScenarioDelta] = Field(min_length=1)
    affected_population: NonNegative | None
    affected_sites: Count | None
    worsened_metric_ids: list[Identifier]
    feasibility: Feasibility
    service_changes: list[ServiceChange] = Field(min_length=1)
    versions: VersionRefs
    overlay_graph_id: Identifier
    baseline_result_hash: Identifier
    result_hash: Identifier
    params_hash: Identifier
    operations_hash: Identifier
    evidence_refs: list[EvidenceRef]
    turnaround_evidence: list[Identifier]
    resource_evidence: list[Identifier] = Field(default_factory=list)
    assumptions: list[Identifier]
    limitations: list[Identifier]
    editable_local_graph: StrictBool

    @model_validator(mode="after")
    def comparable_effects(self) -> Self:
        if not self.editable_local_graph or self.overlay_graph_id == self.versions.graph_version:
            raise ValueError("Computed scenario requires a separate editable graph overlay")
        if self.affected_population is not None and self.affected_sites is not None:
            raise ValueError("Affected residents and physical sites are separate populations")
        _check_feasibility(
            self.feasibility,
            self.turnaround_evidence,
            self.resource_evidence,
            self.editable_local_graph,
            self.limitations,
        )
        refs = [*self.turnaround_evidence, *self.resource_evidence]
        for values in (self.before, self.after):
            refs.extend(ref for metric in values.metrics for ref in metric.source_refs)
            refs.extend(ref for score in values.scores for ref in score.evidence_refs)
        _evidence_ids(self.evidence_refs, refs)
        if len({s.service_id for s in self.service_changes}) != len(self.service_changes):
            raise ValueError("Duplicate service change")
        if len({d.effect_id for d in self.deltas}) != len(self.deltas):
            raise ValueError("Duplicate effect identity")
        before_metrics = {m.metric_id: m for m in self.before.metrics}
        after_metrics = {m.metric_id: m for m in self.after.metrics}
        before_scores = {s.score_id: s for s in self.before.scores}
        after_scores = {s.score_id: s for s in self.after.scores}
        worsened: set[str] = set()
        for effect in self.deltas:
            old_values = before_metrics if effect.kind == "metric" else before_scores
            new_values = after_metrics if effect.kind == "metric" else after_scores
            old = old_values.get(effect.before_id) if effect.before_id is not None else None
            new = new_values.get(effect.after_id) if effect.after_id is not None else None
            if (effect.before_id is not None and old is None) or (
                effect.after_id is not None and new is None
            ):
                raise ValueError("Effect references a missing before/after value")
            pipeline = any(item.subject.kind == "pipeline" for item in (old, new) if item)
            if (effect.scope == "pipeline") != pipeline:
                raise ValueError("Pipeline effects must remain explicitly independent")
            if pipeline and effect.kind == "score":
                raise ValueError(
                    "Pipeline utilization is a raw independent metric, not a transport score"
                )
            if effect.kind == "score" and effect.sign_convention != "after_minus_before":
                raise ValueError("Index delta uses after minus before")
            if (
                effect.unit in {"seconds", "minutes", "hours"}
                and effect.sign_convention != "before_minus_after"
            ):
                raise ValueError("Time improvement uses before minus after")
            if effect.outcome == "new_connection":
                if (
                    not old
                    or old.reason_code != "NO_FEASIBLE_ROUTE"
                    or old.completeness != "full"
                    or not new
                    or new.status != "available"
                ):
                    raise ValueError(
                        "New connection requires confirmed prior absence and a confirmed new path"
                    )
                old_unit = old.unit if isinstance(old, Metric) else "points_0_100"
                new_unit = new.unit if isinstance(new, Metric) else "points_0_100"
                if (
                    old.subject != new.subject
                    or old.name != new.name
                    or old_unit != new_unit
                    or effect.unit != old_unit
                ):
                    raise ValueError("New connection must refer to the same pair and measure")
            if effect.delta is not None:
                if not old or not new or old.value is None or new.value is None:
                    raise ValueError("Numeric effect requires numeric before and after")
                old_unit = old.unit if isinstance(old, Metric) else "points_0_100"
                new_unit = new.unit if isinstance(new, Metric) else "points_0_100"
                if (
                    old_unit != new_unit
                    or effect.unit != old_unit
                    or old.subject != new.subject
                    or old.name != new.name
                    or old.completeness != new.completeness
                ):
                    raise ValueError("Effect values are not comparable")
                expected = new.value - old.value
                if effect.sign_convention == "before_minus_after":
                    expected = -expected
                if not isclose(effect.delta, expected, rel_tol=1e-12, abs_tol=1e-12):
                    raise ValueError("Supplied effect disagrees with the saved raw values")
                if effect.scope == "transport" and (
                    effect.kind == "score" or effect.unit in {"seconds", "minutes", "hours"}
                ):
                    expected_outcome = (
                        "improved" if expected > 0 else "worsened" if expected < 0 else "unchanged"
                    )
                    if effect.outcome != expected_outcome:
                        raise ValueError("Index/time worsening cannot be hidden by a neutral label")
            if effect.kind == "metric" and effect.outcome == "worsened":
                assert effect.after_id is not None
                worsened.add(effect.after_id)
        if set(self.worsened_metric_ids) != worsened or len(worsened) != len(
            self.worsened_metric_ids
        ):
            raise ValueError("Worsened metrics must be reported without omissions or duplicates")
        if self.status == "available" and any(d.outcome == "unknown" for d in self.deltas):
            raise ValueError("Unresolved effects require a partial or unknown result")
        return self
