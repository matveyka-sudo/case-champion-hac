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
    VersionRefs,
)
from .requests import CargoProfile, UrbanPassengerRequest


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


class AnalysisGroupItem(ContractModel):
    category: Category
    attempt: Annotated[int, Field(strict=True, ge=1, le=3)]
    status: Literal["pending", "queued", "running", "succeeded", "failed", "cancelled"]
    job_id: Identifier | None
    analysis_id: Identifier | None
    error: ErrorDetail | None = None

    @model_validator(mode="after")
    def coherent_item(self) -> Self:
        if self.job_id is None and self.status not in ("pending", "failed"):
            raise ValueError("Unadmitted group items are pending or failed")
        if self.status == "pending" and self.job_id is not None:
            raise ValueError("Pending group item already has a job")
        if self.status == "failed" and self.job_id is None and self.error is None:
            raise ValueError("Failed admission requires an error")
        if (self.status == "succeeded") != (self.analysis_id is not None):
            raise ValueError("Only successful group items reference a saved analysis")
        return self


class AnalysisGroupView(ContractModel):
    """Separate category results, not a combined index or coverage assertion."""

    group_id: Identifier
    request: UrbanPassengerRequest
    versions: VersionRefs
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    items: list[AnalysisGroupItem] = Field(min_length=1, max_length=9)
    error: ErrorDetail | None
    request_id: Identifier
    poll_after_seconds: Annotated[int, Field(strict=True, ge=1)] = 2

    @model_validator(mode="after")
    def coherent_group(self) -> Self:
        if [item.category for item in self.items] != self.request.categories:
            raise ValueError("Group categories must match the immutable request")
        if self.status == "succeeded" and any(item.status != "succeeded" for item in self.items):
            raise ValueError("Successful group requires every category result")
        return self


class BatchProgress(ContractModel):
    """Persisted admitted OD batches, not route validity or numerical coverage.

    All batches can be saved while calculation/validation/publication is still
    pending. No duration, ETA or percentage of the whole calculation is implied.
    """

    completed_batches: Annotated[int, Field(strict=True, ge=0, le=4096)]
    total_batches: Annotated[int, Field(strict=True, ge=1, le=4096)]
    phase: Literal["routing", "finalizing", "published", "stopped"]

    @model_validator(mode="after")
    def coherent_counts(self) -> Self:
        if self.completed_batches > self.total_batches:
            raise ValueError("Completed batch count exceeds its plan")
        if self.phase == "routing" and self.completed_batches == self.total_batches:
            raise ValueError("A complete batch inventory is finalizing")
        if self.phase in ("finalizing", "published") and (
            self.completed_batches != self.total_batches
        ):
            raise ValueError("Finalizing/published requires every planned batch")
        return self


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
    progress: BatchProgress | None = Field(default=None, exclude_if=lambda value: value is None)

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
        if (
            self.progress is not None
            and self.progress.phase
            not in {
                "queued": (),
                "running": ("routing", "finalizing"),
                "succeeded": ("published",),
                "failed": ("stopped",),
                "cancelled": ("stopped",),
            }[self.status]
        ):
            raise ValueError("Batch progress phase differs from job state")
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
    analysis_kinds: list[AnalysisKind] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class UrbanPassengerScope(ContractModel):
    """One registered input scope, not a promise of complete metrics or admission.

    All selections must match the same scope; unions across scopes are unsafe.
    ``whole_city`` permits an absent request area_id, not a full-city rating.
    The time interval includes valid_from and excludes valid_to.
    """

    scope_id: Identifier
    snapshot_id: Identifier
    city_id: Identifier
    passenger_modes: list[Literal["walk", "public_transport", "private_car"]] = Field(min_length=1)
    categories: list[Category] = Field(min_length=1)
    traffic_modes: list[TrafficMode] = Field(min_length=1)
    valid_from: Timestamp
    valid_to: Timestamp
    whole_city: bool = Field(strict=True)
    area_ids: list[Identifier] = Field(default_factory=list)

    @model_validator(mode="after")
    def coherent_scope(self) -> Self:
        if self.valid_from >= self.valid_to:
            raise ValueError("Runtime scope requires a nonempty validity interval")
        if not self.whole_city and not self.area_ids:
            raise ValueError("Runtime scope requires an allowed area selection")
        for values in (self.passenger_modes, self.categories, self.traffic_modes, self.area_ids):
            if len(values) != len(set(values)):
                raise ValueError("Runtime scope selections must be unique")
        return self


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
    areas: list[AreaOption] = Field(default_factory=list)
    # None: legacy registry does not publish detailed constraints; []: none registered.
    urban_passenger_scopes: list[UrbanPassengerScope] | None = None
