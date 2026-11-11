import pytest
from a2_contracts.journeys import JourneyRequest, JourneyResult
from a2_contracts.profiles import ProfileConfig, TaskSet
from pydantic import ValidationError


def request():
    return {
        "bundle_id": "demo-bundle",
        "profile_id": "regular",
        "profile_version": 1,
        "origin_id": "demo-origin",
        "destination_id": "demo-work",
        "service_type_id": None,
        "service_date": "2026-11-09",
        "earliest_departure": "2026-11-09T06:00:00+02:00",
        "activity_start": "2026-11-09T09:00:00+02:00",
        "activity_end": "2026-11-09T18:00:00+02:00",
        "return_by": "2026-11-09T21:00:00+02:00",
        "arrival_buffer_min_minutes": 0,
        "arrival_buffer_max_minutes": 30,
        "activity_basis": "scenario",
        "max_one_way_minutes": 90,
        "max_transfers": 2,
        "max_access_walk_minutes": 20,
        "budget": None,
        "require_step_free": False,
    }


def test_regular_request_preserves_explicit_disabled_budget():
    result = JourneyRequest.model_validate(request())
    assert result.budget is None
    assert result.activity_start.hour == 9


@pytest.mark.parametrize(
    "update",
    [
        {"service_type_id": "demo-service"},
        {"destination_id": None},
        {"activity_end": None},
        {"activity_start": "2026-11-09T09:00:00"},
        {"return_by": "2026-11-09T17:00:00+02:00"},
        {"arrival_buffer_min_minutes": 40},
        {"max_transfers": -1},
        {"budget": {"amount": -1, "currency": "RUB"}},
        {"profile_id": "medical"},
        {"require_step_free": "false"},
    ],
)
def test_invalid_activity_requests_are_rejected(update):
    with pytest.raises(ValidationError):
        JourneyRequest.model_validate(request() | update)


def test_medical_selects_service_without_guessing_institution():
    result = JourneyRequest.model_validate(
        request()
        | {
            "profile_id": "medical",
            "destination_id": None,
            "service_type_id": "demo-service",
        }
    )
    assert result.service_type_id == "demo-service"


def test_intercity_one_way_does_not_require_activity():
    data = request() | {"profile_id": "intercity"}
    for field in (
        "activity_start",
        "activity_end",
        "return_by",
        "activity_basis",
        "arrival_buffer_min_minutes",
        "arrival_buffer_max_minutes",
    ):
        data[field] = None
    assert JourneyRequest.model_validate(data).return_by is None


def test_fail_cannot_claim_incomplete_search_is_conclusive():
    with pytest.raises(ValidationError):
        JourneyResult.model_validate(
            {
                "status": "partial",
                "routes": [],
                "overall_feasibility": "fail",
                "constraints": {},
                "missing_fields": ["calendar"],
                "reason_codes": ["missing_calendar"],
                "evidence_ids": [],
                "assumptions": [],
                "assumption_ids": [],
                "provenance": {
                    "bundle_id": "demo",
                    "profile_id": "regular",
                    "profile_version": 1,
                    "code_version": "demo",
                    "completeness_scope": "rail-only",
                    "network_complete": False,
                    "search_complete": True,
                },
                "optimality": "not_proven",
            }
        )


def test_profile_rejects_wrong_metric_and_unknown_timezone():
    data = dict(
        profile_id="regular",
        profile_version=1,
        study_area_id="demo-area",
        study_area_version=1,
        metric_kind="activity_feasibility",
        start_date="2026-11-09",
        end_date="2026-11-13",
        timezone="Europe/Kaliningrad",
        destination_ids=["demo-work"],
        service_type_ids=[],
        modes=["rail", "walk"],
        rules={
            k: request()[k]
            for k in (
                "max_one_way_minutes",
                "max_transfers",
                "max_access_walk_minutes",
                "budget",
                "require_step_free",
            )
        },
        start_times=["06:00:00"],
        weighting="uniform",
        basis="scenario",
        assumption_ids=["demo-hours"],
        evidence_ids=[],
        half_life_minutes=None,
        horizon_hours=None,
    )
    assert ProfileConfig.model_validate(data).profile_version == 1
    for update in (
        {"metric_kind": "time_decay"},
        {"timezone": "bad/zone"},
        {"end_date": "2026-11-08"},
        {"profile_version": "1"},
    ):
        with pytest.raises(ValidationError):
            ProfileConfig.model_validate(data | update)


def test_task_set_cannot_drop_all_tasks_or_duplicate_case_ids():
    data = dict(
        task_set_hash="demo-hash",
        generation_rules_hash="demo-rules",
        profile_id="regular",
        profile_version=1,
        start_date="2026-11-09",
        end_date="2026-11-13",
        cases=[],
        assumption_ids=[],
    )
    with pytest.raises(ValidationError):
        TaskSet.model_validate(data)
