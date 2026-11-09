import pytest
from a2_contracts.results import CityScore, ProfileResult
from a2_contracts.scenarios import ScenarioRequest
from pydantic import ValidationError


@pytest.mark.parametrize(
    "update",
    [
        {"score": 50},
        {"score_lower": -1},
        {"score_upper": 101},
        {"score_lower": 90},
        {"score_lower": float("nan")},
        {"resolved_weight_share": 1.1},
        {"task_count": 0},
    ],
)
def test_partial_score_cannot_lie_about_precision_or_bounds(update):
    with pytest.raises(ValidationError):
        CityScore.model_validate(
            dict(
                origin_id="demo-a",
                score=None,
                score_lower=25,
                score_upper=75,
                task_count=4,
                resolved_weight_share=0.5,
            )
            | update
        )


def test_exact_bounds_require_matching_scalar_without_rounding():
    assert (
        CityScore(
            origin_id="demo-a",
            score=50,
            score_lower=50,
            score_upper=50,
            task_count=2,
            resolved_weight_share=1,
        ).score
        == 50
    )
    with pytest.raises(ValidationError):
        CityScore(
            origin_id="demo-a",
            score=None,
            score_lower=50,
            score_upper=50,
            task_count=2,
            resolved_weight_share=1,
        )


def test_insufficient_data_does_not_fabricate_city_intervals():
    with pytest.raises(ValidationError):
        ProfileResult.model_validate(
            {
                "status": "insufficient_data",
                "cities": [
                    {
                        "origin_id": "demo",
                        "score": None,
                        "score_lower": 0,
                        "score_upper": 100,
                        "task_count": 1,
                        "resolved_weight_share": 0,
                    }
                ],
            }
        )


def scenario():
    return dict(
        base_run_ids={"regular": "demo-base"},
        evaluate_profile_ids=["regular"],
        operations=[
            dict(
                type="shift_trip",
                trip_id="demo:trip",
                service_dates=["2026-11-09"],
                delta_seconds=1800,
            )
        ],
    )


def test_shift_bounds_and_operation_limit():
    assert ScenarioRequest.model_validate(scenario()).operations[0].type == "shift_trip"
    for delta in (-1801, 1801):
        data = scenario()
        data["operations"][0]["delta_seconds"] = delta
        with pytest.raises(ValidationError):
            ScenarioRequest.model_validate(data)
    with pytest.raises(ValidationError):
        ScenarioRequest.model_validate(scenario() | {"operations": scenario()["operations"] * 11})


def test_add_template_requires_new_identity_and_dates():
    data = scenario() | {
        "operations": [
            dict(
                type="add_trip",
                template_trip_id="demo:trip",
                service_dates=["2026-11-09"],
                stop_time_shift_seconds=1800,
            )
        ]
    }
    with pytest.raises(ValidationError):
        ScenarioRequest.model_validate(data)
    data["operations"][0]["new_trip_id"] = "scenario:extra"
    assert ScenarioRequest.model_validate(data).operations[0].new_trip_id == "scenario:extra"


def test_scenario_requires_base_for_every_requested_profile():
    with pytest.raises(ValidationError):
        ScenarioRequest.model_validate(scenario() | {"evaluate_profile_ids": ["medical"]})
