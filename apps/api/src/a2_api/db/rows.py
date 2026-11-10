"""Transactional PostgreSQL jobs and immutable runs; no ORM at runtime."""

import json
from collections.abc import Mapping

from a2_contracts.api import Job
from a2_contracts.common import canonical_hash
from a2_contracts.results import ProfileResult
from a2_contracts.scenarios import Comparison
from a2_contracts.v2.results import (
    AnalysisResult,
)
from a2_contracts.v2.scenarios import (
    ScenarioProposal,
    ScenarioResult,
)
from pydantic import ValidationError

from a2_api.core.errors import DomainError
from a2_api.core.jobs import (
    JobContext,
    JobContextUnion,
    V2JobContext,
)

RunResult = ProfileResult | Comparison | AnalysisResult | ScenarioResult
JOB_FIELDS = (
    "job_id",
    "status",
    "trace_id",
    "bundle_id",
    "input_hash",
    "code_version",
    "created_at",
    "started_at",
    "finished_at",
    "run_id",
    "error",
)


def json_text(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    )


def job_from_row(row: Mapping[str, object]) -> Job:
    data = {name: row[name] for name in JOB_FIELDS}
    if data["error"] is not None:
        data["error"] = json.loads(str(data["error"]))
    return Job.model_validate(data)


def _check_scope(row: Mapping[str, object], scope: str | None) -> None:
    if (row["schema_version"] == "2" and (scope is None or row["access_scope"] != scope)) or (
        row["schema_version"] != "2" and scope is not None
    ):
        raise DomainError(404, "unknown_id", "Resource not found")


def _context_scope(context: JobContextUnion) -> str | None:
    return context.access_scope if isinstance(context, V2JobContext) else None


def _context(data: dict[str, object]) -> JobContextUnion:
    model = V2JobContext if data.get("schema_version") == "2" else JobContext
    return model.model_validate(data)


def _run_from_row(row: Mapping[str, object]) -> RunResult:
    models: dict[
        str, type[ProfileResult] | type[Comparison] | type[AnalysisResult] | type[ScenarioResult]
    ] = {
        "profile": ProfileResult,
        "comparison": Comparison,
        "analysis_v2": AnalysisResult,
        "scenario_v2": ScenarioResult,
    }
    model = models[str(row["result_kind"])]
    try:
        payload = json.loads(str(row["payload"]))
        if not isinstance(payload, dict) or payload.get("result_hash") != row["result_hash"]:
            raise ValueError("Stored hash mismatch")
        unhashed = {key: value for key, value in payload.items() if key != "result_hash"}
        if canonical_hash(unhashed) != row["result_hash"]:
            raise ValueError("Stored payload hash mismatch")
        return model.model_validate_json(str(row["payload"]))
    except (ValueError, ValidationError):
        raise DomainError(
            409, "stored_result_incompatible", "Stored result requires recalculation"
        ) from None


def _proposal_from_row(row: Mapping[str, object]) -> ScenarioProposal:
    """Parse an already scoped immutable command without trusting stale hash metadata."""
    try:
        payload = json.loads(str(row["payload"]))
        if canonical_hash(payload) != row["content_hash"]:
            raise ValueError("Stored command hash mismatch")
        return ScenarioProposal.model_validate_json(str(row["payload"]))
    except (ValueError, ValidationError):
        raise DomainError(
            409, "stored_result_incompatible", "Stored result requires recalculation"
        ) from None
