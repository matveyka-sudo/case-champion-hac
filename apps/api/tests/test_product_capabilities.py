"""TEST_ONLY scopes: no synthetic route or numerical completeness evidence."""

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from a2_api.adapters.product_capabilities import urban_passenger_scopes
from a2_api.adapters.product_runtime import load_runtime
from a2_api.core.registry import ProductRegistry
from a2_contracts.v2.api import UrbanPassengerScope
from pydantic import ValidationError
from test_product_runtime import configure
from test_product_runtime import transport_facts as transport_facts


@pytest.fixture
def runtime(tmp_path, monkeypatch, transport_facts):
    configure(tmp_path, monkeypatch, transport_facts)
    return load_runtime()


def test_only_registered_inputs_are_advertised(runtime):
    scopes = urban_passenger_scopes(runtime)
    assert len(scopes) == 1
    assert scopes[0].categories == ["adult_polyclinic"]
    assert scopes[0].passenger_modes == ["walk"]
    assert scopes[0].whole_city and scopes[0].area_ids == []
    # The manifest still has the profile, but its runtime registration failed.
    assert urban_passenger_scopes(replace(runtime, profiles={})) == []
    assert urban_passenger_scopes(replace(runtime, snapshots={})) == []
    # Old/generic registries must not claim a detailed empty inventory.
    assert ProductRegistry().capabilities().urban_passenger_scopes is None


def test_separate_profiles_do_not_become_cross_product_of_selections(runtime):
    profile, inputs = next(iter(runtime.profiles.values()))
    other = profile.model_copy(
        update={
            "profile_id": "TEST_ONLY-other",
            "categories": ["school"],
            "passenger_modes": ["public_transport"],
        }
    )
    scopes = urban_passenger_scopes(
        replace(
            runtime,
            profiles={profile.profile_id: (profile, inputs), other.profile_id: (other, inputs)},
        )
    )
    assert len(scopes) == 2
    assert not any("school" in s.categories and "walk" in s.passenger_modes for s in scopes)
    assert not any({"school", "adult_polyclinic"} <= set(s.categories) for s in scopes)


def test_certified_date_requires_proof_and_uses_local_midnight(runtime):
    profile, _ = next(iter(runtime.profiles.values()))
    profile = profile.model_copy(
        update={"executor": "certified_walk_model", "categories": ["airport"]}
    )
    runtime = replace(runtime, profiles={profile.profile_id: (profile, None)})
    assert urban_passenger_scopes(runtime) == []
    (scope,) = urban_passenger_scopes(runtime, certified_service_date=date(2026, 11, 10))
    # Fixture city is Europe/Moscow: 00:00 local is previous day 21:00 UTC.
    assert scope.valid_from == datetime(2026, 11, 9, 21, tzinfo=UTC)
    assert scope.valid_to == datetime(2026, 11, 10, 21, tzinfo=UTC)
    assert urban_passenger_scopes(runtime, certified_service_date=date(2026, 12, 10)) == []


def test_snapshot_period_clips_profile_and_empty_intersection_is_omitted(runtime):
    snapshot = next(iter(runtime.snapshots.values()))
    period = snapshot.valid_period.model_copy(
        update={"start": date(2026, 11, 12), "end": date(2026, 11, 12)}
    )
    snapshot = snapshot.model_copy(update={"valid_period": period})
    runtime = replace(runtime, snapshots={snapshot.snapshot_id: snapshot})
    (scope,) = urban_passenger_scopes(runtime)
    assert scope.valid_from == datetime(2026, 11, 11, 21, tzinfo=UTC)
    assert scope.valid_to == datetime(2026, 11, 12, 21, tzinfo=UTC)
    profile, inputs = next(iter(runtime.profiles.values()))
    profile = profile.model_copy(update={"valid_to": scope.valid_from})
    assert (
        urban_passenger_scopes(replace(runtime, profiles={profile.profile_id: (profile, inputs)}))
        == []
    )


@pytest.mark.parametrize(
    "mutation",
    [
        {"categories": []},
        {"passenger_modes": []},
        {"traffic_modes": []},
        {"categories": ["school", "school"]},
        {"whole_city": False},
        {"valid_to": "2026-10-01T00:00:00Z"},
        {"valid_to": "2026-11-01T00:00:00"},
    ],
)
def test_invalid_public_scope_rejected(runtime, mutation):
    (scope,) = urban_passenger_scopes(runtime)
    with pytest.raises(ValidationError):
        UrbanPassengerScope.model_validate(scope.model_dump() | mutation)
