"""Check newly computed scenario chains against the saved task and reference.

This validates engine output at publication. It never routes, reconstructs a
schedule, recalibrates a reference or changes the stored baseline.
"""

from math import isclose

from a2_contracts.common import utc
from a2_contracts.v2.journeys import JourneyResult
from a2_contracts.v2.requests import IntercityBase, UrbanPassengerRequest
from a2_contracts.v2.results import (
    AnalysisResult,
    Metric,
    PopulationBasis,
    validate_reference_applicability,
)
from a2_contracts.v2.scenarios import ScenarioResult

from a2_api.core.errors import DomainError
from a2_api.core.scenario_scope import ScenarioEndpointScope


def _urban_journey(
    journey: JourneyResult,
    baseline: AnalysisResult,
    endpoint_scope: ScenarioEndpointScope | None,
) -> None:
    request = baseline.request
    if not isinstance(request, UrbanPassengerRequest) or endpoint_scope is None:
        raise DomainError(
            409, "incompatible_versions", "Scenario urban endpoint scope is unavailable"
        )
    if (
        endpoint_scope.snapshot_id != baseline.snapshot_id
        or endpoint_scope.city_id != request.city_id
    ):
        raise DomainError(409, "incompatible_versions", "Scenario urban endpoint scope differs")
    point, destination = journey.point_id, journey.destination_object_id
    if (
        point is None
        or destination is None
        or point not in endpoint_scope.point_origins
        or destination not in endpoint_scope.destination_nodes
        or journey.origin_node_id != endpoint_scope.point_origins[point]
        or journey.destination_node_id != endpoint_scope.destination_nodes[destination]
        or not set(request.categories) & set(endpoint_scope.destination_categories[destination])
        or journey.reference_id is not None
    ):
        raise DomainError(409, "incompatible_versions", "Scenario urban endpoint binding differs")
    # These are the public urban engine's mode families; physical service modes
    # still require accepted input bindings before the calculation is queued.
    allowed_modes = {
        "walk": {"walk"},
        "public_transport": {"walk", "public_transport", "rail", "water"},
        "private_car": {"walk", "private_car"},
    }[request.passenger_mode]
    if any(
        leg.mode not in allowed_modes
        or leg.kind not in {"walk", "ride", "wait_initial", "wait_transfer"}
        or (leg.kind == "walk") != (leg.mode == "walk")
        or (request.passenger_mode == "walk" and leg.kind != "walk")
        for leg in journey.legs
    ):
        raise DomainError(409, "incompatible_versions", "Scenario urban passenger mode differs")
    if not journey.legs:
        raise DomainError(
            409, "incompatible_versions", "Scenario urban endpoint chain is unavailable"
        )
    origin_access = endpoint_scope.point_access_nodes[point]
    destination_access = endpoint_scope.destination_access_nodes[destination]
    first, last = journey.legs[0], journey.legs[-1]
    if (
        journey.origin_node_id != origin_access
        and (
            first.kind != "walk"
            or first.from_node_id != journey.origin_node_id
            or first.to_node_id != origin_access
        )
        or journey.destination_node_id != destination_access
        and (
            last.kind != "walk"
            or last.from_node_id != destination_access
            or last.to_node_id != journey.destination_node_id
        )
    ):
        raise DomainError(409, "incompatible_versions", "Scenario urban physical connector differs")


def _urban_score_scope(
    result: ScenarioResult, baseline: AnalysisResult, renamed: dict[str, str]
) -> None:
    """Numeric indices retain every requested, known operand in their own scope."""
    old_times = [
        row
        for row in baseline.metrics
        if row.subject.kind == "sample_point" and row.unit == "seconds"
    ]
    times_by_point: dict[str | None, list[Metric]] = {}
    for row in old_times:
        times_by_point.setdefault(row.subject.point_id, []).append(row)
    old_scores = {row.score_id: row for row in baseline.scores}
    score_pairs = {
        effect.after_id: effect.before_id
        for effect in result.deltas
        if effect.kind == "score" and effect.after_id and effect.before_id
    }
    for score in result.after.scores:
        previous = old_scores.get(score_pairs.get(score.score_id, score.score_id))
        if previous is not None and any(
            getattr(previous, field) != getattr(score, field)
            for field in (
                "name",
                "subject",
                "methodology_version",
                "calculation_kind",
                "aggregation_basis",
            )
        ):
            raise DomainError(
                409, "incompatible_versions", "Scenario urban index task identity differs"
            )
    request = baseline.request
    assert isinstance(request, UrbanPassengerRequest)
    metrics = {row.metric_id: row for row in result.after.metrics}
    scores = {row.score_id: row for row in result.after.scores}

    for aggregate in result.after.scores:
        if aggregate.status != "available":
            continue
        full = aggregate.completeness == "full"
        if (
            aggregate.subject.kind == "sample_point"
            and aggregate.subject.category is None
            and aggregate.subject.point_id in times_by_point
        ):
            children = [scores[item.id] for item in aggregate.components if item.kind == "score"]
            categories = [child.subject.category for child in children]
            if (
                len(children) != len(aggregate.components)
                or len(categories) != len(set(request.categories))
                or set(categories) != set(request.categories)
                or any(
                    child.subject.kind != "sample_point"
                    or child.subject.point_id != aggregate.subject.point_id
                    or child.subject.territory_id != request.city_id
                    or child.subject.mode != request.passenger_mode
                    or child.status != "available"
                    or (full and child.completeness != "full")
                    for child in children
                )
            ):
                raise DomainError(
                    409,
                    "incompatible_versions",
                    "Scenario numeric point category inventory differs",
                )
        if aggregate.subject.kind == "territory":
            rows = [
                row
                for row in old_times
                if row.subject.territory_id == request.city_id
                and row.subject.mode == request.passenger_mode
                and (
                    aggregate.subject.category is None
                    or row.subject.category == aggregate.subject.category
                )
            ]
            if not rows:
                continue
            basis = aggregate.aggregation_basis
            if not isinstance(basis, PopulationBasis) or (
                basis.population is None or basis.coverage < 1 or basis.unknown_positive_weight
            ):
                raise DomainError(
                    409, "incompatible_versions", "Scenario numeric territory weights unavailable"
                )
            point_rows: dict[str | None, list[Metric]] = {}
            for row in rows:
                point_rows.setdefault(row.subject.point_id, []).append(row)
            points = set(point_rows)
            # Only an explicitly known original zero weight may be optional.
            # Unknown and positive original weights remain mandatory operands.
            required = {
                point
                for point, records in point_rows.items()
                if any(
                    row.population_basis is None
                    or row.population_basis.population != 0
                    or row.population_basis.coverage != 1
                    or row.population_basis.unknown_positive_weight
                    for row in records
                )
            }
            children = [scores[item.id] for item in aggregate.components if item.kind == "score"]
            actual = [child.subject.point_id for child in children]
            if (
                len(children) != len(aggregate.components)
                or len(actual) != len(set(actual))
                or not required <= set(actual) <= points
                or aggregate.subject.territory_id != request.city_id
                or aggregate.subject.mode != request.passenger_mode
                or any(
                    child.subject.kind != "sample_point"
                    or child.subject.territory_id != request.city_id
                    or child.subject.mode != request.passenger_mode
                    or child.subject.category != aggregate.subject.category
                    or child.status != "available"
                    or (full and child.completeness != "full")
                    for child in children
                )
            ):
                raise DomainError(
                    409,
                    "incompatible_versions",
                    "Scenario numeric territory point inventory differs",
                )
    old_to_new = {old: new for new, old in renamed.items()}
    for previous_h in baseline.metrics:
        if previous_h.name != "urban_h_seconds":
            continue
        current_h = metrics.get(old_to_new.get(previous_h.metric_id, previous_h.metric_id))
        if current_h is None or current_h.model_dump(
            exclude={"metric_id"}
        ) != previous_h.model_dump(exclude={"metric_id"}):
            raise DomainError(
                409, "incompatible_versions", "Scenario changes immutable urban area scale"
            )
    leaves: dict[str, list[Metric]] = {}

    def operands(identifier: str) -> list[Metric]:
        if identifier not in leaves:
            result_metrics = []
            for component in scores[identifier].components:
                if component.kind == "metric":
                    result_metrics.append(metrics[component.id])
                else:
                    result_metrics.extend(operands(component.id))
            leaves[identifier] = result_metrics
        return leaves[identifier]

    for score in result.after.scores:
        if score.subject.kind != "sample_point" or score.status != "available":
            continue
        # Point aggregates have their complete direct category inventory checked
        # above. Validate each category below; a proven absent category is a
        # terminal zero and contributes no unknown numerical time operand.
        if score.subject.category is None:
            continue
        expected = [
            row
            for row in times_by_point.get(score.subject.point_id, ())
            if score.subject.category is None or row.subject.category == score.subject.category
        ]
        if not expected:
            continue
        expected_after = [
            metrics.get(old_to_new.get(row.metric_id, row.metric_id)) for row in expected
        ]
        if (
            score.value == 0
            and score.reason_code == "NO_FEASIBLE_ROUTE"
            and score.completeness == "full"
            and not score.components
            and all(
                row is not None
                and row.value is None
                and row.status == "not_applicable"
                and row.reason_code == "NO_FEASIBLE_ROUTE"
                and row.completeness == "full"
                for row in expected_after
            )
        ):
            continue
        point_times = [
            row
            for row in operands(score.score_id)
            if row.subject.kind == "sample_point" and row.unit == "seconds"
        ]
        if (
            not point_times
            or any(
                row is None or row.status != "available" or row.value is None
                for row in expected_after
            )
            or any(
                row.subject.point_id != score.subject.point_id
                or row.subject.territory_id != score.subject.territory_id
                or row.subject.mode != score.subject.mode
                or (
                    score.subject.category is not None
                    and row.subject.category != score.subject.category
                )
                for row in point_times
            )
        ):
            raise DomainError(
                409, "incompatible_versions", "Scenario point index uses another time task"
            )
        if any(row.status != "available" or row.value is None for row in operands(score.score_id)):
            raise DomainError(
                409, "incompatible_versions", "Scenario numeric point index has unknown operand"
            )


def scenario_journeys(
    result: ScenarioResult,
    baseline: AnalysisResult,
    *,
    endpoint_scope: ScenarioEndpointScope | None = None,
) -> dict[str, JourneyResult]:
    """Resolve new chains without allowing a baseline identity to be overwritten."""
    journeys = {journey.journey_id: journey for journey in baseline.journeys}
    references = {reference.reference_id: reference for reference in baseline.references}
    evidence = {row.evidence_id: row for row in result.evidence_refs}
    for previous_evidence in baseline.evidence_refs:
        if (
            previous_evidence.evidence_id in evidence
            and previous_evidence != evidence[previous_evidence.evidence_id]
        ):
            raise DomainError(409, "incompatible_versions", "Scenario replaces baseline evidence")
    new_ids: set[str] = set()
    for journey in result.after_journeys:
        old = journeys.get(journey.journey_id)
        if old is not None and old != journey:
            raise DomainError(409, "incompatible_versions", "Scenario replaces baseline journey")
        if (
            journey.snapshot_id != baseline.snapshot_id
            or journey.traffic_mode != baseline.traffic_mode
            or utc(journey.requested_departure_at) != utc(baseline.request.departure_at)
            or journey.scope
            != ("intercity" if baseline.analysis_kind.startswith("intercity_") else "urban")
        ):
            raise DomainError(409, "incompatible_versions", "Scenario journey task differs")
        if journey.scope == "urban":
            _urban_journey(journey, baseline, endpoint_scope)
        if journey.reference_id is not None:
            reference = references.get(journey.reference_id)
            if reference is None:
                raise DomainError(409, "incompatible_versions", "Scenario changes frozen reference")
            try:
                validate_reference_applicability(reference, baseline.request, [journey])
            except ValueError:
                raise DomainError(
                    409, "incompatible_versions", "Scenario journey reference boundary differs"
                ) from None
        if isinstance(baseline.request, IntercityBase) and (
            baseline.request.origin_node_ids is not None
            and (
                journey.origin_node_id not in baseline.request.origin_node_ids
                or journey.destination_node_id not in (baseline.request.destination_node_ids or [])
            )
        ):
            raise DomainError(409, "incompatible_versions", "Scenario journey boundary differs")
        journeys[journey.journey_id] = journey
        new_ids.add(journey.journey_id)

    measures = {
        "path_seconds": "path_seconds",
        "initial_wait_seconds": "initial_wait_seconds",
        "total_seconds": "total_seconds",
        "total_wait_seconds": "total_wait_seconds",
        "minimum_journey_seconds": "total_seconds",
        "best_known_journey_seconds": "total_seconds",
    }
    old_metrics = {metric.metric_id: metric for metric in baseline.metrics}
    renamed = {
        effect.after_id: effect.before_id
        for effect in result.deltas
        if effect.kind == "metric" and effect.after_id and effect.before_id
    }
    for metric in result.after.metrics:
        urban = isinstance(baseline.request, UrbanPassengerRequest)
        previous_task = old_metrics.get(renamed.get(metric.metric_id, metric.metric_id))
        preserved_point_time = (
            urban
            and previous_task is not None
            and previous_task.subject.kind == "sample_point"
            and previous_task.unit == "seconds"
        )
        if (
            preserved_point_time
            and endpoint_scope is not None
            and previous_task is not None
            and any(
                getattr(previous_task, field) != getattr(metric, field)
                for field in (
                    "name",
                    "unit",
                    "subject",
                    "time_kind",
                    "population_basis",
                    "site_basis",
                )
            )
        ):
            raise DomainError(
                409, "incompatible_versions", "Scenario urban time task identity differs"
            )
        if metric.journey_id not in new_ids and not urban:
            continue
        resolved_journey = journeys.get(metric.journey_id or "")
        if resolved_journey is None:
            numeric_point_chain = (
                urban
                and (
                    preserved_point_time
                    or (metric.subject.kind == "sample_point" and metric.unit == "seconds")
                )
                and metric.status == "available"
                and metric.value is not None
            )
            previous_unbound = old_metrics.get(renamed.get(metric.metric_id, metric.metric_id))
            legacy_binding = (
                previous_unbound is not None
                and previous_unbound.status == "available"
                and previous_unbound.value is not None
                and previous_unbound.journey_id == metric.journey_id
                and previous_unbound.subject == metric.subject
                and (previous_unbound.name, previous_unbound.unit, previous_unbound.time_kind)
                == (metric.name, metric.unit, metric.time_kind)
            )
            if numeric_point_chain and (endpoint_scope is not None or not legacy_binding):
                raise DomainError(
                    409, "incompatible_versions", "Scenario urban journey proof is unavailable"
                )
            continue
        journey = resolved_journey
        if urban:
            if endpoint_scope is not None:
                _urban_journey(journey, baseline, endpoint_scope)
            else:
                previous = old_metrics.get(renamed.get(metric.metric_id, metric.metric_id))
                if (
                    previous is None
                    or previous.journey_id != metric.journey_id
                    or previous.subject != metric.subject
                    or (previous.name, previous.unit, previous.time_kind)
                    != (metric.name, metric.unit, metric.time_kind)
                ):
                    raise DomainError(
                        409, "incompatible_versions", "Scenario urban endpoint scope is unavailable"
                    )
        measure = measures.get(metric.name)
        expected = getattr(journey, measure) if measure is not None else None
        if (
            metric.unit != "seconds"
            or metric.status != "available"
            or metric.value is None
            or expected is None
            or not isclose(metric.value, expected, rel_tol=1e-12, abs_tol=1e-9)
            or journey.status != "available"
            or (metric.completeness == "full" and journey.completeness != "full")
            or (metric.time_kind is not None and metric.time_kind != journey.time_kind)
        ):
            raise DomainError(409, "incompatible_versions", "Scenario raw time differs from chain")
        if metric.subject.kind == "sample_point" and metric.subject.point_id != journey.point_id:
            raise DomainError(409, "incompatible_versions", "Scenario journey point differs")
        if isinstance(baseline.request, UrbanPassengerRequest) and endpoint_scope is not None:
            if (
                metric.subject.kind != "sample_point"
                or metric.subject.territory_id != baseline.request.city_id
                or metric.subject.mode != baseline.request.passenger_mode
                or metric.subject.category not in baseline.request.categories
                or metric.subject.category
                not in endpoint_scope.destination_categories[journey.destination_object_id or ""]
            ):
                raise DomainError(
                    409, "incompatible_versions", "Scenario urban category task differs"
                )
        if isinstance(baseline.request, IntercityBase) and (
            metric.subject.origin_id != baseline.request.origin_id
            or metric.subject.destination_id != baseline.request.destination_id
        ):
            raise DomainError(
                409, "incompatible_versions", "Scenario journey territory task differs"
            )
    if isinstance(baseline.request, UrbanPassengerRequest) and endpoint_scope is not None:
        _urban_score_scope(result, baseline, renamed)
    return journeys
