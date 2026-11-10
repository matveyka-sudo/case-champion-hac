"""TEST_ONLY: frozen endpoints come from accepted facts, including absent tasks."""

import json
from pathlib import Path

import pytest
from a2_api.core.scenario_scope import build_scenario_endpoint_scope
from a2_contracts.common import canonical_hash
from a2_contracts.v2.dataset import AcceptedData
from a2_contracts.v2.requests import UrbanPassengerRequest


def inputs():
    root = Path(__file__).resolve().parents[3]
    snapshot = AcceptedData.model_validate(
        json.loads((root / "packages/contracts/fixtures/v2/accepted-data.json").read_text())[
            "payload"
        ]
    )
    category = snapshot.destinations[0].categories[0]
    request = UrbanPassengerRequest(
        schema_version="2",
        analysis_kind="urban_passenger",
        city_id=snapshot.sample_points[0].territory_id,
        passenger_mode="public_transport",
        categories=[category],
        priorities={category: "medium"},
        departure_at="2026-11-09T06:00:00Z",
        traffic_mode="static",
        snapshot_id=snapshot.snapshot_id,
    )
    return snapshot, request


def test_scope_freezes_source_endpoints_without_needing_prior_journeys():
    snapshot, request = inputs()
    scope = build_scenario_endpoint_scope(snapshot, request)
    assert scope.snapshot_hash == canonical_hash(snapshot.model_dump(mode="json"))
    assert scope.city_id == request.city_id
    assert scope.point_origins == {
        p.point_id: "physical:point:" + p.point_id
        for p in snapshot.sample_points
        if p.territory_id == request.city_id
    }
    assert scope.point_access_nodes == {
        p.point_id: p.access_node_id
        for p in snapshot.sample_points
        if p.territory_id == request.city_id
    }
    expected = [d for d in snapshot.destinations if set(d.categories) & set(request.categories)]
    assert scope.destination_nodes == {
        d.destination_id: "physical:destination:" + d.destination_id for d in expected
    }
    assert scope.destination_categories == {
        d.destination_id: sorted(d.categories) for d in expected
    }


def test_a_foreign_snapshot_cannot_supply_scenario_endpoints():
    snapshot, request = inputs()
    with pytest.raises(ValueError, match="snapshot differs"):
        build_scenario_endpoint_scope(snapshot, request.model_copy(update={"snapshot_id": "other"}))


def test_destination_mapping_cannot_silently_omit_category_identity():
    snapshot, request = inputs()
    scope = build_scenario_endpoint_scope(snapshot, request)
    raw = scope.model_dump(mode="json")
    raw["destination_categories"] = {}
    with pytest.raises(ValueError, match="same identities"):
        type(scope).model_validate(raw)
