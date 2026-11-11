"""TEST_ONLY: dispatch selection never changes or silently retimes accepted car facts."""

import json
from dataclasses import replace
from datetime import timedelta

import pytest
from a2_api.adapters import product
from a2_api.adapters.product_inputs import IntercityInputs
from a2_api.adapters.product_runtime import Manifest, Profile, Runtime
from a2_api.core.errors import DomainError
from a2_api.core.registry import ProductRegistry
from a2_contracts.common import canonical_hash
from a2_contracts.v2.dataset import AcceptedData
from a2_contracts.v2.journeys import LineGeometry
from a2_contracts.v2.requests import IntercityPassengerRequest
from a2_contracts.v2.results import EvidenceRef
from a2_engine import AcceptedIntercityCar
from test_product_projection import ROOT, example
from test_product_runtime import claim_for


@pytest.fixture
def configured_intercity(monkeypatch):
    module = example("intercity")
    scope, table = module.demo_inputs()
    analysis = module.run_demo()
    car = AcceptedIntercityCar(
        "TEST_ONLY-car", scope.comparison_scope, "A", "B", 3600, True, module.PROOF
    )
    inputs = IntercityInputs(
        kind="intercity_passenger",
        scope=scope,
        timetable=table,
        transport_modes=analysis.transport_modes,
        physical_services=analysis.physical_services,
        reference_graph=module.demo_reference(scope),
        reference_scope=scope.comparison_scope,
        frequency_start_at=module.START,
        frequency_end_at=module.START + timedelta(days=7),
        car=car,
        car_departure_at=module.START,
    )
    snapshot = AcceptedData.model_validate(
        json.loads((ROOT / "packages/contracts/fixtures/v2/accepted-data.json").read_text())[
            "payload"
        ]
    )
    snapshot = snapshot.model_copy(
        update={
            "snapshot_id": "snapshot",
            "provenance": snapshot.provenance.model_copy(
                update={
                    "graph_version": "graph",
                    "calendar_version": "calendar",
                    "population_version": "population",
                }
            ),
        }
    )
    profile = Profile(
        profile_id="TEST_ONLY-intercity",
        analysis_kind="intercity_passenger",
        origin_id="city-A",
        destination_id="city-B",
        valid_from=module.START,
        valid_to=module.START + timedelta(days=7),
        snapshot_id="snapshot",
        snapshot_hash=canonical_hash(snapshot.model_dump(mode="json")),
        graph_fingerprint="0" * 64,
        routing_profile_fingerprint="0" * 64,
        max_route_pairs=100,
        evidence=[
            EvidenceRef(
                evidence_id=identity,
                source_id="TEST_ONLY-source",
                url="https://example.test/TEST_ONLY",
                retrieved_at=module.START,
                observed_at=None,
                section="TEST_ONLY",
            )
            for identity in module.PROOF
        ],
    )
    runtime = Runtime(
        "a" * 64,
        Manifest(
            schema_version="waydex-product-runtime/1",
            snapshots=[],
            territories=[],
            profiles=[profile],
        ),
        {"snapshot": snapshot},
        {profile.profile_id: (profile, inputs)},
        frozenset(),
    )
    monkeypatch.setenv("A2_CODE_VERSION", "TEST_ONLY-" + runtime.digest)
    monkeypatch.setattr(product, "load_runtime", lambda **kwargs: runtime)
    return module, snapshot, runtime


def request(module, modes, *, offset=0):
    return IntercityPassengerRequest(
        schema_version="2",
        analysis_kind="intercity_passenger",
        origin_id="city-A",
        destination_id="city-B",
        territory_level="city",
        passenger_modes=modes,
        departure_at=module.START + timedelta(minutes=offset),
        traffic_mode="static",
        snapshot_id="snapshot",
        methodology=ProductRegistry().methodology,
    )


def test_public_only_request_does_not_pass_or_return_private_car(configured_intercity, monkeypatch):
    module, snapshot, _ = configured_intercity
    actual = product.analyze_intercity_passenger
    received = []

    def observe(*args, **kwargs):
        received.append(kwargs["car"])
        return actual(*args, **kwargs)

    monkeypatch.setattr(product, "analyze_intercity_passenger", observe)
    result = product.calculate(claim_for(snapshot, request(module, ["rail"])))
    assert received == [None]
    assert all(score.subject.mode != "private_car" for score in result.scores)
    assert all(journey.journey_id != "TEST_ONLY-car" for journey in result.journeys)


def test_private_car_uses_own_public_engine_path_without_scheduled_search(
    configured_intercity, monkeypatch
):
    module, snapshot, _ = configured_intercity

    def forbidden(*args, **kwargs):
        raise AssertionError("Private-car selection must not run the scheduled solver")

    monkeypatch.setattr(product, "analyze_intercity_passenger", forbidden)
    result = product.calculate(claim_for(snapshot, request(module, ["private_car"])))
    assert len(result.scores) == 1 and result.scores[0].value == 50
    assert result.scores[0].subject.mode == "private_car"
    assert result.journeys[0].initial_wait_seconds == 0
    assert result.journeys[0].geometry is None


@pytest.mark.parametrize("modes", [["road"], ["rail"], ["mixed"], ["road", "private_car"], None])
def test_car_only_dataset_preserves_unknown_public_modes(configured_intercity, modes):
    module, snapshot, runtime = configured_intercity
    key = next(iter(runtime.profiles))
    profile, inputs = runtime.profiles[key]
    table = replace(
        inputs.timetable,
        scope=replace(
            inputs.timetable.scope,
            schedule_complete=False,
            calendar_complete=False,
            transfer_version="not-collected",
        ),
        runs=(),
        footpaths=(),
        transfers=(),
    )
    runtime.profiles[key] = (
        profile,
        inputs.model_copy(
            update={
                "timetable": table,
                "transport_modes": (),
                "physical_services": (),
                "car": replace(inputs.car, complete=False),
            }
        ),
    )
    result = product.calculate(claim_for(snapshot, request(module, modes)))
    public = [s for s in result.scores if s.subject.mode != "private_car"]
    assert public and all(s.value is None and s.status == "unknown" for s in public)
    assert all(not s.ranking_eligible for s in result.scores)
    car_requested = modes is None or "private_car" in modes
    assert len(result.journeys) == int(car_requested)
    if car_requested:
        assert result.journeys[0].total_seconds == 3600
        assert result.journeys[0].completeness == "partial"
    else:
        assert all(m.value is None for m in result.metrics)
        assert result.status == "unknown"


def test_private_car_observation_cannot_be_retimed(configured_intercity):
    module, snapshot, runtime = configured_intercity
    changed = request(module, ["private_car"], offset=15)
    with pytest.raises(DomainError, match="CAR_OBSERVATION_DEPARTURE"):
        runtime.select(changed, snapshot)


def test_explicit_static_car_observation_supports_selected_departure(configured_intercity):
    module, snapshot, runtime = configured_intercity
    key = next(iter(runtime.profiles))
    profile, inputs = runtime.profiles[key]
    runtime.profiles[key] = (profile.model_copy(update={"static_observations": True}), inputs)
    changed = request(module, ["private_car"], offset=15)
    runtime.select(changed, snapshot)
    result = product.calculate(claim_for(snapshot, changed))
    assert result.journeys[0].departure_at == changed.departure_at
    assert result.journeys[0].total_seconds == inputs.car.path_seconds
    assert result.journeys[0].initial_wait_seconds == 0
    with pytest.raises(DomainError):
        runtime.select(changed.model_copy(update={"traffic_mode": "live"}), snapshot)


def test_car_geometry_survives_projection_without_changing_time(configured_intercity):
    module, snapshot, runtime = configured_intercity
    key = next(iter(runtime.profiles))
    profile, inputs = runtime.profiles[key]
    geometry = LineGeometry(
        type="LineString",
        coordinates=[(37.0, 55.0), (38.0, 54.0)],
        evidence_refs=list(module.PROOF),
    )
    runtime.profiles[key] = (profile, inputs.model_copy(update={"car_geometry": geometry}))
    result = product.calculate(claim_for(snapshot, request(module, ["private_car"])))
    assert result.journeys[0].geometry == geometry
    assert result.journeys[0].geometry_kind == "actual"
    assert result.journeys[0].total_seconds == 3600
