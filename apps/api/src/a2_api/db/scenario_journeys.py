"""Check newly computed scenario chains against the saved task and reference.

This validates engine output at publication. It never routes, reconstructs a
schedule, recalibrates a reference or changes the stored baseline.
"""

from math import isclose

from a2_contracts.common import utc
from a2_contracts.v2.journeys import JourneyResult
from a2_contracts.v2.requests import IntercityBase
from a2_contracts.v2.results import AnalysisResult, validate_reference_applicability
from a2_contracts.v2.scenarios import ScenarioResult

from a2_api.core.errors import DomainError


def scenario_journeys(result: ScenarioResult, baseline: AnalysisResult) -> dict[str, JourneyResult]:
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
    for metric in result.after.metrics:
        if metric.journey_id not in new_ids:
            continue
        journey = journeys[metric.journey_id]
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
        if isinstance(baseline.request, IntercityBase) and (
            metric.subject.origin_id != baseline.request.origin_id
            or metric.subject.destination_id != baseline.request.destination_id
        ):
            raise DomainError(
                409, "incompatible_versions", "Scenario journey territory task differs"
            )
    return journeys
