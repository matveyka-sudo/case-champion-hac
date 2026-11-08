"""Production API envelope and capability DTOs; no transport implementation."""

from datetime import date
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from a2_contracts.api import FieldError

from .base import (
    AnalysisKind,
    Category,
    ContractModel,
    Identifier,
    MethodologyRef,
    PipelineProduct,
    TerritoryLevel,
    Timestamp,
    TrafficMode,
)
from .requests import CargoProfile


class ErrorDetail(ContractModel):
    code: Identifier
    message: Identifier
    field_errors: list[FieldError]
    request_id: Identifier
    retryable: bool = Field(strict=True)


class ErrorEnvelope(ContractModel):
    error: ErrorDetail


class JobAccepted(ContractModel):
    job_id: Identifier
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    request_id: Identifier
    poll_after_seconds: Annotated[int, Field(strict=True, ge=1)] = 2


class JobView(ContractModel):
    job_id: Identifier
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    request_id: Identifier
    created_at: Timestamp
    started_at: Timestamp | None
    finished_at: Timestamp | None
    analysis_id: Identifier | None
    scenario_run_id: Identifier | None
    error: ErrorDetail | None
    poll_after_seconds: Annotated[int, Field(strict=True, ge=1)] = 2

    @model_validator(mode="after")
    def coherent_state(self) -> Self:
        terminal = self.status in ("succeeded", "failed", "cancelled")
        if terminal != (self.finished_at is not None):
            raise ValueError("Terminal job requires finish time")
        outputs = int(self.analysis_id is not None) + int(self.scenario_run_id is not None)
        if self.status == "succeeded" and (
            outputs != 1 or self.error is not None or self.started_at is None
        ):
            raise ValueError("Succeeded job requires exactly one result and no error")
        if self.status != "succeeded" and outputs:
            raise ValueError("Only succeeded job publishes a result")
        if (self.status == "failed") != (self.error is not None):
            raise ValueError("Failed job requires error")
        if self.status == "running" and self.started_at is None:
            raise ValueError("Running job requires start time")
        if self.status == "queued" and self.started_at is not None:
            raise ValueError("Queued job cannot be started")
        return self


class TerritoryOption(ContractModel):
    territory_id: Identifier
    level: TerritoryLevel
    name: Identifier
    timezone: Identifier
    parent_region_id: Identifier | None = None
    capital_city_id: Identifier | None = None


class AreaOption(ContractModel):
    area_id: Identifier
    city_id: Identifier
    name: Identifier


class KindCapability(ContractModel):
    analysis_kind: AnalysisKind
    contract_available: bool = Field(strict=True)
    calculation_available: bool = Field(strict=True)
    reason_code: Identifier | None


class SnapshotAvailability(ContractModel):
    """Accepted snapshot metadata, not proof of full route or mode coverage."""

    snapshot_id: Identifier
    territory_ids: list[Identifier]
    valid_from: date | None
    valid_to: date | None
    graph_version: Identifier
    calendar_version: Identifier


class Capabilities(ContractModel):
    schema_version: Literal["2"]
    spec_version: Literal["2026-11-08.1"]
    methodology: MethodologyRef
    analyses: list[KindCapability]
    urban_city_names: list[Identifier]
    categories: list[Category]
    traffic_modes: list[TrafficMode]
    default_traffic_mode: TrafficMode
    pipeline_products: list[PipelineProduct]
    territory_catalog_available: bool = Field(strict=True)
    national_catalog_complete: bool = Field(strict=True)
    cargo_profiles: list[CargoProfile]
    limitations: list[Identifier]
    snapshots: list[SnapshotAvailability] = Field(default_factory=list)
