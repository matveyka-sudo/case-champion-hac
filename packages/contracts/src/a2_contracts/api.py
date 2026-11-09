"""HTTP-independent API payloads and job state invariants."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from .common import ContractModel, Identifier, ProfileId, Timestamp, Version

ErrorCode = Literal[
    "invalid_request",
    "unknown_id",
    "incompatible_versions",
    "idempotency_conflict",
    "budget_exceeded",
    "engine_unavailable",
    "internal_error",
    "method_not_allowed",
    "job_timeout",
    "job_cancelled",
    "data_unavailable",
    "unsupported_traffic_mode",
    "provider_unavailable",
    "unsupported_scenario",
    "stored_result_incompatible",
]
JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


class FieldError(ContractModel):
    location: list[str]
    code: Identifier
    message: Identifier


class ErrorDetail(ContractModel):
    code: ErrorCode
    message: Identifier
    field_errors: list[FieldError]
    retryable: bool = Field(strict=True)
    trace_id: Identifier


class ErrorEnvelope(ContractModel):
    error: ErrorDetail


class AnalysisRequest(ContractModel):
    bundle_id: Identifier
    profile_id: ProfileId
    profile_version: Version
    task_set_hash: Identifier
    origin_ids: list[Identifier] = Field(min_length=1)
    include_case_results: bool = Field(strict=True)

    @model_validator(mode="after")
    def unique_origins(self) -> Self:
        if len(set(self.origin_ids)) != len(self.origin_ids):
            raise ValueError("Duplicate origin IDs")
        return self


class JobAccepted(ContractModel):
    job_id: Identifier
    status: JobStatus


class Job(ContractModel):
    job_id: Identifier
    status: JobStatus
    trace_id: Identifier
    bundle_id: Identifier
    input_hash: Identifier
    code_version: Identifier
    created_at: Timestamp
    started_at: Timestamp | None
    finished_at: Timestamp | None
    run_id: Identifier | None
    error: ErrorDetail | None

    @model_validator(mode="after")
    def consistent_state(self) -> Self:
        if self.started_at is not None and self.started_at < self.created_at:
            raise ValueError("Job started before creation")
        if self.finished_at is not None and self.finished_at < (self.started_at or self.created_at):
            raise ValueError("Job finished before it started")
        if self.status == "queued" and (
            self.started_at is not None or self.finished_at is not None
        ):
            raise ValueError("Queued job cannot have execution timestamps")
        if self.status == "running" and (self.started_at is None or self.finished_at is not None):
            raise ValueError("Running job requires started_at, no finished_at")
        if self.status in ("succeeded", "failed", "cancelled") and self.finished_at is None:
            raise ValueError("Terminal job requires finished_at")
        if self.status == "succeeded":
            if self.run_id is None or self.error is not None or self.started_at is None:
                raise ValueError("Successful job requires run_id and no error")
        elif self.run_id is not None:
            raise ValueError("Only a successful job may reference a published run")
        if (self.status == "failed") != (self.error is not None):
            raise ValueError("Only failed jobs have an error, and must have one")
        return self


class Place(ContractModel):
    place_id: Identifier
    name: Identifier
    kind: Literal["city", "municipality", "terminal", "stop"]
    region_id: Identifier
    lon: Annotated[float, Field(ge=-180, le=180)]
    lat: Annotated[float, Field(ge=-90, le=90)]


class Health(ContractModel):
    status: Literal["ok"]
