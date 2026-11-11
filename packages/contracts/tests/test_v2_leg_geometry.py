"""TEST_ONLY movement-leg geometry, evidence closure and legacy wire bytes."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from a2_contracts.v2.journeys import JourneyResult, Leg
from a2_contracts.v2.results import AnalysisResult
from a2_contracts.v2.scenarios import ScenarioResult
from pydantic import ValidationError
from test_v2_scenario_journeys import with_chain
from test_v2_scenarios import result as scenario_result

FIXTURES = Path(__file__).parents[1] / "fixtures/v2"


def line(ref="TEST_ONLY-geometry-proof"):
    return dict(type="LineString", coordinates=[[37.6, 55.7], [37.61, 55.71]], evidence_refs=[ref])


def leg():
    return dict(
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


@pytest.mark.parametrize("kind,mode", [("walk", "walk"), ("ride", "rail")])
def test_actual_geometry_belongs_to_its_movement_leg(kind, mode):
    data = leg() | dict(kind=kind, mode=mode, geometry=line())
    parsed = Leg.model_validate(data)
    assert parsed.geometry.coordinates == [(37.6, 55.7), (37.61, 55.71)]
    assert Leg.model_validate_json(parsed.model_dump_json()) == parsed
    assert parsed.duration_seconds == 300


@pytest.mark.parametrize(
    "kind", ["wait_initial", "wait_transfer", "handling", "transfer_operation"]
)
def test_nonmovement_leg_cannot_claim_route_geometry(kind):
    with pytest.raises(ValidationError, match="Only movement legs"):
        Leg.model_validate(leg() | dict(kind=kind, geometry=line()))


@pytest.mark.parametrize(
    "geometry",
    [
        dict(type="LineString", coordinates=[[181, 55], [37, 55]], evidence_refs=["e"]),
        dict(type="LineString", coordinates=[[37, -91], [37, 55]], evidence_refs=["e"]),
        dict(type="LineString", coordinates=[[float("nan"), 55], [37, 55]], evidence_refs=["e"]),
        dict(type="LineString", coordinates=[[37, float("inf")], [37, 55]], evidence_refs=["e"]),
        dict(type="LineString", coordinates=[[37, 55]], evidence_refs=["e"]),
        dict(type="LineString", coordinates=[[37, 55, 100], [37, 55, 100]], evidence_refs=["e"]),
        dict(type="Point", coordinates=[[37, 55], [37, 55]], evidence_refs=["e"]),
        dict(type="LineString", coordinates=[[37, 55], [37, 55]], evidence_refs=[]),
    ],
)
def test_invalid_coordinates_type_or_missing_evidence_are_rejected(geometry):
    with pytest.raises(ValidationError):
        Leg.model_validate(leg() | dict(geometry=geometry))


def analysis():
    return json.loads((FIXTURES / "intercity_passenger-result.json").read_text())["payload"]


@pytest.mark.parametrize("container", ["analysis", "scenario"])
def test_leg_geometry_evidence_must_resolve_in_the_owning_result(container):
    data = analysis() if container == "analysis" else with_chain()
    journeys = data["journeys"] if container == "analysis" else data["after_journeys"]
    journeys[0]["legs"][0]["geometry"] = line()
    model = AnalysisResult if container == "analysis" else ScenarioResult
    with pytest.raises(ValidationError, match="evidence reference"):
        model.model_validate(data)
    evidence = deepcopy(data["evidence_refs"][0])
    evidence["evidence_id"] = "TEST_ONLY-geometry-proof"
    data["evidence_refs"].append(evidence)
    parsed = model.model_validate(data)
    assert model.model_validate_json(parsed.model_dump_json()) == parsed


def test_leg_geometry_does_not_promote_whole_journey_geometry_or_time_proof():
    data = with_chain()["after_journeys"][0]
    data.update(completeness="partial", geometry=None, geometry_kind="none")
    before = JourneyResult.model_validate(data)
    data["legs"][0]["geometry"] = line("e-1")
    after = JourneyResult.model_validate(data)
    assert after.geometry is None and after.geometry_kind == "none"
    assert after.completeness == "partial"
    assert after.model_dump(exclude={"legs"}) == before.model_dump(exclude={"legs"})
    assert after.total_seconds == before.total_seconds


def test_legacy_leg_bytes_are_identical_with_absent_or_explicit_null_geometry():
    expected = '{"mode":"walk","kind":"walk","from_node_id":"demo-a","to_node_id":"demo-b","start_at":"2026-11-09T08:15:00+03:00","end_at":"2026-11-09T08:20:00+03:00","duration_seconds":300.0,"service_id":null,"evidence_refs":["demo-evidence"]}'
    for data in (leg(), leg() | {"geometry": None}):
        parsed = Leg.model_validate(data)
        assert parsed.model_dump_json() == expected
        assert "geometry" not in parsed.model_dump(mode="json")


@pytest.mark.parametrize(
    "name,expected",
    [
        ("urban_passenger", "b9aa2f92588b0bbc4d2bd4c86c0304c00f28831218ea5ea52343856a193f00e3"),
        ("intercity_passenger", "232b354df379fe11d6f5a09482f5505880fa4c18c251e8f38ee3cb1498ad2262"),
        ("urban_cargo", "66bd6a79e58e0e8c1e909f029a4acb30413fcd43225e74ec190d635b2d5cd8dc"),
        ("intercity_cargo", "41568b724a531a2f2a93e9d9335b14c06530845370720dab9da81e86408cb0ce"),
    ],
)
def test_existing_analysis_serialized_bytes_remain_identical(name, expected):
    payload = json.loads((FIXTURES / (name + "-result.json")).read_text())["payload"]
    parsed = AnalysisResult.model_validate(payload)
    assert hashlib.sha256(parsed.model_dump_json().encode()).hexdigest() == expected
    for journey in payload.get("journeys", []):
        for item in journey["legs"]:
            item["geometry"] = None
    assert AnalysisResult.model_validate(payload).model_dump_json() == parsed.model_dump_json()


@pytest.mark.parametrize(
    "factory,expected",
    [
        (scenario_result, "059f49fc2068e0c2336b08ef3b437752bfca7718e5d044a18d54c6f561ee955c"),
        (with_chain, "24caea818f88d40bdbcb08828359e9c11e7c970b702129601c79fdf1423ce154"),
    ],
)
def test_existing_scenario_serialized_bytes_remain_identical(factory, expected):
    parsed = ScenarioResult.model_validate(factory())
    assert hashlib.sha256(parsed.model_dump_json().encode()).hexdigest() == expected
