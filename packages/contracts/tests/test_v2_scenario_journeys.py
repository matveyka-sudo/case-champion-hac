"""Synthetic scenario chain inventory; saved baseline lookup remains server-side."""

from copy import deepcopy

import pytest
from a2_contracts.v2.scenarios import ScenarioResult
from pydantic import ValidationError
from test_v2_scenarios import result


def with_chain():
    value = result()
    value["before"]["metrics"][0]["name"] = "total_seconds"
    value["after"]["metrics"][0].update(name="total_seconds", journey_id="new-chain")
    value["after_journeys"] = [
        dict(
            journey_id="new-chain",
            scope="intercity",
            origin_node_id="node-1",
            destination_node_id="node-2",
            requested_departure_at="2026-11-08T10:00:00Z",
            departure_at="2026-11-08T10:00:00Z",
            arrival_at="2026-11-08T10:00:35Z",
            total_seconds=35.0,
            initial_wait_seconds=0.0,
            path_seconds=35.0,
            total_wait_seconds=0.0,
            legs=[
                dict(
                    mode="rail",
                    kind="ride",
                    from_node_id="node-1",
                    to_node_id="node-2",
                    start_at="2026-11-08T10:00:00Z",
                    end_at="2026-11-08T10:00:35Z",
                    duration_seconds=35.0,
                    service_id="service-1",
                    evidence_refs=["e-1"],
                )
            ],
            geometry=None,
            geometry_kind="none",
            status="available",
            reason_code=None,
            snapshot_id="snapshot-1",
            completeness="full",
            traffic_mode="static",
            time_kind="complete_chain",
            reference_id=None,
            destination_object_id=None,
            evidence_refs=["e-1"],
        )
    ]
    return value


def test_new_chain_survives_canonical_roundtrip():
    value = ScenarioResult.model_validate(with_chain())
    assert ScenarioResult.model_validate_json(value.model_dump_json()) == value
    assert value.after_journeys[0].total_seconds == value.after.metrics[0].value


def test_legacy_scenario_serialization_omits_empty_new_inventory():
    assert "after_journeys" not in ScenarioResult.model_validate(result()).model_dump(mode="json")


@pytest.mark.parametrize("mutation", ["duplicate", "unused", "snapshot", "evidence", "leg"])
def test_after_chain_cannot_be_foreign_unresolved_or_unreferenced(mutation):
    value = with_chain()
    journey = value["after_journeys"][0]
    if mutation == "duplicate":
        value["after_journeys"].append(deepcopy(journey))
    elif mutation == "unused":
        value["after"]["metrics"][0]["journey_id"] = None
    elif mutation == "snapshot":
        journey["snapshot_id"] = "foreign-snapshot"
    elif mutation == "evidence":
        journey["evidence_refs"] = ["unknown-source"]
    else:
        journey["legs"][0]["evidence_refs"] = ["unknown-source"]
    with pytest.raises(ValidationError):
        ScenarioResult.model_validate(value)
