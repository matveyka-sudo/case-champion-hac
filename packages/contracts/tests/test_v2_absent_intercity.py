"""Synthetic contract boundaries: confirmed absence is not a numeric time ratio."""

import pytest
from a2_contracts.v2.results import AnalysisResult
from a2_contracts.v2.scenarios import ScenarioValues
from pydantic import ValidationError
from test_v2_requests import cases
from test_v2_results import analysis, metric, score


def absent_analysis(kind="intercity_passenger", calculation="aggregate"):
    value = analysis()
    request = cases()[1 if kind == "intercity_passenger" else 3] | {
        "snapshot_id": value["snapshot_id"],
        "methodology": value["request"]["methodology"],
    }
    subject = {
        "kind": "connection",
        "origin_id": request["origin_id"],
        "destination_id": request["destination_id"],
        "mode": "rail",
    }
    value.update(
        analysis_kind=kind,
        request=request,
        metrics=[
            metric()
            | {
                "subject": subject,
                "value": None,
                "status": "not_applicable",
                "reason_code": "NO_FEASIBLE_ROUTE",
                "time_kind": "complete_chain",
            }
        ],
        scores=[
            score()
            | {
                "subject": subject,
                "value": 0.0,
                "reason_code": "NO_FEASIBLE_ROUTE",
                "components": [],
                "calculation_kind": calculation,
            }
        ],
    )
    return value


@pytest.mark.parametrize(
    ("kind", "calculation"),
    [
        ("intercity_passenger", "aggregate"),
        ("intercity_cargo", "delivery_deadline"),
    ],
)
def test_confirmed_absence_needs_no_fabricated_journey_or_reference(kind, calculation):
    result = AnalysisResult.model_validate(absent_analysis(kind, calculation))
    assert result.scores[0].value == 0
    assert result.metrics[0].value is None
    assert result.journeys == result.references == result.scores[0].components == []
    ScenarioValues(metrics=result.metrics, scores=result.scores)


@pytest.mark.parametrize(
    "change",
    [
        {"status": "unknown"},
        {"completeness": "partial"},
        {"reason_code": "NETWORK_UNAVAILABLE"},
        {"time_kind": "movement"},
        {"source_refs": []},
        {"source_refs": ["different-proof"]},
        {"journey_id": "nonexistent-route"},
        {"unit": "minutes"},
        {"subject": {"kind": "connection", "origin_id": "elsewhere", "destination_id": "x"}},
    ],
)
def test_absence_score_cannot_use_missing_partial_or_unrelated_time(change):
    value = absent_analysis()
    value["metrics"][0].update(change)
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(value)
    with pytest.raises(ValidationError):
        ScenarioValues.model_validate({"metrics": value["metrics"], "scores": value["scores"]})


@pytest.mark.parametrize(
    "change",
    [
        {"value": 1.0},
        {"reason_code": None},
        {"completeness": "partial"},
        {"evidence_refs": []},
        {"calculation_kind": None},
        {"calculation_kind": "delivery_deadline"},
        {"components": [{"kind": "metric", "id": "demo-time"}]},
        {"reference_ids": ["invented-denominator"]},
    ],
)
def test_absence_exception_cannot_authorize_normal_or_misclassified_scores(change):
    value = absent_analysis()
    value["scores"][0].update(change)
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(value)


def test_absence_marker_alone_is_insufficient_in_saved_scenario_values():
    value = absent_analysis()
    with pytest.raises(ValidationError, match="scoped complete-chain placeholder"):
        ScenarioValues.model_validate({"metrics": [], "scores": value["scores"]})
