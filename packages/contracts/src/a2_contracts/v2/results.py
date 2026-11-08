"""Metric availability, score completeness and immutable four-kind results."""

from typing import Annotated, Literal, Self

from pydantic import Field, HttpUrl, model_validator

from a2_contracts.common import utc
from a2_contracts.v2.base import (
    AnalysisKind,
    Availability,
    Category,
    Completeness,
    ContractModel,
    Identifier,
    MethodologyVersion,
    Mode,
    NonNegative,
    Timestamp,
    TrafficMode,
    VersionRefs,
)
from a2_contracts.v2.journeys import CountResult, JourneyResult, LastReturn, ReferenceResult
from a2_contracts.v2.map_data import AnalysisMapData
from a2_contracts.v2.requests import AnalysisRequest


class EvidenceRef(ContractModel):
    evidence_id: Identifier
    source_id: Identifier
    url: HttpUrl
    retrieved_at: Timestamp
    observed_at: Timestamp | None
    section: Identifier


class SubjectRef(ContractModel):
    kind: Literal[
        "sample_point",
        "territory",
        "category",
        "freight_site",
        "freight_node",
        "connection",
        "pipeline",
        "city_pair",
    ]
    territory_id: Identifier | None = None
    origin_id: Identifier | None = None
    destination_id: Identifier | None = None
    point_id: Identifier | None = None
    site_id: Identifier | None = None
    node_id: Identifier | None = None
    category: Category | None = None
    mode: Mode | None = None

    @model_validator(mode="after")
    def identifiable_subject(self) -> Self:
        required = {
            "sample_point": ("point_id",),
            "territory": ("territory_id",),
            "category": ("category",),
            "freight_site": ("site_id",),
            "freight_node": ("node_id",),
            "connection": ("origin_id", "destination_id"),
            "pipeline": ("origin_id", "destination_id"),
            "city_pair": ("origin_id", "destination_id"),
        }
        if any(getattr(self, name) is None for name in required[self.kind]):
            raise ValueError("Subject kind requires its identifying fields")
        return self


class PopulationBasis(ContractModel):
    population: NonNegative | None
    population_method: Identifier
    year: Annotated[int, Field(strict=True, ge=1900, le=2200)] | None
    coverage: Annotated[float, Field(ge=0, le=1)]
    unit: Literal["persons"]
    unknown_positive_weight: bool = Field(strict=True)


class SiteBasis(ContractModel):
    site_count: Annotated[int, Field(strict=True, ge=0)]
    weighting: Literal["equal_physical_sites"]
    coverage: Annotated[float, Field(ge=0, le=1)]


class Metric(ContractModel):
    metric_id: Identifier
    name: Identifier
    value: Annotated[float, Field(strict=True)] | None
    unit: Identifier
    status: Literal["available", "unknown", "not_applicable", "methodology_pending", "pending"]
    reason_code: Identifier | None
    source_refs: list[Identifier]
    subject: SubjectRef
    population_basis: PopulationBasis | None = None
    site_basis: SiteBasis | None = None
    completeness: Completeness
    dependency_job_id: Identifier | None = None
    time_kind: Literal["movement", "complete_chain"] | None = None
    journey_id: Identifier | None = None

    @model_validator(mode="after")
    def available_value(self) -> Self:
        if (self.status == "available") != (self.value is not None):
            raise ValueError("Available metric requires a number; other states require null")
        if self.status != "available" and not self.reason_code:
            raise ValueError("Non-numeric metric requires a machine-readable reason")
        if self.status == "pending" and self.dependency_job_id is None:
            raise ValueError("Pending metric requires its separate job")
        return self


class ComponentRef(ContractModel):
    kind: Literal["metric", "score"]
    id: Identifier


class Score(ContractModel):
    score_id: Identifier
    name: Identifier
    value: Annotated[float, Field(strict=True, ge=0, le=100)] | None
    status: Availability
    completeness: Completeness
    methodology_version: MethodologyVersion
    reason_code: Identifier | None
    components: list[ComponentRef]
    evidence_refs: list[Identifier]
    subject: SubjectRef
    ranking_eligible: bool = Field(strict=True)
    aggregation_basis: PopulationBasis | SiteBasis | None = None
    reference_ids: list[Identifier] = Field(
        default_factory=list, exclude_if=lambda value: not value
    )

    @model_validator(mode="after")
    def genuine_score(self) -> Self:
        if self.subject.kind == "pipeline":
            raise ValueError("Pipeline facts are independent raw metrics, not transport scores")
        if (self.status == "available") != (self.value is not None):
            raise ValueError("Only available scores have numeric values")
        if self.status != "available" and not self.reason_code:
            raise ValueError("Unknown/inapplicable score requires a reason")
        if self.reason_code == "NO_FEASIBLE_ROUTE" and (
            self.value != 0 or self.completeness != "full" or not self.evidence_refs
        ):
            raise ValueError("Confirmed absence requires full scope, evidence and available zero")
        if len(set(self.reference_ids)) != len(self.reference_ids):
            raise ValueError("Duplicate score reference dependency")
        if self.status == "available" and self.completeness == "full":
            basis = self.aggregation_basis
            if isinstance(basis, PopulationBasis) and (
                basis.population is None or basis.coverage < 1 or basis.unknown_positive_weight
            ):
                raise ValueError("Full index requires complete known positive population weights")
            if isinstance(basis, SiteBasis) and basis.coverage < 1:
                raise ValueError("Full index requires the complete physical-site sample")
        if self.ranking_eligible:
            if self.status != "available" or self.completeness != "full":
                raise ValueError("Only full available scores may enter a ranking")
            if self.subject.kind == "territory" and self.aggregation_basis is None:
                raise ValueError("Territorial ranking requires its declared population/site basis")
            if isinstance(self.aggregation_basis, SiteBasis) and (
                self.aggregation_basis.site_count == 0 or self.aggregation_basis.coverage < 1
            ):
                raise ValueError("Unknown/incomplete or zero site denominator cannot enter ranking")
            if isinstance(self.aggregation_basis, PopulationBasis) and (
                self.aggregation_basis.population is None
                or self.aggregation_basis.unknown_positive_weight
                or self.aggregation_basis.coverage < 1
            ):
                raise ValueError(
                    "Unknown population or positive weights block full territorial ranking"
                )
        denominator = (
            self.aggregation_basis.population
            if isinstance(self.aggregation_basis, PopulationBasis)
            else self.aggregation_basis.site_count
            if isinstance(self.aggregation_basis, SiteBasis)
            else None
        )
        if denominator == 0 and (
            self.value is not None
            or self.status != "not_applicable"
            or self.reason_code != "ZERO_WEIGHT"
        ):
            raise ValueError("Zero aggregation denominator is not_applicable, never numeric")
        return self


def validate_score_dependencies(metrics: list[Metric], scores: list[Score]) -> None:
    """Shared analysis/scenario invariants, without computing any domain formula."""
    metric_map = {metric.metric_id: metric for metric in metrics}
    score_map = {score.score_id: score for score in scores}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(identifier: str) -> None:
        if identifier in visiting:
            raise ValueError("Circular score component graph")
        if identifier in visited:
            return
        visiting.add(identifier)
        score = score_map[identifier]
        for component in score.components:
            dependencies = metric_map if component.kind == "metric" else score_map
            if component.id not in dependencies:
                raise ValueError("Unknown score component")
            dependency = dependencies[component.id]
            if (
                score.status == "available"
                and score.completeness == "full"
                and (dependency.status != "available" or dependency.completeness != "full")
            ):
                raise ValueError("Full numeric score cannot depend on missing/partial components")
            if component.kind == "score":
                visit(component.id)
        visiting.remove(identifier)
        visited.add(identifier)

    for identifier in score_map:
        visit(identifier)


class AnalysisResult(ContractModel):
    schema_version: Literal["2"]
    analysis_id: Identifier
    analysis_kind: AnalysisKind
    request: AnalysisRequest
    snapshot_id: Identifier
    methodology_version: MethodologyVersion
    config_hash: Identifier
    traffic_mode: TrafficMode
    status: Literal["available", "partial", "unknown"]
    metrics: list[Metric]
    scores: list[Score]
    evidence_refs: list[EvidenceRef]
    limitations: list[Identifier]
    versions: VersionRefs
    journeys: list[JourneyResult]
    references: list[ReferenceResult]
    counts: list[CountResult]
    last_returns: list[LastReturn]
    result_hash: Identifier
    map_data: AnalysisMapData = Field(
        default_factory=AnalysisMapData, exclude_if=lambda value: value == AnalysisMapData()
    )

    @model_validator(mode="after")
    def resolved_consistent_result(self) -> Self:
        if (
            self.request.analysis_kind != self.analysis_kind
            or self.request.traffic_mode != self.traffic_mode
        ):
            raise ValueError("Saved request kind/traffic differs from result")
        if self.request.snapshot_id != self.snapshot_id or self.request.methodology is None:
            raise ValueError(
                "Saved request must contain its resolved immutable snapshot/methodology"
            )
        for field in ("snapshot_id", "methodology_version", "config_hash"):
            if getattr(self.versions, field) != getattr(self, field):
                raise ValueError("Result and version references disagree")
        if (
            self.request.methodology.version != self.methodology_version
            or self.request.methodology.config_hash != self.config_hash
        ):
            raise ValueError("Saved methodology reference differs from result")
        metrics = {m.metric_id: m for m in self.metrics}
        scores = {s.score_id: s for s in self.scores}
        evidence = {e.evidence_id for e in self.evidence_refs}
        if (
            len(metrics) != len(self.metrics)
            or len(scores) != len(self.scores)
            or len(evidence) != len(self.evidence_refs)
        ):
            raise ValueError("Duplicate metric/score/evidence identity")
        for item in self.metrics:
            if not set(item.source_refs) <= evidence:
                raise ValueError("Unknown metric evidence reference")
        validate_score_dependencies(self.metrics, self.scores)
        for score in self.scores:
            if (
                score.methodology_version != self.methodology_version
                or not set(score.evidence_refs) <= evidence
            ):
                raise ValueError("Unknown score evidence or methodology")
            if score.ranking_eligible and score.subject.kind == "territory":
                expected = PopulationBasis if self.analysis_kind == "urban_passenger" else SiteBasis
                if self.analysis_kind in ("urban_passenger", "urban_cargo") and not isinstance(
                    score.aggregation_basis, expected
                ):
                    raise ValueError("Territorial weighting must match passenger/cargo meaning")
            if (
                self.analysis_kind == "intercity_cargo"
                and score.status == "available"
                and not any(
                    c.kind == "metric" and metrics[c.id].time_kind == "complete_chain"
                    for c in score.components
                )
            ):
                raise ValueError("Cargo delivery score needs a complete-chain time operand")
        journeys = {j.journey_id: j for j in self.journeys}
        references = {r.reference_id: r for r in self.references}
        if len(journeys) != len(self.journeys) or len(references) != len(self.references):
            raise ValueError("Duplicate journey/reference identity")

        def evidence_links(values: list[str]) -> None:
            if not set(values) <= evidence:
                raise ValueError("Unknown nested evidence reference")

        for feature in self.map_data.features:
            evidence_links(feature.source_refs)

        for reference in self.references:
            evidence_links(reference.calibration_evidence)
            if reference.graph_version != self.versions.graph_version:
                raise ValueError("Reference graph mismatch")
        for journey in self.journeys:
            if journey.snapshot_id != self.snapshot_id or journey.traffic_mode != self.traffic_mode:
                raise ValueError("Journey snapshot/traffic differs from analysis")
            evidence_links(journey.evidence_refs)
            for leg in journey.legs:
                evidence_links(leg.evidence_refs)
            if journey.geometry:
                evidence_links(journey.geometry.evidence_refs)
            if journey.reference_id is not None:
                if journey.reference_id not in references:
                    raise ValueError("Unknown journey reference")
                boundary = references[journey.reference_id].boundary_node_sets
                if (
                    journey.origin_node_id not in boundary.origin_node_ids
                    or journey.destination_node_id not in boundary.destination_node_ids
                ):
                    raise ValueError("Journey/reference boundary mismatch")
        for metric in self.metrics:
            if metric.journey_id is not None and metric.journey_id not in journeys:
                raise ValueError("Unknown metric journey")

        # A reference affects an index, not the independently observed raw time.
        # Legacy score chains carry their reference through Metric -> Journey;
        # reference_ids also expresses a direct denominator dependency explicitly.
        def check_required_dependencies(score: Score) -> None:
            full = score.status == "available" and score.completeness == "full"
            required_references = set(score.reference_ids)
            for component in score.components:
                if component.kind != "metric":
                    continue
                metric = metrics[component.id]
                if metric.journey_id is None:
                    continue
                journey = journeys[metric.journey_id]
                if full and (journey.status != "available" or journey.completeness != "full"):
                    raise ValueError("Full index requires its complete available journey")
                if journey.reference_id is not None:
                    required_references.add(journey.reference_id)
            for identifier in required_references:
                if identifier not in references:
                    raise ValueError("Unknown score reference dependency")
                reference = references[identifier]
                if full and (reference.status != "available" or reference.completeness != "full"):
                    raise ValueError("Full index requires its complete available reference")

        for score in self.scores:
            check_required_dependencies(score)
        if len({count.metric_id for count in self.counts}) != len(self.counts):
            raise ValueError("Duplicate count metric identity")
        for count in self.counts:
            if count.metric_id not in metrics:
                raise ValueError("Unknown count metric")
            metric = metrics[count.metric_id]
            if (
                metric.unit != "objects"
                or metric.value != count.value
                or metric.status != count.status
                or metric.reason_code != count.reason_code
                or metric.dependency_job_id != count.dependency_job_id
            ):
                raise ValueError("Count differs from its canonical metric")
            evidence_links(count.evidence_refs)
        for last_return in self.last_returns:
            evidence_links(last_return.evidence_refs)
            if last_return.journey_id is not None:
                if last_return.journey_id not in journeys:
                    raise ValueError("Unknown last-return journey")
                linked = journeys[last_return.journey_id]
                if (
                    linked.point_id != last_return.point_id
                    or linked.destination_object_id != last_return.destination_object_id
                ):
                    raise ValueError("Last return belongs to a different point/object")
                if last_return.status == "available":
                    rides = [
                        leg for leg in linked.legs if leg.kind == "ride" and leg.mode != "walk"
                    ]
                    if (
                        linked.status != "available"
                        or not rides
                        or last_return.leave_at is None
                        or last_return.first_departure_at is None
                        or last_return.arrival_at is None
                        or linked.arrival_at is None
                        or utc(last_return.leave_at) != utc(linked.requested_departure_at)
                        or utc(last_return.first_departure_at) != utc(rides[0].start_at)
                        or utc(last_return.arrival_at) != utc(linked.arrival_at)
                    ):
                        raise ValueError(
                            "Available last return requires matching confirmed journey times"
                        )
        items: list[Metric | Score] = [*self.metrics, *self.scores]
        if not items:
            raise ValueError(
                "Analysis requires explicit metrics or scores, including unknown states"
            )
        if self.status == "available" and any(
            item.status not in ("available", "not_applicable") or item.completeness != "full"
            for item in items
        ):
            raise ValueError("Unresolved/partial components require a partial result")
        if self.status == "unknown" and any(item.value is not None for item in items):
            raise ValueError("Known raw values must remain visible in a partial result")
        return self
