"""Independent regressions: absence of links must not imply absence of operands."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from a2_contracts.v2.results import AnalysisResult
from pydantic import ValidationError
from test_required_result_dependencies import complete_result


def urban_result():
    raw = complete_result()
    raw.update(analysis_kind="urban_passenger", journeys=[], references=[])
    raw["request"] = dict(
        schema_version="2",
        analysis_kind="urban_passenger",
        city_id="city-1",
        departure_at="2026-11-09T08:15:00+03:00",
        traffic_mode="static",
        passenger_mode="walk",
        categories=["school"],
        priorities={"school": "high"},
    )
    raw["request"].update(
        snapshot_id=raw["snapshot_id"],
        methodology=dict(
            version=raw["methodology_version"],
            config_hash=raw["config_hash"],
            reference_profile="edge-calibrated-v1",
        ),
    )
    raw["metrics"] = [raw["metrics"][0]]
    raw["metrics"][0].update(
        journey_id=None,
        subject=dict(kind="sample_point", point_id="point-1", category="school", mode="walk"),
    )
    basis = dict(
        population=100,
        population_method="synthetic",
        year=2026,
        coverage=1.0,
        unit="persons",
        unknown_positive_weight=False,
    )
    raw["metrics"][0]["population_basis"] = deepcopy(basis)
    raw["scores"] = [raw["scores"][0]]
    raw["scores"][0].pop("calculation_kind", None)
    raw["scores"][0].update(
        subject=dict(kind="territory", territory_id="city-1", mode="walk"),
        aggregation_basis=basis,
        ranking_eligible=False,
    )
    return raw


def test_complete_controls_before_adversarial_mutation():
    assert AnalysisResult.model_validate(complete_result()).scores[-1].value > 70
    assert AnalysisResult.model_validate(urban_result()).scores[0].value == 50


@pytest.mark.parametrize(
    "removed", ["journey_reference", "metric_journey", "reference", "all_links"]
)
def test_removed_required_reference_links_cannot_claim_full(removed):
    raw = complete_result()
    raw["references"][0]["completeness"] = "partial"
    if removed in {"journey_reference", "reference", "all_links"}:
        raw["journeys"][0]["reference_id"] = None
    if removed in {"metric_journey", "all_links"}:
        raw["metrics"][0]["journey_id"] = None
    if removed == "reference":
        raw["references"] = []
    for score in raw["scores"]:
        score.pop("reference_ids", None)
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(raw)


@pytest.mark.parametrize(
    "basis", [None, dict(site_count=1, weighting="equal_physical_sites", coverage=1)]
)
def test_territorial_full_basis_cannot_be_removed_or_swapped_with_ranking_disabled(basis):
    raw = urban_result()
    raw["metrics"][0]["population_basis"].update(coverage=0.5, unknown_positive_weight=True)
    raw["scores"][0]["aggregation_basis"] = basis
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(raw)


def test_full_territorial_basis_cannot_conceal_incomplete_component_weights():
    raw = urban_result()
    raw["metrics"][0]["population_basis"].update(coverage=0.5, unknown_positive_weight=True)
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(raw)


def test_historical_untyped_full_intercity_index_is_not_silently_accepted():
    raw = complete_result()
    for score in raw["scores"]:
        score.pop("calculation_kind", None)
    with pytest.raises(ValidationError, match="explicit calculation semantics"):
        AnalysisResult.model_validate(raw)


def test_partial_historical_intercity_value_keeps_known_raw_times():
    raw = complete_result()
    raw["status"] = "partial"
    for score in raw["scores"]:
        score.pop("calculation_kind", None)
        score.update(completeness="partial", ranking_eligible=False)
    assert AnalysisResult.model_validate(raw).metrics[0].value == 1800


def test_urban_h_index_does_not_depend_on_an_unrelated_partial_reference():
    raw = urban_result()
    original = complete_result()
    raw["references"] = original["references"]
    raw["references"][0]["completeness"] = "partial"
    raw["journeys"] = original["journeys"]
    raw["journeys"][0]["scope"] = "urban"
    raw["metrics"][0]["journey_id"] = "path"
    assert AnalysisResult.model_validate(raw).scores[0].value == 50
    raw["scores"][0]["calculation_kind"] = "reference_ratio"
    with pytest.raises(ValidationError, match="analysis methodology"):
        AnalysisResult.model_validate(raw)


def test_urban_partial_keeps_preliminary_value_and_raw_time():
    raw = urban_result()
    raw["status"] = "partial"
    raw["scores"][0].update(completeness="partial", aggregation_basis=None)
    raw["metrics"][0]["population_basis"].update(coverage=0.5, unknown_positive_weight=True)
    saved = AnalysisResult.model_validate(raw)
    assert saved.scores[0].value == 50
    assert saved.metrics[0].value == 1800


@pytest.mark.parametrize("cargo", [False, True])
def test_full_territorial_basis_has_to_match_analysis_kind(cargo):
    raw = urban_result()
    if cargo:
        raw.update(analysis_kind="urban_cargo")
        raw["request"] = json.loads(
            (Path(__file__).parents[1] / "fixtures/v2/urban_cargo-result.json").read_text()
        )["payload"]["request"]
    else:
        raw["scores"][0]["aggregation_basis"] = dict(
            site_count=1, weighting="equal_physical_sites", coverage=1
        )
    with pytest.raises(ValidationError, match="weighting must match"):
        AnalysisResult.model_validate(raw)


def test_intermediate_full_score_cannot_erase_reference_requirement():
    raw = complete_result()
    middle = deepcopy(raw["scores"][2])
    middle.update(score_id="middle", components=[dict(kind="score", id="path-index")])
    raw["scores"].insert(2, middle)
    raw["scores"][-1]["components"] = [dict(kind="score", id="middle")]
    assert AnalysisResult.model_validate(raw).scores[-1].value > 70
    raw["journeys"][0]["reference_id"] = None
    raw["references"] = []
    with pytest.raises(ValidationError, match="reference operand"):
        AnalysisResult.model_validate(raw)


def test_initial_wait_is_independent_of_partial_reference():
    raw = complete_result()
    raw["scores"] = [raw["scores"][1]]
    raw["references"][0]["completeness"] = "partial"
    assert AnalysisResult.model_validate(raw).scores[0].value == 100


def test_reference_ratio_explicit_full_operand_survives_without_journey_link():
    raw = complete_result()
    raw["scores"] = [raw["scores"][0]]
    raw["scores"][0]["reference_ids"] = ["reference"]
    raw["metrics"][0]["journey_id"] = None
    assert AnalysisResult.model_validate(raw).scores[0].value == 50
    raw["references"][0]["completeness"] = "partial"
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(raw)


def cargo_aggregate_result():
    raw = json.loads(
        (Path(__file__).parents[1] / "fixtures/v2/intercity_cargo-result.json").read_text()
    )["payload"]
    aggregate = deepcopy(raw["scores"][0])
    aggregate.update(
        score_id="cargo-aggregate",
        calculation_kind="aggregate",
        components=[dict(kind="score", id=raw["scores"][0]["score_id"])],
    )
    raw["scores"].append(aggregate)
    return raw


def test_full_cargo_aggregate_accepts_nested_complete_delivery_operand():
    saved = AnalysisResult.model_validate(cargo_aggregate_result())
    assert saved.scores[-1].calculation_kind == "aggregate"


@pytest.mark.parametrize("state", ["movement", "unknown"])
def test_full_cargo_aggregate_cannot_hide_incomplete_delivery_operand(state):
    raw = cargo_aggregate_result()
    if state == "movement":
        raw["metrics"][0]["time_kind"] = "movement"
    else:
        raw["metrics"][0].update(
            value=None, status="unknown", reason_code="DELIVERY_MISSING", completeness="partial"
        )
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(raw)


@pytest.mark.parametrize("intermediate", [False, True])
def test_passenger_aggregate_cannot_delete_its_entire_path_operand(intermediate):
    raw = complete_result()
    raw["references"][0]["completeness"] = "partial"
    raw["journeys"][0]["reference_id"] = None
    wait, combined = raw["scores"][1:]
    combined["components"] = [dict(kind="score", id=wait["score_id"])]
    raw["scores"] = [wait, combined]
    if intermediate:
        middle = deepcopy(combined)
        middle["score_id"] = "middle"
        combined["components"] = [dict(kind="score", id="middle")]
        raw["scores"].insert(1, middle)
    with pytest.raises(ValidationError, match="path operand"):
        AnalysisResult.model_validate(raw)
