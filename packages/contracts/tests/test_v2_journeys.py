import pytest
from a2_contracts.v2.journeys import JourneyResult, LastReturn, Leg
from pydantic import ValidationError


def test_leg_uses_actual_elapsed_seconds_and_never_invents_pipeline_route():
    data = dict(
        mode="walk",
        kind="walk",
        from_node_id="demo-a",
        to_node_id="demo-b",
        start_at="2026-11-09T08:15:00+03:00",
        end_at="2026-11-09T08:20:00+03:00",
        duration_seconds=300,
        service_id=None,
        evidence_refs=["demo-evidence"],
    )
    assert Leg.model_validate(data).duration_seconds == 300
    for update in ({"duration_seconds": 301}, {"mode": "pipeline"}):
        with pytest.raises(ValidationError):
            Leg.model_validate(data | update)


def test_intercity_initial_wait_cannot_be_counted_twice():
    data = dict(
        journey_id="demo",
        scope="intercity",
        origin_node_id="demo-a",
        destination_node_id="demo-b",
        requested_departure_at="2026-11-09T08:00:00+03:00",
        departure_at="2026-11-09T08:15:00+03:00",
        arrival_at="2026-11-09T08:45:00+03:00",
        total_seconds=2700,
        initial_wait_seconds=900,
        path_seconds=1800,
        total_wait_seconds=900,
        time_kind="complete_chain",
        status="available",
        reason_code=None,
        snapshot_id="demo-snapshot",
        completeness="full",
        traffic_mode="static",
        reference_id=None,
        geometry=None,
        geometry_kind="none",
        evidence_refs=["demo-evidence"],
        destination_object_id=None,
        legs=[
            dict(
                mode="rail",
                kind="wait_initial",
                from_node_id="demo-a",
                to_node_id="demo-a",
                start_at="2026-11-09T08:00:00+03:00",
                end_at="2026-11-09T08:15:00+03:00",
                duration_seconds=900,
                service_id="demo-rail",
                evidence_refs=["demo-evidence"],
            ),
            dict(
                mode="rail",
                kind="ride",
                from_node_id="demo-a",
                to_node_id="demo-b",
                start_at="2026-11-09T08:15:00+03:00",
                end_at="2026-11-09T08:45:00+03:00",
                duration_seconds=1800,
                service_id="demo-rail",
                evidence_refs=["demo-evidence"],
            ),
        ],
    )
    assert JourneyResult.model_validate(data).path_seconds == 1800
    with pytest.raises(ValidationError):
        JourneyResult.model_validate(data | {"path_seconds": 2700})


def test_last_return_first_departure_day_is_local_but_arrival_may_cross_midnight():
    data = dict(
        point_id="demo-point",
        destination_object_id="demo-object",
        journey_id="demo-return",
        status="available",
        reason_code=None,
        service_date="2026-11-09",
        timezone="Europe/Moscow",
        leave_at="2026-11-09T23:30:00+03:00",
        first_departure_at="2026-11-09T23:45:00+03:00",
        arrival_at="2026-11-10T00:30:00+03:00",
        evidence_refs=["demo-evidence"],
    )
    assert LastReturn.model_validate(data).arrival_at.day == 10
    with pytest.raises(ValidationError):
        LastReturn.model_validate(data | {"service_date": "2026-11-10"})
