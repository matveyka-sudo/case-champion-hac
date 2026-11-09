from copy import deepcopy

import pytest
from a2_contracts.v2.results import AnalysisResult
from a2_contracts.v2.scenarios import ScenarioResult, ScenarioValues
from pydantic import ValidationError
from test_v2_results import analysis, score
from test_v2_scenarios import result as scenario_result


def counted_analysis():
    data = analysis()
    data["scores"] = []
    data["metrics"][0].update(value=2.0, unit="objects")
    data["counts"] = [
        dict(
            metric_id="demo-time",
            threshold_seconds=1800,
            value=2,
            status="available",
            reason_code=None,
            dependency_job_id=None,
            evidence_refs=["demo-evidence"],
        )
    ]
    return data


@pytest.mark.parametrize(
    "change",
    [
        {"value": 999},
        {"status": "unknown", "value": None, "reason_code": "MISSING"},
        {"dependency_job_id": "wrong-job"},
    ],
)
def test_count_matches_its_canonical_metric(change):
    data = counted_analysis()
    AnalysisResult.model_validate(data)
    data["counts"][0].update(change)
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(data)


def test_count_requires_object_units_and_unique_metric_identity():
    data = counted_analysis()
    data["metrics"][0]["unit"] = "seconds"
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(data)
    data = counted_analysis()
    data["counts"].append(deepcopy(data["counts"][0]))
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(data)


def return_analysis():
    data = analysis()
    data["journeys"] = [
        dict(
            journey_id="return",
            scope="urban",
            origin_node_id="object",
            destination_node_id="point",
            requested_departure_at="2026-11-09T23:00:00+03:00",
            departure_at="2026-11-09T23:00:00+03:00",
            arrival_at="2026-11-09T23:30:00+03:00",
            total_seconds=1800,
            initial_wait_seconds=900,
            path_seconds=None,
            total_wait_seconds=900,
            geometry=None,
            geometry_kind="none",
            status="available",
            reason_code=None,
            snapshot_id="demo-snapshot",
            completeness="full",
            traffic_mode="static",
            time_kind="complete_chain",
            reference_id=None,
            destination_object_id="school",
            point_id="point",
            evidence_refs=["demo-evidence"],
            legs=[
                dict(
                    mode="public_transport",
                    kind="wait_initial",
                    from_node_id="object",
                    to_node_id="object",
                    start_at="2026-11-09T23:00:00+03:00",
                    end_at="2026-11-09T23:15:00+03:00",
                    duration_seconds=900,
                    service_id="bus",
                    evidence_refs=["demo-evidence"],
                ),
                dict(
                    mode="public_transport",
                    kind="ride",
                    from_node_id="object",
                    to_node_id="point",
                    start_at="2026-11-09T23:15:00+03:00",
                    end_at="2026-11-09T23:30:00+03:00",
                    duration_seconds=900,
                    service_id="bus",
                    evidence_refs=["demo-evidence"],
                ),
            ],
        )
    ]
    data["last_returns"] = [
        dict(
            point_id="point",
            destination_object_id="school",
            journey_id="return",
            status="available",
            reason_code=None,
            service_date="2026-11-09",
            timezone="Europe/Moscow",
            leave_at="2026-11-09T23:00:00+03:00",
            first_departure_at="2026-11-09T23:15:00+03:00",
            arrival_at="2026-11-09T23:30:00+03:00",
            evidence_refs=["demo-evidence"],
        )
    ]
    return data


@pytest.mark.parametrize(
    "field,value",
    [
        ("leave_at", "2026-11-09T22:00:00+03:00"),
        ("first_departure_at", "2026-11-09T23:10:00+03:00"),
        ("arrival_at", "2026-11-10T00:00:00+03:00"),
    ],
)
def test_return_times_match_confirmed_journey(field, value):
    data = return_analysis()
    AnalysisResult.model_validate(data)
    data["last_returns"][0][field] = value
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(data)


def test_available_return_cannot_reference_unknown_or_walk_only_journey():
    data = return_analysis()
    journey = data["journeys"][0]
    journey.update(
        status="unknown",
        reason_code="TIMETABLE_UNAVAILABLE",
        legs=[],
        departure_at=None,
        arrival_at=None,
        total_seconds=None,
        initial_wait_seconds=None,
        total_wait_seconds=None,
        completeness="partial",
    )
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(data)
    data = return_analysis()
    for leg in data["journeys"][0]["legs"]:
        leg["mode"] = "walk"
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(data)


def test_scenario_full_score_cannot_depend_on_unknown_metric():
    data = scenario_result()
    metric = deepcopy(data["after"]["metrics"][0])
    metric.update(metric_id="missing", value=None, status="unknown", reason_code="MISSING")
    data["after"]["metrics"].append(metric)
    data["after"]["scores"] = [
        score() | {"evidence_refs": ["e-1"], "components": [{"kind": "metric", "id": "missing"}]}
    ]
    with pytest.raises(ValidationError):
        ScenarioResult.model_validate(data)


def test_scenario_score_dependency_graph_cannot_cycle():
    values = {
        "metrics": [],
        "scores": [
            score() | {"score_id": "a", "components": [{"kind": "score", "id": "b"}]},
            score() | {"score_id": "b", "components": [{"kind": "score", "id": "a"}]},
        ],
    }
    with pytest.raises(ValidationError):
        ScenarioValues.model_validate(values)


def test_scenario_top_level_availability_cannot_hide_unknown_after_metric():
    from a2_contracts.v2.scenarios import ScenarioResult
    from test_v2_scenarios import metric, result

    raw = result()
    raw["after"]["metrics"].append(metric("unresolved", None))
    with pytest.raises(ValidationError):
        ScenarioResult.model_validate(raw)
    raw["status"] = "partial"
    assert ScenarioResult.model_validate(raw).status == "partial"
