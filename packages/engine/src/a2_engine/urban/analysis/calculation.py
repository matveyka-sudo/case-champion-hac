"""analysis / calculation — canonical urban implementation."""

from collections.abc import Mapping, Sequence
from typing import Literal

from a2_engine.shared.config import UrbanConfig, UrbanJourneyRules
from a2_engine.shared.results import MetricResult
from a2_engine.shared.scoring import category_index
from a2_engine.urban.analysis.models import (
    UrbanAnalysis,
    UrbanCategoryResult,
    UrbanPointResult,
    UrbanTerritoryResult,
)
from a2_engine.urban.analysis.validation import _check_candidate
from a2_engine.urban.indices import point_index, select_h_seconds, territory_index
from a2_engine.urban.journeys import UrbanJourneyTiming
from a2_engine.urban.models import (
    Completeness,
    UrbanArea,
    UrbanCandidate,
    UrbanContext,
    UrbanCoverage,
    UrbanDestination,
    UrbanPoint,
    UrbanPopulationCoverage,
    UrbanThreshold,
    references,
)
from a2_engine.urban.selection import select_urban_objects
from a2_engine.urban.validation import _check_points


def _category_result(
    point_id: str,
    category: str,
    mode: str,
    journeys: tuple[UrbanJourneyTiming, ...],
    destinations: Mapping[str, UrbanDestination],
    coverage: UrbanCoverage | None,
    threshold: UrbanThreshold | None,
    h_seconds: float | None,
) -> UrbanCategoryResult:
    objects = select_urban_objects(
        journeys, destinations, point_id=point_id, category=category, passenger_mode=mode
    )
    relevant = tuple(
        j
        for j in journeys
        if (j.candidate.point_id, j.candidate.category, j.candidate.passenger_mode)
        == (point_id, category, mode)
    )
    eligible_unknown = any(
        d.in_service_scope and d.eligible is None and category in d.categories
        for d in destinations.values()
    )
    full = bool(
        coverage is not None
        and coverage.catalog_complete
        and coverage.search_complete
        and coverage.catalog_destination_ids
        == frozenset(
            d.destination_id
            for d in destinations.values()
            if d.in_service_scope and category in d.categories
        )
        and not eligible_unknown
    )
    reason = (
        "DESTINATION_ELIGIBILITY_UNKNOWN"
        if eligible_unknown
        else "DATA_UNAVAILABLE"
        if coverage is not None and coverage.provider_error is not None
        else "INCOMPLETE_COVERAGE"
    )
    best = objects[0] if objects else None
    if not full:
        index = MetricResult(None, "unknown", reason)
    elif best is not None:
        index = category_index(best.journey.total_seconds, h_seconds)
    else:
        index = category_index(None, h_seconds, confirmed_no_route=True)
    if threshold is None or threshold.seconds is None:
        count = MetricResult(None, "unknown", "TAU_UNAVAILABLE")
    elif not full:
        count = MetricResult(None, "unknown", reason)
    else:
        count = MetricResult(
            float(sum(item.journey.total_seconds <= threshold.seconds for item in objects)),
            "available",
        )
    evidence = [
        ref for d in destinations.values() if category in d.categories for ref in d.source_refs
    ]
    for timing in relevant:
        evidence.extend(timing.candidate.evidence_refs)
        for leg in timing.candidate.legs:
            evidence.extend(leg.evidence_refs)
        if timing.candidate.geometry is not None:
            evidence.extend(timing.candidate.geometry.evidence_refs)
    if coverage is not None:
        evidence.extend(coverage.evidence_refs)
    if threshold is not None:
        evidence.extend(threshold.evidence_refs)
    limitations = {
        metric.reason_code for metric in (index, count) if metric.reason_code is not None
    }
    if best is not None and best.journey.candidate.geometry is None:
        limitations.add("GEOMETRY_UNAVAILABLE")
    rejected = tuple(j for j in relevant if not j.admissible)
    if rejected:
        limitations.add("MAX_TOTAL_WAIT_EXCEEDED")
    return UrbanCategoryResult(
        point_id,
        category,
        mode,
        best,
        objects,
        rejected,
        index,
        count,
        threshold,
        coverage,
        full,
        "full" if full and index.status != "unknown" else "partial",
        references(tuple(evidence)),
        tuple(sorted(limitations)),
    )


def analyze_urban_candidates(
    context: UrbanContext,
    points: Sequence[UrbanPoint],
    destinations: Sequence[UrbanDestination],
    candidates: Sequence[UrbanCandidate],
    coverage: Sequence[UrbanCoverage],
    *,
    area: UrbanArea,
    selected_categories: Mapping[str, str],
    modes: Sequence[str],
    population_coverage: UrbanPopulationCoverage,
    config: UrbanConfig,
    rules: UrbanJourneyRules,
    thresholds: Sequence[UrbanThreshold] = (),
) -> UrbanAnalysis:
    """Analyze one accepted context; missing coverage is never a completeness proof."""
    if (
        context.versions.methodology_version != config.methodology_version
        or rules.methodology_version != config.methodology_version
    ):
        raise ValueError("METHODOLOGY_VERSION_MISMATCH")
    if (
        area.city_id != context.city_id
        or area.geometry_version != context.versions.geometry_version
    ):
        raise ValueError("ADMINISTRATIVE_AREA_CONTEXT_MISMATCH")
    if population_coverage.context != context:
        raise ValueError("POPULATION_COVERAGE_CONTEXT_MISMATCH")
    if not selected_categories or any(
        not isinstance(c, str)
        or not isinstance(p, str)
        or c not in config.categories
        or p not in config.priority_weights
        for c, p in selected_categories.items()
    ):
        raise ValueError("INVALID_CATEGORY_SELECTION")
    if not modes or len(set(modes)) != len(modes) or set(modes) - config.modes:
        raise ValueError("INVALID_MODE_SELECTION")
    point_map = _check_points(points, context)
    destination_map: dict[str, UrbanDestination] = {}
    for destination in destinations:
        if (
            destination.destination_id in destination_map
            or destination.categories - config.categories
        ):
            raise ValueError("INVALID_DESTINATION_CATALOG")
        destination_map[destination.destination_id] = destination
    seen_journeys: set[str] = set()
    checked = []
    for candidate in candidates:
        if candidate.journey_id in seen_journeys:
            raise ValueError("DUPLICATE_JOURNEY_ID")
        seen_journeys.add(candidate.journey_id)
        checked.append(
            _check_candidate(candidate, context, point_map, destination_map, config, rules)
        )
    journeys = tuple(sorted(checked, key=lambda j: j.candidate.journey_id))
    grouped_journeys: dict[tuple[str, str, str], list[UrbanJourneyTiming]] = {}
    for journey in journeys:
        candidate = journey.candidate
        key = (candidate.point_id, candidate.category, candidate.passenger_mode)
        grouped_journeys.setdefault(key, []).append(journey)
    coverage_map: dict[tuple[str, str, str], UrbanCoverage] = {}
    for item in coverage:
        scope = item.scope
        key = (scope.point_id, scope.category, scope.passenger_mode)
        if (
            scope.context != context
            or scope.max_total_wait_seconds != rules.max_total_wait_seconds
            or scope.point_id not in point_map
            or scope.category not in config.categories
            or scope.passenger_mode not in config.modes
            or key in coverage_map
        ):
            raise ValueError("INVALID_OR_DUPLICATE_SEARCH_SCOPE")
        coverage_map[key] = item
    threshold_map: dict[str, UrbanThreshold] = {}
    for threshold in thresholds:
        if (
            threshold.city_id != context.city_id
            or threshold.versions != context.versions
            or threshold.traffic_mode != context.traffic_mode
            or threshold.search_parameters_id != context.search_parameters_id
            or threshold.passenger_mode not in config.modes
            or threshold.passenger_mode in threshold_map
        ):
            raise ValueError("TAU_CONTEXT_MISMATCH")
        threshold_map[threshold.passenger_mode] = threshold
    h = select_h_seconds(area.area_km2, bands=config.area_h_bands)
    selection = dict(sorted(selected_categories.items()))
    territory_results = []
    all_metrics = [h]
    limitations: set[str] = set()
    population_full = (
        population_coverage.complete
        and population_coverage.excluded_population == 0
        and population_coverage.point_ids == frozenset(point_map)
    )
    for mode in sorted(modes):
        point_results = []
        evidence = list(population_coverage.evidence_refs) + list(area.source_refs)
        for point in sorted(point_map.values(), key=lambda p: p.point_id):
            categories = tuple(
                _category_result(
                    point.point_id,
                    category,
                    mode,
                    tuple(grouped_journeys.get((point.point_id, category, mode), ())),
                    destination_map,
                    coverage_map.get((point.point_id, category, mode)),
                    threshold_map.get(mode),
                    h.value,
                )
                for category in selection
            )
            index = point_index(
                {result.category: result.index for result in categories}, selection, config=config
            )
            point_results.append(
                UrbanPointResult(
                    point,
                    mode,
                    categories,
                    index,
                    "full" if index.status != "unknown" else "partial",
                )
            )
            all_metrics.append(index)
            evidence.extend(point.source_refs)
            for result in categories:
                all_metrics.extend((result.index, result.count))
                evidence.extend(result.evidence_refs)
                limitations.update(result.limitations)
        aggregate = territory_index(
            {p.point.point_id: p.index for p in point_results},
            {p.point_id: p.population for p in point_map.values()},
        )
        if not population_full:
            aggregate = MetricResult(None, "unknown", "INCOMPLETE_POPULATION_COVERAGE")
        elif context.versions.population_version is None and aggregate.value is not None:
            aggregate = MetricResult(None, "unknown", "POPULATION_VERSION_UNAVAILABLE")
        completeness: Completeness = "full" if aggregate.status != "unknown" else "partial"
        territory_results.append(
            UrbanTerritoryResult(
                mode,
                tuple(point_results),
                aggregate,
                completeness,
                aggregate.status == "available" and completeness == "full",
                references(tuple(evidence)),
            )
        )
        all_metrics.append(aggregate)
        if aggregate.reason_code is not None:
            limitations.add(aggregate.reason_code)
    status: Literal["available", "partial", "unknown"] = (
        "available"
        if all(m.status != "unknown" for m in all_metrics)
        else "partial"
        if any(m.value is not None for m in all_metrics) or journeys
        else "unknown"
    )
    return UrbanAnalysis(
        context,
        area,
        selection,
        h,
        journeys,
        tuple(territory_results),
        population_coverage,
        status,
        tuple(sorted(limitations)),
    )
