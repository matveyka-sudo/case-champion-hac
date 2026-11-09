"""Contracts reject unsupported claims and preserve service-day information."""

import importlib
import importlib.util
from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError


def dataset() -> Any:
    assert importlib.util.find_spec("a2_contracts.dataset") is not None, "dataset DTOs missing"
    return importlib.import_module("a2_contracts.dataset")


def bundle_payload() -> dict[str, Any]:
    scope = {
        "origin_ids": ["origin-1"],
        "destination_ids": ["dest-1"],
        "modes": ["rail", "walk"],
        "valid_period": {"valid_from": "2026-11-09", "valid_to": None},
    }
    return {
        "bundle_id": "bundle-1",
        "timetable_snapshot": "timetable-1",
        "destinations_snapshot": "destinations-1",
        "fares_snapshot": None,
        "transfer_snapshot": "transfers-1",
        "hashes": {"stop_times": "hash-1"},
        "parser_version": "parser-1",
        "acceptance_state": "accepted",
        "coverage": {"scope": scope, "is_complete": False, "missing_fields": ["end_date"]},
        "completeness_scope": scope,
        "completeness_evidence": [],
        "missing_fields": ["end_date"],
        "accepted_by": "reviewer-1",
        "accepted_at": "2026-11-07T10:00:00Z",
    }


def accepted_payload() -> dict[str, Any]:
    return {
        "bundle": bundle_payload(),
        "sources": [
            {
                "source_id": "source-1",
                "publisher": "Publisher",
                "url": "https://example.org",
                "license_status": "unknown",
            }
        ],
        "evidence": [
            {
                "evidence_id": "e-1",
                "source_id": "source-1",
                "record_id": "r-1",
                "fetched_at": "2026-11-07T10:00:00Z",
                "valid_period": {"valid_from": "2026-11-09", "valid_to": None},
                "content_hash": "hash-1",
                "verification_state": "unknown",
            }
        ],
        "origins": [
            {
                "origin_id": "origin-1",
                "city_id": "city-1",
                "anchor_id": "anchor-1",
                "lon": 20.5,
                "lat": 54.7,
                "timezone": "Europe/Kaliningrad",
                "type": "anchor",
                "evidence_ids": ["e-1"],
            }
        ],
        "destinations": [
            {
                "destination_id": "dest-1",
                "city_id": "city-2",
                "anchor_id": "anchor-2",
                "lon": 20.0,
                "lat": 55.0,
                "timezone": "Europe/Kaliningrad",
                "type": "medical",
                "evidence_ids": ["e-1"],
                "entrance_id": "entrance-1",
                "verification_state": "unknown",
            }
        ],
        "service_offerings": [
            {
                "destination_id": "dest-1",
                "service_type_id": "service-1",
                "eligibility": "unknown",
                "valid_from": "2026-11-09",
                "valid_to": None,
                "service_verified": "verified",
                "hours_verified": "unknown",
                "evidence_ids": ["e-1"],
            }
        ],
        "stops": [{"stop_id": "feed:stop-1", "parent_station_id": None, "evidence_ids": ["e-1"]}],
        "trips": [
            {
                "trip_id": "feed:trip-1",
                "service_id": "feed:calendar-1",
                "operator_id": "operator-1",
                "mode": "rail",
                "evidence_ids": ["e-1"],
            }
        ],
        "stop_times": [
            {
                "trip_id": "feed:trip-1",
                "stop_id": "feed:stop-1",
                "sequence": 1,
                "arrival_seconds": 90600,
                "departure_seconds": 90720,
                "service_id": "feed:calendar-1",
                "pickup_allowed": True,
                "dropoff_allowed": True,
            }
        ],
        "calendars": [
            {
                "service_id": "feed:calendar-1",
                "valid_period": {"valid_from": "2026-11-09", "valid_to": None},
                "weekdays": [0, 1, 2, 3, 4],
                "exceptions": [
                    {"service_date": "2026-11-10", "operation": "remove", "evidence_ids": ["e-1"]}
                ],
                "timezone": "Europe/Kaliningrad",
                "evidence_ids": ["e-1"],
            }
        ],
        "transfers": [
            {
                "from_id": "anchor-1",
                "to_id": "feed:stop-1",
                "duration_seconds": 300,
                "access_state": "unknown",
                "basis": "scenario",
                "kind": "access",
                "restrictions": [],
                "evidence_ids": ["e-1"],
            }
        ],
        "fares": [],
    }


def test_unknown_validity_end_is_preserved() -> None:
    model = dataset().ValidityPeriod(valid_from="2026-11-09", valid_to=None)
    assert model.valid_to is None


def test_validity_period_rejects_reversed_dates() -> None:
    with pytest.raises(ValidationError):
        dataset().ValidityPeriod(valid_from="2026-11-10", valid_to="2026-11-09")


def test_accepted_bundle_requires_explicit_reviewer() -> None:
    payload = bundle_payload()
    payload["accepted_by"] = None
    with pytest.raises(ValidationError):
        dataset().SnapshotBundle.model_validate(payload)


def test_unaccepted_bundle_is_not_promoted_during_load() -> None:
    payload = accepted_payload()
    payload["bundle"]["acceptance_state"] = "validated"
    with pytest.raises(ValidationError):
        dataset().AcceptedData.model_validate(payload)


def test_service_seconds_over_midnight_are_not_wrapped() -> None:
    model = dataset().StopTime.model_validate(accepted_payload()["stop_times"][0])
    assert model.arrival_seconds == 90600
    assert model.departure_seconds == 90720


@pytest.mark.parametrize(
    "change",
    [
        {"arrival_seconds": -1},
        {"arrival_seconds": 90721},
        {"sequence": -1},
        {"departure_seconds": 3.5},
    ],
)
def test_invalid_stop_event_is_rejected(change: dict[str, Any]) -> None:
    payload = accepted_payload()["stop_times"][0] | change
    with pytest.raises(ValidationError):
        dataset().StopTime.model_validate(payload)


@pytest.mark.parametrize(
    "change", [{"lon": 181}, {"lat": -91}, {"timezone": "Invalid/Timezone"}, {"lon": float("nan")}]
)
def test_invalid_place_is_rejected(change: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        dataset().Origin.model_validate(accepted_payload()["origins"][0] | change)


def test_evidence_requires_aware_timestamp() -> None:
    payload = accepted_payload()["evidence"][0] | {"fetched_at": "2026-11-07T10:00:00"}
    with pytest.raises(ValidationError):
        dataset().Evidence.model_validate(payload)


def test_evidence_timestamp_is_normalized_to_utc() -> None:
    payload = accepted_payload()["evidence"][0] | {"fetched_at": "2026-11-07T12:00:00+02:00"}
    assert (
        dataset().Evidence.model_validate(payload).fetched_at.isoformat()
        == "2026-11-07T10:00:00+00:00"
    )


def test_service_and_hours_verification_are_independent() -> None:
    model = dataset().ServiceOffering.model_validate(accepted_payload()["service_offerings"][0])
    assert model.service_verified == "verified"
    assert model.hours_verified == "unknown"


def test_accepted_data_retains_partial_coverage() -> None:
    model = dataset().AcceptedData.model_validate(accepted_payload())
    assert model.bundle.coverage.is_complete is False
    assert model.bundle.missing_fields == ["end_date"]


@pytest.mark.parametrize(
    "table,field,value",
    [
        ("evidence", "source_id", "missing-source"),
        ("stop_times", "trip_id", "missing-trip"),
        ("stop_times", "stop_id", "missing-stop"),
        ("stop_times", "service_id", "missing-service"),
        ("trips", "service_id", "missing-service"),
        ("origins", "evidence_ids", ["missing-evidence"]),
        ("service_offerings", "destination_id", "missing-destination"),
        ("stops", "parent_station_id", "missing-station"),
        ("transfers", "to_id", "missing-endpoint"),
    ],
)
def test_dangling_foreign_keys_are_rejected(table: str, field: str, value: Any) -> None:
    payload = accepted_payload()
    payload[table][0][field] = value
    with pytest.raises(ValidationError):
        dataset().AcceptedData.model_validate(payload)


@pytest.mark.parametrize(
    "table",
    [
        "sources",
        "evidence",
        "origins",
        "destinations",
        "trips",
        "stops",
        "calendars",
        "stop_times",
        "service_offerings",
    ],
)
def test_duplicate_keys_are_rejected(table: str) -> None:
    payload = accepted_payload()
    payload[table].append(deepcopy(payload[table][0]))
    with pytest.raises(ValidationError):
        dataset().AcceptedData.model_validate(payload)


def test_trip_chain_cannot_travel_back_in_time() -> None:
    payload = accepted_payload()
    payload["stop_times"].append(
        payload["stop_times"][0]
        | {"sequence": 2, "arrival_seconds": 90000, "departure_seconds": 90100}
    )
    with pytest.raises(ValidationError):
        dataset().AcceptedData.model_validate(payload)


def test_source_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        dataset().Source.model_validate(accepted_payload()["sources"][0] | {"trusted": True})


def test_calendar_rejects_duplicate_exception_dates() -> None:
    payload = accepted_payload()["calendars"][0]
    payload["exceptions"].append(payload["exceptions"][0] | {"operation": "add"})
    with pytest.raises(ValidationError):
        dataset().Calendar.model_validate(payload)


def test_complete_scope_requires_evidence() -> None:
    payload = bundle_payload()
    payload["coverage"]["is_complete"] = True
    with pytest.raises(ValidationError):
        dataset().SnapshotBundle.model_validate(payload)


def availability_payload() -> dict[str, Any]:
    return {
        "start": "09:00:00",
        "end": "17:00:00",
        "end_day_offset": 0,
        "weekdays": [0, 1, 2, 3, 4],
        "valid_period": {"valid_from": "2026-11-09", "valid_to": None},
        "timezone": "Europe/Moscow",
        "verification_state": "verified",
        "evidence_ids": ["e-1"],
    }


def applicability_payload() -> dict[str, Any]:
    return {
        "passenger_categories": ["adult"],
        "requires_referral": None,
        "restrictions": ["appointment_required"],
        "evidence_ids": ["e-1"],
    }


def test_verified_service_hours_require_actual_windows() -> None:
    payload = accepted_payload()["service_offerings"][0] | {"hours_verified": "verified"}
    with pytest.raises(ValidationError):
        dataset().ServiceOffering.model_validate(payload)


def test_service_offering_transmits_hours_and_applicability() -> None:
    payload = accepted_payload()["service_offerings"][0] | {
        "hours_verified": "verified",
        "availability_windows": [availability_payload()],
        "applicability": applicability_payload(),
    }
    result = dataset().ServiceOffering.model_validate(payload)
    assert result.availability_windows[0].start.isoformat() == "09:00:00"
    assert result.applicability.requires_referral is None
    assert result.applicability.restrictions == ["appointment_required"]


def test_unknown_hours_and_applicability_remain_null() -> None:
    result = dataset().ServiceOffering.model_validate(accepted_payload()["service_offerings"][0])
    assert result.availability_windows is None
    assert result.applicability is None


@pytest.mark.parametrize("change", [{"evidence_ids": []}, {"verification_state": "unknown"}])
def test_verified_service_hours_require_verified_window_evidence(change: dict[str, Any]) -> None:
    payload = accepted_payload()["service_offerings"][0] | {
        "hours_verified": "verified",
        "availability_windows": [availability_payload() | change],
    }
    with pytest.raises(ValidationError):
        dataset().ServiceOffering.model_validate(payload)


def test_explicit_overnight_hours_preserve_next_day_offset() -> None:
    payload = availability_payload() | {"start": "22:00:00", "end": "02:00:00", "end_day_offset": 1}
    offering = accepted_payload()["service_offerings"][0] | {"availability_windows": [payload]}
    result = dataset().ServiceOffering.model_validate(offering)
    assert result.availability_windows[0].end_day_offset == 1
    assert result.availability_windows[0].end.isoformat() == "02:00:00"


@pytest.mark.parametrize(
    "change",
    [
        {"start": "17:00:00", "end": "09:00:00"},
        {"end_day_offset": 2},
        {"end_day_offset": True},
        {"weekdays": [7]},
        {"weekdays": [0, 0]},
        {"weekdays": []},
        {"timezone": "Invalid/Timezone"},
        {"start": "09:00:00+03:00"},
    ],
)
def test_service_availability_rejects_invalid_local_windows(change: dict[str, Any]) -> None:
    assert hasattr(dataset(), "AvailabilityWindow"), "typed availability windows missing"
    with pytest.raises(ValidationError):
        dataset().AvailabilityWindow.model_validate(availability_payload() | change)


@pytest.mark.parametrize("value", ["true", 1])
def test_service_applicability_rejects_coerced_referral_flag(value: Any) -> None:
    assert hasattr(dataset(), "Applicability"), "typed applicability missing"
    with pytest.raises(ValidationError):
        dataset().Applicability.model_validate(
            applicability_payload() | {"requires_referral": value}
        )


@pytest.mark.parametrize("field", ["availability_windows", "applicability"])
def test_service_details_reject_dangling_evidence(field: str) -> None:
    payload = accepted_payload()
    detail = availability_payload() if field == "availability_windows" else applicability_payload()
    detail["evidence_ids"] = ["missing-evidence"]
    payload["service_offerings"][0][field] = [detail] if field == "availability_windows" else detail
    dataset().ServiceOffering.model_validate(payload["service_offerings"][0])
    with pytest.raises(ValidationError):
        dataset().AcceptedData.model_validate(payload)
