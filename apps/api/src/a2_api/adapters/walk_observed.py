"""Separate preliminary route assessments; canonical minimum indices stay intact."""

from collections import defaultdict

from a2_contracts.common import canonical_hash
from a2_contracts.v2.base import Category
from a2_contracts.v2.results import Metric, SubjectRef
from a2_engine import MetricResult, WalkAnalysisResult, load_urban_config, load_urban_journey_rules
from a2_engine.urban.walk.observed import ObservedRouteAssessment, assess_observed_walk_route
from pydantic import TypeAdapter

from a2_api.adapters.product_projection import Projection


def project_observed_walk(out: Projection, calculated: WalkAnalysisResult) -> None:
    inputs = calculated.plan.inputs
    context = inputs.context
    if (
        context.versions.snapshot_id != out.context.snapshot_id
        or context.versions.config_hash != out.context.versions.config_hash
        or context.versions.methodology_version != out.context.versions.methodology_version
        or context.requested_departure_at != out.context.normalized_request.departure_at
    ):
        raise ValueError("OBSERVED_PROJECTION_SOURCE_CONTEXT_MISMATCH")
    config, rules = load_urban_config(), load_urban_journey_rules()
    categories = TypeAdapter(list[Category]).validate_python(list(calculated.plan.categories))
    destinations = {destination.destination_id: destination for destination in inputs.destinations}
    assessments: dict[tuple[str, str], list[ObservedRouteAssessment]] = defaultdict(list)
    for route in calculated.routes:
        for category in categories:
            if category not in destinations[route.request.destination_id].categories:
                continue
            assessed = assess_observed_walk_route(
                inputs, route, category=category, config=config, rules=rules
            )
            assessments[assessed.point_id, category].append(assessed)
    journeys = {journey.journey_id: journey for journey in out.journeys}
    existing_times: dict[tuple[str | None, str | None], Metric] = {
        (metric.subject.point_id, metric.subject.category): metric
        for metric in out.metrics
        if metric.name == "minimum_journey_seconds" and metric.subject.mode == "walk"
    }
    if not any(metric.metric_id == "urban-h" for metric in out.metrics):
        raise ValueError("OBSERVED_PROJECTION_REQUIRES_CANONICAL_AREA_H")
    for point in inputs.points:
        for category in categories:
            options = assessments.get((point.point_id, category), [])
            available = [option for option in options if option.observed_total_seconds is not None]
            chosen = (
                min(
                    available,
                    key=lambda option: (option.observed_total_seconds, option.destination_id),
                )
                if available
                else None
            )
            score = (
                chosen.observed_route_accessibility
                if chosen is not None
                else options[0].observed_route_accessibility
                if options
                else MetricResult(None, "unknown", "OBSERVED_ROUTE_UNAVAILABLE")
            )
            evidence = (
                chosen.evidence_refs
                if chosen
                else options[0].evidence_refs
                if options
                else point.source_refs
            )
            journey_id = None
            if chosen is not None:
                raw = existing_times.get((point.point_id, category))
                journey = journeys.get(raw.journey_id) if raw and raw.journey_id else None
                if journey is None or journey.destination_object_id != chosen.destination_id:
                    raise ValueError("OBSERVED_PROJECTION_EXACT_JOURNEY_UNAVAILABLE")
                journey_id = journey.journey_id
            key = canonical_hash((point.point_id, category))[:24]
            subject = SubjectRef(
                kind="sample_point",
                territory_id=context.territory_id,
                point_id=point.point_id,
                category=category,
                mode="walk",
            )
            time_id = out.metric(
                "observed-walk-time-" + key,
                "best_observed_time",
                MetricResult(chosen.observed_total_seconds, "available", "MINIMUM_NOT_PROVEN")
                if chosen
                else MetricResult(None, "unknown", score.reason_code),
                subject,
                evidence,
                journey_id=journey_id,
                time_kind="complete_chain",
            )
            out.score(
                "observed-walk-accessibility-" + key,
                "observed_route_accessibility",
                score,
                subject,
                evidence,
                metrics=(time_id, "urban-h"),
                full=False,
                ranking=False,
            )
            for assessed in options:
                out.limitations.update(assessed.limitations)
    out.limitations.update({"OBSERVED_ROUTE_ESTIMATE", "MINIMUM_NOT_PROVEN", "NOT_A_CITY_RANKING"})
