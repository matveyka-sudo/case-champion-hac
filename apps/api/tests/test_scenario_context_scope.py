"""TEST_ONLY: server endpoint scope is optional for history and bound when present."""

import pytest
from a2_api.core.jobs import V2JobContext
from a2_api.core.scenario_scope import ScenarioEndpointScope
from a2_contracts.common import canonical_hash
from pydantic import ValidationError
from test_v2_storage import context, proposal, result


def bound_context():
    base = context()
    snapshot_hash = canonical_hash({"TEST_ONLY": "endpoint-snapshot"})
    endpoint_scope = ScenarioEndpointScope(
        snapshot_id=base.snapshot_id,
        snapshot_hash=snapshot_hash,
        city_id=base.normalized_request.city_id,
        point_origins={"TEST_ONLY-point": "physical:point:TEST_ONLY-point"},
        point_access_nodes={"TEST_ONLY-point": "TEST_ONLY-access"},
        destination_nodes={"TEST_ONLY-object": "physical:destination:TEST_ONLY-object"},
        destination_access_nodes={"TEST_ONLY-object": "TEST_ONLY-node"},
        destination_categories={"TEST_ONLY-object": ["school"]},
    )
    payload = {
        **base.model_dump(mode="json"),
        "snapshot_hash": snapshot_hash,
        "scenario_proposal": proposal(result()).model_dump(mode="json"),
        "scenario_endpoint_scope": endpoint_scope.model_dump(mode="json"),
    }
    return payload


def test_endpoint_scope_binds_snapshot_hash_city_and_saved_proposal():
    payload = bound_context()
    parsed = V2JobContext.model_validate(payload)
    assert parsed.scenario_endpoint_scope.city_id == parsed.normalized_request.city_id
    assert parsed.scenario_endpoint_scope.snapshot_hash == parsed.snapshot_hash


@pytest.mark.parametrize("change", ["hash", "city", "proposal"])
def test_foreign_or_unproposed_endpoint_scope_is_rejected(change):
    payload = bound_context()
    if change == "hash":
        payload["scenario_endpoint_scope"]["snapshot_hash"] = "f" * 64
    elif change == "city":
        payload["scenario_endpoint_scope"]["city_id"] = "TEST_ONLY-foreign"
    else:
        payload["scenario_proposal"] = None
    with pytest.raises(ValidationError, match="immutable baseline context"):
        V2JobContext.model_validate(payload)


def test_old_context_omits_new_none_scope_for_stable_cache_identity():
    assert "scenario_endpoint_scope" not in context().model_dump(mode="json")
