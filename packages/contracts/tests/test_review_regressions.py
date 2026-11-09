from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from a2_contracts.journeys import JourneyResult, Leg
from a2_contracts.results import CaseResult, ComparisonEntry
from a2_contracts.scenarios import Comparison
from pydantic import ValidationError


def provenance():
    return dict(
        bundle_id="demo-bundle",
        profile_id="regular",
        profile_version=1,
        task_set_hash="demo-task",
        code_version="demo-code",
        completeness_scope="demo-scope",
        network_complete=True,
        search_complete=True,
    )


def journey():
    return dict(
        status="ok",
        overall_feasibility="pass",
        constraints={"outbound": "pass"},
        routes=[
            dict(
                journey_id="demo-route",
                legs=[
                    dict(
                        mode="walk",
                        from_id="demo-a",
                        to_id="demo-b",
                        trip_id=None,
                        departure_at="2026-11-09T06:00:00Z",
                        arrival_at="2026-11-09T06:01:00Z",
                        duration_seconds=60,
                        evidence_ids=[],
                    )
                ],
                outbound_minutes=1,
                return_minutes=None,
                waiting_minutes=0,
                transfers=0,
                total_fare=None,
                currency=None,
            )
        ],
        missing_fields=[],
        reason_codes=[],
        evidence_ids=[],
        assumptions=[],
        assumption_ids=[],
        provenance=provenance(),
        optimality="proven_in_scope",
    )


@pytest.mark.parametrize("state", ["fail", "unknown"])
def test_overall_pass_cannot_hide_unsatisfied_constraint(state):
    data = journey() | {"constraints": {"budget": state}}
    with pytest.raises(ValidationError):
        JourneyResult.model_validate(data)
    with pytest.raises(ValidationError):
        CaseResult(
            case_id="demo",
            weight=1,
            overall_feasibility="pass",
            constraint_results={"budget": state},
            reason_codes=[],
            journey_ids=["demo-route"],
            evidence_ids=[],
            assumption_ids=[],
        )


def entry():
    return dict(
        profile_id="regular",
        profile_version=1,
        task_set_hash="demo-task",
        base_run_id="demo-base",
        scenario_run_id="demo-scenario",
        origin_id="demo-a",
        status="partial",
        before=[20, 40],
        after=[30, 50],
        lower_bound_change=10,
        upper_bound_change=10,
        delta_interval_conservative=[-10, 30],
        confirmed_improved_cases=1,
        confirmed_worse_cases=0,
        unresolved_cases=1,
        worst_worsened_case_ids=[],
        reason=None,
    )


@pytest.mark.parametrize(
    "update",
    [
        {"lower_bound_change": -10000},
        {"upper_bound_change": 900},
        {"delta_interval_conservative": [999, -999]},
        {"status": "ok"},
    ],
)
def test_comparison_effects_must_match_supplied_intervals(update):
    assert ComparisonEntry.model_validate(entry()).lower_bound_change == 10
    with pytest.raises(ValidationError):
        ComparisonEntry.model_validate(entry() | update)


def comparison():
    return dict(
        run_id="demo-result",
        scenario_id="demo-scenario",
        bundle_id="demo-bundle",
        base_run_ids={"regular": "demo-base"},
        status="partial",
        operations=[
            dict(
                type="shift_trip",
                trip_id="demo-trip",
                service_dates=["2026-11-09"],
                delta_seconds=300,
            )
        ],
        operations_hash="demo-hash",
        comparisons=[entry()],
        resource_delta={"departures": 0, "vehicle_minutes": None},
        operational_validation="not_verified",
        provenance=[provenance()],
        evidence_ids=[],
        assumption_ids=[],
        code_version="demo-code",
        params_hash="demo-params",
        result_hash="demo-hash",
        warnings=[],
    )


@pytest.mark.parametrize(
    "update",
    [
        {"status": "ok"},
        {"provenance": []},
        {"operations": []},
        {"base_run_ids": {"regular": "demo-base", "medical": "demo-medical"}},
        {"provenance": [provenance() | {"bundle_id": "different"}]},
    ],
)
def test_comparison_parent_keeps_profiles_and_provenance_complete(update):
    assert Comparison.model_validate(comparison()).status == "partial"
    with pytest.raises(ValidationError):
        Comparison.model_validate(comparison() | update)


def test_engine_python_times_and_wire_times_agree_across_dst():
    zone = ZoneInfo("Europe/Berlin")
    departure = datetime(2026, 10, 25, 2, 30, tzinfo=zone, fold=0)
    arrival = datetime(2026, 10, 25, 2, 45, tzinfo=zone, fold=1)
    leg = Leg(
        mode="walk",
        from_id="a",
        to_id="b",
        trip_id=None,
        departure_at=departure,
        arrival_at=arrival,
        duration_seconds=4500,
        evidence_ids=[],
    )
    assert leg.duration_seconds == 4500
