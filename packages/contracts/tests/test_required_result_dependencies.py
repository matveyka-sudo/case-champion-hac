"""Synthetic dependency-chain controls; formulas are supplied, not computed by DTOs."""

import json
from math import sqrt
from pathlib import Path

import pytest
from a2_contracts.v2.results import AnalysisResult
from pydantic import ValidationError


def complete_result():
    return json.loads(
        (Path(__file__).parents[1] / "fixtures/v2/required-dependencies-synthetic.json").read_text()
    )["payload"]


def test_mathematically_consistent_full_dependency_control():
    saved = AnalysisResult.model_validate(complete_result())
    assert saved.scores[-1].value == sqrt(5000)
    assert saved.journeys[0].initial_wait_seconds == 0


@pytest.mark.parametrize("state", ["partial", "unknown"])
def test_incomplete_reference_blocks_full_score(state):
    raw = complete_result()
    ref = raw["references"][0]
    ref["completeness"] = "partial"
    if state == "unknown":
        ref.update(status="unknown", seconds=None, reason_code="REFERENCE_MISSING")
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(raw)


@pytest.mark.parametrize("state", ["partial", "unknown"])
def test_incomplete_journey_blocks_full_score(state):
    raw = complete_result()
    journey = raw["journeys"][0]
    journey["completeness"] = "partial"
    if state == "unknown":
        journey.update(
            status="unknown",
            reason_code="JOURNEY_MISSING",
            legs=[],
            departure_at=None,
            arrival_at=None,
            total_seconds=None,
            initial_wait_seconds=None,
            path_seconds=None,
            total_wait_seconds=None,
        )
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(raw)


@pytest.mark.parametrize("ranking", [False, True])
@pytest.mark.parametrize("change", ["coverage", "unknown_weight", "unknown_population", "sites"])
def test_incomplete_aggregation_blocks_full_index_even_without_ranking(ranking, change):
    raw = complete_result()
    score = raw["scores"][0]
    score.update(
        ranking_eligible=ranking,
        subject=dict(kind="territory", territory_id="synthetic-city"),
        aggregation_basis=dict(
            population=100,
            population_method="synthetic",
            year=2026,
            coverage=1,
            unit="persons",
            unknown_positive_weight=False,
        ),
    )
    if change == "coverage":
        score["aggregation_basis"]["coverage"] = 0.5
    elif change == "unknown_weight":
        score["aggregation_basis"]["unknown_positive_weight"] = True
    elif change == "unknown_population":
        score["aggregation_basis"]["population"] = None
    else:
        score["aggregation_basis"] = dict(
            site_count=2, weighting="equal_physical_sites", coverage=0.5
        )
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(raw)


def test_partial_reference_preserves_known_raw_time_and_preliminary_scores():
    raw = complete_result()
    raw["references"][0]["completeness"] = "partial"
    raw["status"] = "partial"
    for score in raw["scores"]:
        score.update(completeness="partial", ranking_eligible=False)
    saved = AnalysisResult.model_validate(raw)
    assert saved.metrics[0].value == 1800
    assert saved.scores[-1].value == sqrt(5000)


def test_independent_raw_metric_needs_no_full_reference():
    raw = complete_result()
    raw["references"][0]["completeness"] = "partial"
    raw["scores"] = []
    assert AnalysisResult.model_validate(raw).metrics[0].value == 1800


def test_explicit_reference_dependency_cannot_bypass_completeness_or_identity():
    raw = complete_result()
    raw["metrics"][0]["journey_id"] = None
    raw["scores"][0]["reference_ids"] = ["reference"]
    assert AnalysisResult.model_validate(raw).scores[0].reference_ids == ["reference"]
    raw["references"][0]["completeness"] = "partial"
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(raw)
    raw["scores"][0]["reference_ids"] = ["missing-reference"]
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(raw)


@pytest.mark.parametrize(
    "basis",
    [
        dict(
            population=0,
            population_method="synthetic",
            year=2026,
            coverage=1,
            unit="persons",
            unknown_positive_weight=False,
        ),
        dict(site_count=0, weighting="equal_physical_sites", coverage=1),
    ],
)
def test_zero_denominator_never_becomes_numeric_index(basis):
    raw = complete_result()
    raw["scores"][0]["aggregation_basis"] = basis
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(raw)


def test_partial_aggregation_preserves_preliminary_numeric_value():
    raw = complete_result()
    raw["status"] = "partial"
    raw["scores"] = [raw["scores"][0]]
    raw["scores"][0].update(
        completeness="partial",
        ranking_eligible=False,
        aggregation_basis=dict(
            population=100,
            population_method="synthetic",
            year=2026,
            coverage=0.5,
            unit="persons",
            unknown_positive_weight=True,
        ),
    )
    saved = AnalysisResult.model_validate(raw)
    assert saved.scores[0].value == 50
    assert saved.metrics[0].value == 1800
