"""Synthetic scenarios require server-controlled operations and honest effects."""

import importlib
import importlib.util
from typing import Any

import pytest
from pydantic import ValidationError


def dto() -> Any:
    assert importlib.util.find_spec("a2_contracts.v2.scenarios"), "v2 scenarios missing"
    return importlib.import_module("a2_contracts.v2.scenarios")


def versions() -> dict[str, Any]:
    return {
        "snapshot_id": "snapshot-1",
        "graph_version": "graph-1",
        "calendar_version": "calendar-1",
        "population_version": "population-1",
        "methodology_version": "a2-methodology-v3",
        "config_hash": "config-1",
        "code_version": "code-1",
    }


def evidence() -> dict[str, Any]:
    return {
        "evidence_id": "e-1",
        "source_id": "source-1",
        "url": "https://example.org/synthetic",
        "retrieved_at": "2026-11-08T10:00:00Z",
        "observed_at": None,
        "section": "synthetic_only",
    }


def proposal() -> dict[str, Any]:
    return {
        "schema_version": "2",
        "scenario_id": "scenario-1",
        "baseline_analysis_id": "analysis-1",
        "template_id": "coordinate-transfer-v1",
        "operation_type": "coordinate_transfer",
        "fixed_parameters": {
            "service_ids": ["service-1"],
            "shift_seconds": 300,
            "demand_evidence": [],
            "resource_evidence": [],
            "turnaround_evidence": [],
            "resource_constraints": [],
        },
        "evidence_refs": [evidence()],
        "assumptions": ["synthetic_only"],
        "status": "proposed",
        "feasibility": "unverified",
        "versions": versions(),
        "baseline_result_hash": "base-hash",
        "editable_local_graph": True,
        "limitations": ["TURNAROUND_UNVERIFIED"],
    }


def metric(identifier: str, value: float | None = 30.0) -> dict[str, Any]:
    return {
        "metric_id": identifier,
        "name": "travel_time",
        "value": value,
        "unit": "seconds",
        "status": "available" if value is not None else "unknown",
        "reason_code": None if value is not None else "DATA_UNAVAILABLE",
        "source_refs": ["e-1"],
        "subject": {
            "kind": "connection",
            "origin_id": "node-1",
            "destination_id": "node-2",
            "mode": "rail",
        },
        "completeness": "full",
    }


def result() -> dict[str, Any]:
    return {
        "schema_version": "2",
        "run_id": "run-1",
        "scenario_id": "scenario-1",
        "baseline_analysis_id": "analysis-1",
        "status": "available",
        "before": {"metrics": [metric("before-time", 30.0)], "scores": []},
        "after": {"metrics": [metric("after-time", 35.0)], "scores": []},
        "deltas": [
            {
                "effect_id": "effect-1",
                "kind": "metric",
                "before_id": "before-time",
                "after_id": "after-time",
                "unit": "seconds",
                "delta": -5.0,
                "sign_convention": "before_minus_after",
                "outcome": "worsened",
                "reason_code": None,
                "scope": "transport",
            }
        ],
        "affected_population": 200,
        "affected_sites": None,
        "worsened_metric_ids": ["after-time"],
        "feasibility": "unverified",
        "service_changes": [
            {
                "service_id": "service-1",
                "shift_seconds": 300,
                "whole_trip": True,
                "subsequent_stops_updated": True,
            }
        ],
        "versions": versions(),
        "overlay_graph_id": "scenario-overlay-1",
        "baseline_result_hash": "base-hash",
        "result_hash": "scenario-hash",
        "params_hash": "params-hash",
        "operations_hash": "operations-hash",
        "evidence_refs": [evidence()],
        "turnaround_evidence": [],
        "assumptions": ["synthetic_only"],
        "limitations": ["TURNAROUND_UNVERIFIED"],
        "editable_local_graph": True,
    }


def test_public_run_options_cannot_contain_arbitrary_operations() -> None:
    assert dto().ScenarioRunOptions.model_validate({"schema_version": "2"}).schema_version == "2"
    with pytest.raises(ValidationError):
        dto().ScenarioRunOptions.model_validate({"schema_version": "2", "operations": []})


def test_internal_run_request_keeps_path_and_immutable_baseline_identity() -> None:
    parsed = dto().ScenarioRunRequest(
        schema_version="2", scenario_id="scenario-1", baseline_analysis_id="analysis-1"
    )
    assert parsed.baseline_analysis_id == "analysis-1"


def test_unverified_proposal_has_no_numerical_effect() -> None:
    parsed = dto().ScenarioProposal.model_validate(proposal())
    assert parsed.status == "proposed"
    assert "delta" not in parsed.model_dump()
    with pytest.raises(ValidationError):
        dto().ScenarioProposal.model_validate(proposal() | {"delta": 20})


@pytest.mark.parametrize(
    "change", [{"feasibility": "verified"}, {"limitations": []}, {"editable_local_graph": "true"}]
)
def test_missing_turnaround_cannot_claim_verified_implementation(change: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        dto().ScenarioProposal.model_validate(proposal() | change)


def test_external_route_proposal_explains_inability_to_run() -> None:
    raw = proposal() | {"editable_local_graph": False}
    with pytest.raises(ValidationError):
        dto().ScenarioProposal.model_validate(raw)
    raw["limitations"].append("EDITABLE_GRAPH_UNAVAILABLE")
    assert dto().ScenarioProposal.model_validate(raw).editable_local_graph is False


@pytest.mark.parametrize("missing", ["demand_evidence", "resource_evidence"])
def test_additional_departure_requires_demand_and_resource_evidence(missing: str) -> None:
    raw = proposal() | {
        "operation_type": "additional_departure",
        "template_id": "additional-departure-v1",
    }
    raw["fixed_parameters"].update(demand_evidence=["e-1"], resource_evidence=["e-1"])
    raw["fixed_parameters"][missing] = []
    with pytest.raises(ValidationError):
        dto().ScenarioProposal.model_validate(raw)


def test_additional_departure_accepts_supported_need_without_asserting_turnaround() -> None:
    raw = proposal() | {"operation_type": "additional_departure"}
    raw["fixed_parameters"].update(demand_evidence=["e-1"], resource_evidence=["e-1"])
    assert dto().ScenarioProposal.model_validate(raw).feasibility == "unverified"


def test_proposal_rejects_dangling_evidence() -> None:
    raw = proposal()
    raw["fixed_parameters"]["resource_evidence"] = ["missing-evidence"]
    with pytest.raises(ValidationError):
        dto().ScenarioProposal.model_validate(raw)


def test_scenario_result_retains_negative_improvement_and_affected_people_not_flow() -> None:
    parsed = dto().ScenarioResult.model_validate(result())
    assert parsed.deltas[0].delta == -5
    assert parsed.worsened_metric_ids == ["after-time"]
    assert parsed.affected_population == 200
    assert parsed.affected_sites is None


@pytest.mark.parametrize("delta", [0.0, 5.0, float("inf")])
def test_result_rejects_fabricated_delta(delta: float) -> None:
    raw = result()
    raw["deltas"][0]["delta"] = delta
    with pytest.raises(ValidationError):
        dto().ScenarioResult.model_validate(raw)


@pytest.mark.parametrize(
    "field,value",
    [
        ("after_id", "missing-metric"),
        ("unit", "minutes"),
        ("sign_convention", "after_minus_before"),
    ],
)
def test_effect_requires_matching_values_units_and_time_sign(field: str, value: str) -> None:
    raw = result()
    raw["deltas"][0][field] = value
    with pytest.raises(ValidationError):
        dto().ScenarioResult.model_validate(raw)


def test_missing_after_result_produces_null_effect_with_reason() -> None:
    raw = result() | {"status": "partial", "worsened_metric_ids": []}
    raw["after"]["metrics"] = [metric("after-time", None)]
    raw["deltas"][0].update(delta=None, outcome="unknown", reason_code="DATA_UNAVAILABLE")
    assert dto().ScenarioResult.model_validate(raw).deltas[0].delta is None
    raw["deltas"][0]["reason_code"] = None
    with pytest.raises(ValidationError):
        dto().ScenarioResult.model_validate(raw)


def test_new_connection_is_an_event_without_infinite_time_saving() -> None:
    raw = result() | {"worsened_metric_ids": []}
    raw["before"]["metrics"] = [
        metric("before-time", None)
        | {"status": "not_applicable", "reason_code": "NO_FEASIBLE_ROUTE"}
    ]
    raw["deltas"][0].update(delta=None, outcome="new_connection", reason_code="NEW_CONNECTION")
    assert dto().ScenarioResult.model_validate(raw).deltas[0].outcome == "new_connection"


def test_unknown_old_network_cannot_be_claimed_as_a_new_connection() -> None:
    raw = result() | {"worsened_metric_ids": []}
    raw["before"]["metrics"] = [metric("before-time", None)]
    raw["deltas"][0].update(delta=None, outcome="new_connection", reason_code="NEW_CONNECTION")
    with pytest.raises(ValidationError):
        dto().ScenarioResult.model_validate(raw)


@pytest.mark.parametrize(
    "change",
    [
        {"worsened_metric_ids": []},
        {"affected_sites": 2},
        {"editable_local_graph": False},
        {"overlay_graph_id": "graph-1"},
    ],
)
def test_result_rejects_hidden_worsening_or_inconsistent_context(change: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        dto().ScenarioResult.model_validate(result() | change)


@pytest.mark.parametrize("field", ["whole_trip", "subsequent_stops_updated"])
def test_schedule_change_cannot_shift_only_one_stop(field: str) -> None:
    raw = result()
    raw["service_changes"][0][field] = False
    with pytest.raises(ValidationError):
        dto().ScenarioResult.model_validate(raw)


def test_pipeline_effect_is_explicitly_independent() -> None:
    raw = result() | {"worsened_metric_ids": []}
    for group in ("before", "after"):
        raw[group]["metrics"][0].update(
            name="pipeline_load",
            unit="percent",
            subject={
                "kind": "pipeline",
                "origin_id": "node-1",
                "destination_id": "node-2",
                "mode": "pipeline",
            },
        )
    raw["deltas"][0].update(
        unit="percent",
        delta=5.0,
        sign_convention="after_minus_before",
        outcome="changed",
        scope="pipeline",
    )
    assert dto().ScenarioResult.model_validate(raw).deltas[0].scope == "pipeline"
    raw["deltas"][0]["scope"] = "transport"
    with pytest.raises(ValidationError):
        dto().ScenarioResult.model_validate(raw)


def test_verified_proposal_needs_both_turnaround_and_resource_evidence() -> None:
    raw = proposal() | {"feasibility": "verified", "limitations": []}
    raw["fixed_parameters"].update(turnaround_evidence=["e-1"], resource_evidence=["e-1"])
    assert dto().ScenarioProposal.model_validate(raw).feasibility == "verified"
    raw["fixed_parameters"]["resource_evidence"] = []
    with pytest.raises(ValidationError):
        dto().ScenarioProposal.model_validate(raw)


def test_result_evidence_must_resolve_to_saved_references() -> None:
    raw = result()
    raw["after"]["metrics"][0]["source_refs"] = ["missing-source-record"]
    with pytest.raises(ValidationError):
        dto().ScenarioResult.model_validate(raw)


def test_score_delta_uses_after_minus_before_without_computing_an_index() -> None:
    raw = result() | {"worsened_metric_ids": []}
    score = {
        "score_id": "before-score",
        "name": "accessibility",
        "value": 50.0,
        "status": "available",
        "completeness": "full",
        "methodology_version": "a2-methodology-v3",
        "reason_code": None,
        "components": [],
        "evidence_refs": ["e-1"],
        "subject": {"kind": "territory", "territory_id": "city-1", "mode": "walk"},
        "ranking_eligible": False,
        "aggregation_basis": dict(
            population=100,
            population_method="synthetic",
            year=2026,
            coverage=1.0,
            unit="persons",
            unknown_positive_weight=False,
        ),
    }
    raw["before"] = {"metrics": [], "scores": [score]}
    raw["after"] = {"metrics": [], "scores": [score | {"score_id": "after-score", "value": 60.0}]}
    raw["deltas"][0].update(
        kind="score",
        before_id="before-score",
        after_id="after-score",
        unit="points_0_100",
        delta=10.0,
        sign_convention="after_minus_before",
        outcome="improved",
    )
    assert dto().ScenarioResult.model_validate(raw).deltas[0].delta == 10.0


def test_time_worsening_cannot_hide_behind_neutral_changed_label() -> None:
    raw = result() | {"worsened_metric_ids": []}
    raw["deltas"][0]["outcome"] = "changed"
    with pytest.raises(ValidationError):
        dto().ScenarioResult.model_validate(raw)


def test_new_connection_cannot_compare_different_pairs() -> None:
    raw = result() | {"worsened_metric_ids": []}
    raw["before"]["metrics"] = [
        metric("before-time", None)
        | {"status": "not_applicable", "reason_code": "NO_FEASIBLE_ROUTE"}
    ]
    raw["after"]["metrics"][0]["subject"]["destination_id"] = "different-node"
    raw["deltas"][0].update(delta=None, outcome="new_connection", reason_code="NEW_CONNECTION")
    with pytest.raises(ValidationError):
        dto().ScenarioResult.model_validate(raw)
