"""Result exports retain provenance and unknowns without spreadsheet execution."""

import csv
import importlib
import importlib.util
import io
import json
from pathlib import Path
from typing import Any

import pytest
from a2_contracts.results import ProfileResult
from a2_contracts.scenarios import Comparison

ROOT = Path(__file__).resolve().parents[3]


def exporter() -> Any:
    assert importlib.util.find_spec("a2_api.run_exports") is not None, "run exports missing"
    return importlib.import_module("a2_api.run_exports").export_run


def profile(kind: str = "partial") -> ProfileResult:
    raw = json.loads((ROOT / f"packages/contracts/fixtures/{kind}.json").read_text())
    return ProfileResult.model_validate(raw["payload"])


def insufficient_profile() -> ProfileResult:
    raw = profile().model_dump(mode="json")
    raw.update(status="insufficient_data", cities=[], reason_codes=["calendar_unverified"])
    return ProfileResult.model_validate(raw)


def comparison() -> Comparison:
    baseline = profile()
    provenance = baseline.provenance.model_dump(mode="json")
    return Comparison.model_validate(
        {
            "run_id": "demo-comparison",
            "scenario_id": "demo-scenario",
            "bundle_id": baseline.bundle_id,
            "base_run_ids": {"regular": "demo-base", "medical": "demo-med-base"},
            "status": "partial",
            "operations": [
                {
                    "type": "shift_trip",
                    "trip_id": "demo-trip",
                    "service_dates": ["2026-11-09"],
                    "delta_seconds": -600,
                }
            ],
            "operations_hash": "demo-operations",
            "comparisons": [
                {
                    "profile_id": "regular",
                    "profile_version": 1,
                    "task_set_hash": baseline.task_set_hash,
                    "base_run_id": "demo-base",
                    "scenario_run_id": "demo-scenario-result",
                    "origin_id": "demo-city",
                    "status": "ok",
                    "before": [50, 50],
                    "after": [30, 30],
                    "lower_bound_change": -20,
                    "upper_bound_change": -20,
                    "delta_interval_conservative": [-20, -20],
                    "confirmed_improved_cases": 0,
                    "confirmed_worse_cases": 1,
                    "unresolved_cases": 0,
                    "worst_worsened_case_ids": ["demo-worse"],
                    "reason": None,
                },
                {
                    "profile_id": "medical",
                    "profile_version": 1,
                    "task_set_hash": "demo-med-tasks",
                    "base_run_id": "demo-med-base",
                    "scenario_run_id": None,
                    "origin_id": None,
                    "status": "insufficient_data",
                    "before": None,
                    "after": None,
                    "lower_bound_change": None,
                    "upper_bound_change": None,
                    "delta_interval_conservative": None,
                    "confirmed_improved_cases": None,
                    "confirmed_worse_cases": None,
                    "unresolved_cases": None,
                    "worst_worsened_case_ids": [],
                    "reason": "baseline_insufficient_data",
                },
            ],
            "resource_delta": {"departures": 0, "vehicle_minutes": None},
            "operational_validation": "not_verified",
            "provenance": [
                provenance,
                provenance | {"profile_id": "medical", "task_set_hash": "demo-med-tasks"},
            ],
            "evidence_ids": ["demo-evidence"],
            "assumption_ids": ["demo-synthetic"],
            "code_version": baseline.code_version,
            "params_hash": baseline.params_hash,
            "result_hash": "demo-comparison-hash",
            "warnings": ["synthetic_only"],
        }
    )


def read_csv(data: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(data.decode("utf-8"), newline="")))


def test_json_export_preserves_complete_result_and_parameters() -> None:
    result = profile()
    parameters = {"window": "условный визит", "budget": None}
    content_type, body = exporter()(result, parameters, "json")
    assert content_type == "application/json"
    assert json.loads(body) == {"result": result.model_dump(mode="json"), "parameters": parameters}
    assert "условный визит" in body.decode("utf-8")


@pytest.mark.parametrize("format", ["json", "csv"])
def test_export_is_stable_across_parameter_key_order(format: str) -> None:
    assert exporter()(profile(), {"b": 2, "a": {"y": 1, "x": 3}}, format) == exporter()(
        profile(), {"a": {"x": 3, "y": 1}, "b": 2}, format
    )


def test_profile_csv_retains_unknown_score_metadata_and_parameters() -> None:
    result = profile()
    content_type, body = exporter()(result, {"budget": None}, "csv")
    assert content_type == "text/csv"
    rows = read_csv(body)
    assert len(rows) == len(result.cities)
    assert rows[0]["score"] == ""
    assert float(rows[0]["score_lower"]) == 0
    assert float(rows[0]["score_upper"]) == 50
    for name in (
        "run_id",
        "bundle_id",
        "task_set_hash",
        "code_version",
        "params_hash",
        "result_hash",
        "unit",
        "status",
    ):
        assert rows[0][name] == getattr(result, name)
    assert rows[0]["profile_version"] == "1"
    assert json.loads(rows[0]["parameters_json"]) == {"budget": None}
    assert json.loads(rows[0]["assumption_ids"]) == result.assumption_ids


def test_insufficient_profile_still_exports_reason_row() -> None:
    rows = read_csv(exporter()(insufficient_profile(), {}, "csv")[1])
    assert len(rows) == 1
    assert rows[0]["status"] == "insufficient_data"
    assert rows[0]["origin_id"] == rows[0]["score_lower"] == rows[0]["score_upper"] == ""
    assert json.loads(rows[0]["reason_codes"]) == ["calendar_unverified"]
    assert json.loads(rows[0]["missing_fields"]) == ["return_calendar"]


def test_comparison_csv_keeps_negative_numbers_and_uncomputed_profile() -> None:
    rows = read_csv(exporter()(comparison(), {"scenario": "demo-scenario"}, "csv")[1])
    assert len(rows) == 2
    assert rows[0]["lower_bound_change"] == "-20.0"
    assert rows[0]["delta_lower"] == "-20.0"
    assert float(rows[0]["before_lower"]) == 50
    assert float(rows[0]["after_lower"]) == 30
    assert rows[1]["profile_id"] == "medical"
    assert rows[1]["reason"] == "baseline_insufficient_data"
    assert rows[1]["before_lower"] == rows[1]["delta_lower"] == ""
    assert rows[1]["status"] == "insufficient_data"
    assert rows[1]["base_run_id"] == "demo-med-base"


@pytest.mark.parametrize("identifier", ["=1+1", "+SUM(A1:A2)", "-1+1", "@SUM(A1)", "  =1+1"])
def test_csv_neutralizes_formula_identifiers(identifier: str) -> None:
    raw = profile("success").model_dump(mode="json")
    raw["run_id"] = identifier
    result = ProfileResult.model_validate(raw)
    row = read_csv(exporter()(result, {}, "csv")[1])[0]
    assert row["run_id"] == "'" + result.run_id
    assert json.loads(exporter()(result, {}, "json")[1])["result"]["run_id"] == result.run_id


def test_csv_quotes_embedded_commas_quotes_and_newlines() -> None:
    raw = profile().model_dump(mode="json")
    raw["run_id"] = 'demo,"run"\nline'
    result = ProfileResult.model_validate(raw)
    rows = read_csv(exporter()(result, {"label": 'текст,"цитата"\nстрока'}, "csv")[1])
    assert len(rows) == len(result.cities)
    assert rows[0]["run_id"] == result.run_id
    assert json.loads(rows[0]["parameters_json"])["label"] == 'текст,"цитата"\nстрока'


@pytest.mark.parametrize("identifier", ["\ttext", "\rtext", "  \ttext", "\u2003=1+1"])
def test_csv_neutralizes_leading_controls_in_supplied_model_strings(identifier: str) -> None:
    # Export remains defensive even if a caller supplies a model copied without
    # Pydantic revalidation, which would normally strip leading whitespace.
    result = profile().model_copy(update={"run_id": identifier})
    row = read_csv(exporter()(result, {}, "csv")[1])[0]
    assert row["run_id"] == "'" + identifier


@pytest.mark.parametrize("format", ["json", "csv"])
def test_nonfinite_parameters_are_rejected(format: str) -> None:
    with pytest.raises(ValueError):
        exporter()(profile(), {"bad": float("nan")}, format)


def test_unknown_export_format_is_rejected() -> None:
    with pytest.raises(ValueError):
        exporter()(profile(), {}, "xlsx")
