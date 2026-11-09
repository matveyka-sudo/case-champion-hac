"""Deterministic result downloads, independent of storage and engine execution."""

import csv
import io
import json
from typing import Literal

from a2_contracts.results import ProfileResult
from a2_contracts.scenarios import Comparison
from pydantic import JsonValue

from .services import DomainError

Cell = str | int | float | None

CSV_FIELDS = (
    "run_id",
    "bundle_id",
    "result_kind",
    "profile_id",
    "profile_version",
    "task_set_hash",
    "code_version",
    "params_hash",
    "result_hash",
    "unit",
    "run_status",
    "status",
    "origin_id",
    "score",
    "score_lower",
    "score_upper",
    "task_count",
    "resolved_weight_share",
    "scenario_id",
    "base_run_id",
    "scenario_run_id",
    "before_lower",
    "before_upper",
    "after_lower",
    "after_upper",
    "lower_bound_change",
    "upper_bound_change",
    "delta_lower",
    "delta_upper",
    "confirmed_improved_cases",
    "confirmed_worse_cases",
    "unresolved_cases",
    "worst_worsened_case_ids",
    "reason",
    "reason_codes",
    "missing_fields",
    "evidence_ids",
    "assumption_ids",
    "warnings",
    "provenance_json",
    "operations_json",
    "resource_delta_json",
    "operational_validation",
    "parameters_json",
)


def _json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _csv_cell(value: Cell) -> str | int | float:
    if value is None:
        return ""
    if isinstance(value, str):
        # Treat a control character in leading whitespace as unsafe, even when
        # the first visible character is not a formula marker. Numeric values
        # bypass this string-only guard so negative effects stay numeric.
        for character in value:
            if character in "\t\r=+-@":
                return "'" + value
            if not character.isspace():
                break
    return value


def _rows(result: ProfileResult | Comparison, parameters_json: str) -> list[dict[str, Cell]]:
    base: dict[str, Cell] = {
        "run_id": result.run_id,
        "bundle_id": result.bundle_id,
        "code_version": result.code_version,
        "params_hash": result.params_hash,
        "result_hash": result.result_hash,
        "unit": "points_0_100",
        "run_status": result.status,
        "status": result.status,
        "evidence_ids": _json(result.evidence_ids),
        "assumption_ids": _json(result.assumption_ids),
        "warnings": _json(result.warnings),
        "parameters_json": parameters_json,
    }
    if isinstance(result, ProfileResult):
        base.update(
            result_kind="profile",
            profile_id=result.profile_id,
            profile_version=result.profile_version,
            task_set_hash=result.task_set_hash,
            unit=result.unit,
            reason_codes=_json(result.reason_codes),
            missing_fields=_json(result.missing_fields),
            provenance_json=_json(result.provenance.model_dump(mode="json")),
        )
        return [base | city.model_dump(mode="json") for city in result.cities] or [base]

    base.update(
        result_kind="comparison",
        scenario_id=result.scenario_id,
        operational_validation=result.operational_validation,
        provenance_json=_json([item.model_dump(mode="json") for item in result.provenance]),
        operations_json=_json([item.model_dump(mode="json") for item in result.operations]),
        resource_delta_json=_json(result.resource_delta.model_dump(mode="json")),
    )
    rows: list[dict[str, Cell]] = []
    for entry in result.comparisons:
        row = base | {
            "profile_id": entry.profile_id,
            "profile_version": entry.profile_version,
            "task_set_hash": entry.task_set_hash,
            "base_run_id": entry.base_run_id,
            "scenario_run_id": entry.scenario_run_id,
            "origin_id": entry.origin_id,
            "status": entry.status,
            "lower_bound_change": entry.lower_bound_change,
            "upper_bound_change": entry.upper_bound_change,
            "confirmed_improved_cases": entry.confirmed_improved_cases,
            "confirmed_worse_cases": entry.confirmed_worse_cases,
            "unresolved_cases": entry.unresolved_cases,
            "worst_worsened_case_ids": _json(entry.worst_worsened_case_ids),
            "reason": entry.reason,
        }
        for name, interval in (
            ("before", entry.before),
            ("after", entry.after),
            ("delta", entry.delta_interval_conservative),
        ):
            row[f"{name}_lower"] = interval[0] if interval is not None else None
            row[f"{name}_upper"] = interval[1] if interval is not None else None
        rows.append(row)
    return rows


def export_run(
    result: ProfileResult | Comparison,
    parameters: dict[str, JsonValue],
    format: Literal["json", "csv"],
) -> tuple[str, bytes]:
    """Return media type and UTF-8 bytes without altering saved result values.

    CSV intervals are flattened into named lower/upper columns. Nested metadata
    uses canonical JSON cells; missing scalars remain empty cells. Row order is
    the saved result order, preserving any explicit origin ordering.
    """
    if not isinstance(result, (ProfileResult, Comparison)):
        raise DomainError(404, "unknown_id", "Legacy run not found")
    if format == "json":
        body = _json({"result": result.model_dump(mode="json"), "parameters": parameters})
        return "application/json", (body + "\n").encode("utf-8")
    if format != "csv":
        raise ValueError("Unsupported export format; expected json or csv")
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, lineterminator="\r\n")
    writer.writeheader()
    for row in _rows(result, _json(parameters)):
        writer.writerow({key: _csv_cell(value) for key, value in row.items()})
    return "text/csv", output.getvalue().encode("utf-8")
