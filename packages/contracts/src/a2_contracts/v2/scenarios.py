"""Server-controlled proposals and checked effects for PRODUCT_SPEC scenarios.

These DTOs validate supplied engine outputs; they do not run scenarios or decide
operational feasibility. HTTP authorization and baseline lookup remain backend work.
"""

from math import isclose
from typing import Annotated, Literal, Self

from pydantic import Field, StrictBool, model_validator

from a2_contracts.common import canonical_hash
from a2_contracts.v2.base import ContractModel, Count, Identifier, NonNegative, VersionRefs
from a2_contracts.v2.journeys import JourneyResult
from a2_contracts.v2.results import EvidenceRef, Metric, Score, validate_score_dependencies

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
        if len(scores) != len(self.scores):
            raise ValueError("Duplicate score identity in scenario values")
        validate_score_dependencies(self.metrics, self.scores)
        return self


class ScenarioDelta(ContractModel):
    effect_id: Identifier
    kind: Literal["metric", "score"]
    before_id: Identifier | None
    after_id: Identifier | None
    unit: Identifier
    delta: Annotated[float, Field(strict=True)] | None
    sign_convention: Literal["before_minus_after", "after_minus_before"]
    outcome: Literal[
        "improved",
        "worsened",
        "unchanged",
        "changed",
        "unknown",
        "new_connection",
        "lost_connection",
    ]
    reason_code: Identifier | None
    scope: Literal["transport", "pipeline"]

    @model_validator(mode="after")
    def explicit_effect(self) -> Self:
        if self.delta is None:
            if not self.reason_code or self.outcome not in {
                "unknown",
                "new_connection",
                "lost_connection",
            }:
                raise ValueError("Uncomputed delta requires a reason and a nonnumeric outcome")
        elif self.outcome in {"unknown", "new_connection", "lost_connection"}:
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


def _measure_key(value: Metric | Score) -> str:
    """Semantic identity excludes values, evidence and scenario-specific route IDs."""
    data: dict[str, object] = {
        "name": value.name,
        "subject": value.subject.model_dump(mode="json"),
        "unit": value.unit if isinstance(value, Metric) else "points_0_100",
        "completeness": value.completeness,
    }
    if isinstance(value, Metric):
        data.update(
            time_kind=value.time_kind,
            population_basis=value.population_basis.model_dump(mode="json")
            if value.population_basis
            else None,
            site_basis=value.site_basis.model_dump(mode="json") if value.site_basis else None,
        )
    else:
        data.update(
            methodology_version=value.methodology_version,
            calculation_kind=value.calculation_kind,
            aggregation_basis=value.aggregation_basis.model_dump(mode="json")
            if value.aggregation_basis
            else None,
        )
    return canonical_hash(data)


def _confirmed_absence(value: Metric | Score) -> bool:
    return (
        value.reason_code == "NO_FEASIBLE_ROUTE"
        and value.completeness == "full"
        and (
            isinstance(value, Metric)
            and value.status == "not_applicable"
            and value.value is None
            and bool(value.source_refs)
            or isinstance(value, Score)
            and value.status == "available"
            and value.value == 0
        )
    )


def _is_time_or_index(value: Metric | Score) -> bool:
    return value.subject.kind != "pipeline" and (
        isinstance(value, Score) or value.unit in {"seconds", "minutes", "hours"}
    )


def _numeric_change(old: Metric | Score, new: Metric | Score) -> bool:
    return (
        _is_time_or_index(old)
        and _measure_key(old) == _measure_key(new)
        and old.status == new.status == "available"
        and old.value != new.value
        and old.value is not None
        and new.value is not None
    )


def _unproven_minimum(value: Metric | Score) -> bool:
    return (
        isinstance(value, Metric)
        and value.name == "minimum_journey_seconds"
        and value.status == "available"
        and value.reason_code == "MINIMUM_TIME_UNPROVEN"
    )


def _check_effect_coverage(
    before: ScenarioValues, after: ScenarioValues, effects: list[ScenarioDelta]
) -> set[str]:
    """Check coverage independently of the worker's selected delta rows.

    Stable IDs anchor pairs first. Explicit one-to-one effects may map renamed
    IDs; otherwise only a unique semantic match is safe. Never pair by ordering
    or by stripping an ID prefix, and never invent an effect for unknown values.
    """
    worsened: set[str] = set()
    for kind in ("metric", "score"):
        old_items: list[Metric | Score] = (
            list(before.metrics) if kind == "metric" else list(before.scores)
        )
        new_items: list[Metric | Score] = (
            list(after.metrics) if kind == "metric" else list(after.scores)
        )

        def identity(value: Metric | Score) -> str:
            return value.metric_id if isinstance(value, Metric) else value.score_id

        old_values = {identity(item): item for item in old_items}
        new_values = {identity(item): item for item in new_items}
        old_keys = {identifier: _measure_key(item) for identifier, item in old_values.items()}
        new_keys = {identifier: _measure_key(item) for identifier, item in new_values.items()}
        pairs = {identifier: identifier for identifier in old_values.keys() & new_values.keys()}
        reverse = dict(pairs)
        covered: dict[tuple[str, str], ScenarioDelta] = {}
        used_old: set[str] = set()
        used_new: set[str] = set()
        for effect in effects:
            if effect.kind != kind:
                continue
            old_id, new_id = effect.before_id, effect.after_id
            if old_id is not None:
                if old_id in used_old:
                    raise ValueError("A before value must not participate in multiple effects")
                used_old.add(old_id)
            if new_id is not None:
                if new_id in used_new:
                    raise ValueError("An after value must not participate in multiple effects")
                used_new.add(new_id)
            if old_id is None or new_id is None:
                continue
            if (old_id in pairs and pairs[old_id] != new_id) or (
                new_id in reverse and reverse[new_id] != old_id
            ):
                raise ValueError("Effects must preserve stable before/after identities")
            pairs[old_id] = new_id
            reverse[new_id] = old_id
            covered[(old_id, new_id)] = effect
        # Stable and explicitly comparable pairs are resolved before checking
        # alternatives, so aliases cannot make validation depend on effect order.
        resolved = {
            old_id: new_id
            for old_id, new_id in pairs.items()
            if old_id == new_id or old_keys[old_id] == new_keys[new_id]
        }
        resolved_new = set(resolved.values())
        old_meanings = {key for identifier, key in old_keys.items() if identifier not in resolved}
        new_meanings = {
            key for identifier, key in new_keys.items() if identifier not in resolved_new
        }
        for old_id, new_id in pairs.items():
            if (
                old_keys[old_id] != new_keys[new_id]
                and old_id != new_id
                and (old_keys[old_id] in new_meanings or new_keys[new_id] in old_meanings)
            ):
                raise ValueError("Incomparable effect must not hide another matching measure")
        old_groups: dict[str, list[str]] = {}
        new_groups: dict[str, list[str]] = {}
        for identifier, item in old_values.items():
            if identifier not in pairs and _is_time_or_index(item):
                old_groups.setdefault(old_keys[identifier], []).append(identifier)
        for identifier, item in new_values.items():
            if identifier not in reverse and _is_time_or_index(item):
                new_groups.setdefault(new_keys[identifier], []).append(identifier)
        for key in old_groups.keys() & new_groups.keys():
            old_ids, new_ids = old_groups[key], new_groups[key]
            if len(old_ids) == len(new_ids) == 1:
                pairs[old_ids[0]] = new_ids[0]
            else:
                old_numbers = {
                    old_values[a].value
                    for a in old_ids
                    if old_values[a].status == "available" and old_values[a].value is not None
                }
                new_numbers = {
                    new_values[b].value
                    for b in new_ids
                    if new_values[b].status == "available" and new_values[b].value is not None
                }
                if old_numbers and new_numbers and len(old_numbers | new_numbers) > 1:
                    raise ValueError(
                        "Ambiguous changed measures require stable IDs or explicit effects"
                    )
        # A task must remain represented even when its connection disappears.
        # A null-valued, scoped placeholder distinguishes absence from missing data.
        if any(
            _is_time_or_index(item) and identifier not in pairs
            for identifier, item in old_values.items()
        ):
            raise ValueError("Every prior time/index task requires an after representation")
        paired_new = set(pairs.values())
        if any(
            _is_time_or_index(item)
            and identifier not in paired_new
            and (item.status == "available" or _confirmed_absence(item))
            for identifier, item in new_values.items()
        ):
            raise ValueError("Every new time/index task requires a before representation")
        for old_id, new_id in pairs.items():
            old, new = old_values[old_id], new_values[new_id]
            task_effect = covered.get((old_id, new_id))
            if _unproven_minimum(old) or _unproven_minimum(new):
                if (
                    task_effect is None
                    or task_effect.outcome != "unknown"
                    or task_effect.delta is not None
                ):
                    raise ValueError("Unproven minimum requires an explicit unknown effect")
                continue
            if _is_time_or_index(old) or _is_time_or_index(new):
                changed_state = (
                    old.status != new.status
                    or old.value != new.value
                    or old.reason_code != new.reason_code
                    or old_keys[old_id] != new_keys[new_id]
                )
                if changed_state and task_effect is None:
                    raise ValueError("Every changed task requires an explicit effect")
                if old_keys[old_id] != new_keys[new_id]:
                    if (
                        task_effect is None
                        or task_effect.outcome != "unknown"
                        or task_effect.delta is not None
                    ):
                        raise ValueError(
                            "Changed task semantics require an explicit unknown effect"
                        )
                elif (
                    old.status == "available"
                    and not _confirmed_absence(old)
                    and _confirmed_absence(new)
                ):
                    if task_effect is None or task_effect.outcome != "lost_connection":
                        raise ValueError(
                            "Confirmed connection loss requires a lost_connection event"
                        )
                    if isinstance(new, Metric):
                        worsened.add(new_id)
                elif (
                    _confirmed_absence(old)
                    and new.status == "available"
                    and not _confirmed_absence(new)
                ):
                    if task_effect is None or task_effect.outcome != "new_connection":
                        raise ValueError(
                            "Confirmed connection appearance requires a new_connection event"
                        )
                elif old.status != new.status:
                    if task_effect is None or task_effect.outcome != "unknown":
                        raise ValueError("Uncertain task transitions require an unknown effect")
            if not _numeric_change(old, new):
                continue
            checked_effect = covered.get((old_id, new_id))
            if checked_effect is not None and checked_effect.outcome in {
                "new_connection",
                "lost_connection",
            }:
                # Confirmed absence -> connection is a checked event, never an invented saving.
                continue
            if checked_effect is None or checked_effect.delta is None:
                raise ValueError("Every comparable changed time or index requires a numeric effect")
            # Arithmetic and direction were checked against these exact values by the caller.
            if (
                isinstance(old, Metric)
                and old.value is not None
                and new.value is not None
                and new.value > old.value
            ):
                worsened.add(new_id)
    return worsened


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
    after_journeys: list[JourneyResult] = Field(
        default_factory=list, max_length=10000, exclude_if=lambda value: not value
    )

    @model_validator(mode="after")
    def comparable_effects(self) -> Self:
        items: list[Metric | Score] = [
            *self.before.metrics,
            *self.before.scores,
            *self.after.metrics,
            *self.after.scores,
        ]
        if self.status == "available" and any(
            item.status not in ("available", "not_applicable") or item.completeness != "full"
            for item in items
        ):
            raise ValueError("Unresolved or partial scenario values require a partial result")
        if self.status == "unknown" and any(item.value is not None for item in items):
            raise ValueError("Known scenario values must remain visible in a partial result")
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
            for metric in values.metrics:
                if _unproven_minimum(metric) and (
                    metric.completeness != "partial"
                    or not metric.source_refs
                    or metric.journey_id is None
                ):
                    raise ValueError("Unproven minimum requires a partial evidence-bound journey")
        _evidence_ids(self.evidence_refs, refs)
        after_journeys = {journey.journey_id: journey for journey in self.after_journeys}
        if len(after_journeys) != len(self.after_journeys):
            raise ValueError("Duplicate after journey identity")
        for metric in self.after.metrics:
            if _unproven_minimum(metric):
                journey = after_journeys.get(metric.journey_id or "")
                if (
                    journey is None
                    or journey.status != "available"
                    or journey.completeness != "partial"
                    or journey.scope != "urban"
                    or journey.point_id != metric.subject.point_id
                    or journey.time_kind != metric.time_kind
                    or journey.total_seconds != metric.value
                ):
                    raise ValueError("Unproven after minimum requires its matching partial journey")
        used_journeys = {metric.journey_id for metric in self.after.metrics}
        if not after_journeys.keys() <= used_journeys:
            raise ValueError("After journey must support a saved after metric")
        for journey in self.after_journeys:
            if journey.snapshot_id != self.versions.snapshot_id:
                raise ValueError("After journey snapshot differs from scenario")
            journey_refs = [*journey.evidence_refs]
            journey_refs.extend(ref for leg in journey.legs for ref in leg.evidence_refs)
            journey_refs.extend(
                ref
                for leg in journey.legs
                if leg.geometry is not None
                for ref in leg.geometry.evidence_refs
            )
            if journey.geometry is not None:
                journey_refs.extend(journey.geometry.evidence_refs)
            _evidence_ids(self.evidence_refs, journey_refs)
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
            if effect.outcome in {"new_connection", "lost_connection"}:
                absent, present = (old, new) if effect.outcome == "new_connection" else (new, old)
                if (
                    absent is None
                    or present is None
                    or not _confirmed_absence(absent)
                    or present.status != "available"
                    or _confirmed_absence(present)
                    or present.completeness != "full"
                ):
                    raise ValueError(
                        "Connection event requires confirmed absence and a confirmed path"
                    )
                old_unit = old.unit if isinstance(old, Metric) else "points_0_100"
                if _measure_key(absent) != _measure_key(present) or effect.unit != old_unit:
                    raise ValueError("Connection event must refer to the same task and measure")
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
                    or _measure_key(old) != _measure_key(new)
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
        worsened.update(_check_effect_coverage(self.before, self.after, self.deltas))
        if set(self.worsened_metric_ids) != worsened or len(worsened) != len(
            self.worsened_metric_ids
        ):
            raise ValueError("Worsened metrics must be reported without omissions or duplicates")
        if self.status == "available" and any(d.outcome == "unknown" for d in self.deltas):
            raise ValueError("Unresolved effects require a partial or unknown result")
        return self
