"""Check newly computed scenario chains against the saved task and reference.

This validates engine output at publication. It never routes, reconstructs a
schedule, recalibrates a reference or changes the stored baseline.
"""

from math import isclose

from a2_contracts.common import utc
from a2_contracts.v2.journeys import JourneyResult
from a2_contracts.v2.requests import IntercityBase, UrbanPassengerRequest
from a2_contracts.v2.results import AnalysisResult, validate_reference_applicability
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
        if metric.journey_id not in new_ids and not urban:
            continue
        resolved_journey = journeys.get(metric.journey_id or "")
        if resolved_journey is None:
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
    return journeys
