"""Shared wire primitives; no dependency on HTTP, persistence, or calculation."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Version = Annotated[int, Field(strict=True, gt=0)]
Timestamp = AwareDatetime
NonNegative = Annotated[float, Field(ge=0)]
Positive = Annotated[float, Field(gt=0)]
Count = Annotated[int, Field(strict=True, ge=0)]
ProfileId = Literal["regular", "medical", "intercity"]
RunStatus = Literal["ok", "partial", "insufficient_data"]
Feasibility = Literal["pass", "fail", "unknown"]
ConstraintState = Literal["pass", "fail", "unknown", "not_requested"]
Mode = Literal["rail", "bus", "walk"]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, validate_default=True)


class Provenance(ContractModel):
    bundle_id: Identifier
    profile_id: ProfileId
    profile_version: Version
    code_version: Identifier
    task_set_hash: Identifier | None = None
    completeness_scope: Identifier
    network_complete: bool = Field(strict=True)
    search_complete: bool = Field(strict=True)


class Assumption(ContractModel):
    assumption_id: Identifier
    description: Identifier
    basis: Literal["scenario", "verified"]
    evidence_ids: list[Identifier]


def utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def validate_feasibility(overall: Feasibility, constraints: dict[str, ConstraintState]) -> None:
    active = [state for state in constraints.values() if state != "not_requested"]
    if overall == "pass" and (not active or any(state != "pass" for state in active)):
        raise ValueError("Overall pass requires all active constraints to pass")


def valid_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("Expected an IANA timezone") from exc
    return value


def canonical_hash(value: object) -> str:
    """Hash a JSON-compatible value; lists retain their semantic order."""
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
