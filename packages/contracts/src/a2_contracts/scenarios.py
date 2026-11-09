"""Scenario commands: structural validation only; ML1 evaluates their effects."""

from datetime import date
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from .common import (
    ContractModel,
    Count,
    Identifier,
    Mode,
    NonNegative,
    ProfileId,
    Provenance,
    RunStatus,
)
from .results import ComparisonEntry


class ShiftTrip(ContractModel):
    type: Literal["shift_trip"]
    trip_id: Identifier
    service_dates: list[date] = Field(min_length=1)
    delta_seconds: Annotated[int, Field(strict=True, ge=-1800, le=1800)]


class ScenarioStopTime(ContractModel):
    stop_id: Identifier
    sequence: Count
    arrival_seconds: Count
    departure_seconds: Count
    pickup: bool = Field(strict=True)
    dropoff: bool = Field(strict=True)


class AddTrip(ContractModel):
    type: Literal["add_trip"]
    new_trip_id: Identifier
    service_dates: list[date] = Field(min_length=1)
    template_trip_id: Identifier | None = None
    stop_time_shift_seconds: Annotated[int, Field(strict=True)] | None = None
    route_id: Identifier | None = None
    service_id: Identifier | None = None
    mode: Mode | None = None
    stop_times: list[ScenarioStopTime] | None = None

    @model_validator(mode="after")
    def complete_chain_or_template(self) -> Self:
        if self.template_trip_id is not None:
            if self.stop_time_shift_seconds is None or self.new_trip_id == self.template_trip_id:
                raise ValueError("Template copy requires a shift and a new trip identity")
            if any(
                x is not None for x in (self.route_id, self.service_id, self.mode, self.stop_times)
            ):
                raise ValueError("Template and full-chain forms cannot be combined")
        else:
            if self.stop_time_shift_seconds is not None or any(
                x is None for x in (self.route_id, self.service_id, self.mode, self.stop_times)
            ):
                raise ValueError("Full add_trip requires route, service, mode and stop_times")
            assert self.stop_times is not None
            if len(self.stop_times) < 2 or self.mode == "walk":
                raise ValueError("A transit trip requires at least two stop times")
            previous: ScenarioStopTime | None = None
            for event in self.stop_times:
                if event.arrival_seconds > event.departure_seconds:
                    raise ValueError("Arrival must precede departure")
                if previous and (
                    event.sequence <= previous.sequence
                    or event.arrival_seconds < previous.departure_seconds
                ):
                    raise ValueError("Stop chain must have increasing sequence and times")
                previous = event
        return self


Operation = Annotated[ShiftTrip | AddTrip, Field(discriminator="type")]


class ScenarioRequest(ContractModel):
    base_run_ids: dict[ProfileId, Identifier]
    evaluate_profile_ids: list[ProfileId] = Field(min_length=1, max_length=3)
    operations: list[Operation] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def compatible_profiles(self) -> Self:
        if len(set(self.evaluate_profile_ids)) != len(self.evaluate_profile_ids):
            raise ValueError("Duplicate evaluated profile")
        if set(self.base_run_ids) != set(self.evaluate_profile_ids):
            raise ValueError("Base runs must match evaluated profiles")
        new_ids = [op.new_trip_id for op in self.operations if isinstance(op, AddTrip)]
        if len(set(new_ids)) != len(new_ids):
            raise ValueError("Duplicate new trip identity")
        return self


class ResourceDelta(ContractModel):
    departures: int = Field(strict=True)
    vehicle_minutes: NonNegative | None


class Comparison(ContractModel):
    run_id: Identifier
    scenario_id: Identifier
    bundle_id: Identifier
    base_run_ids: dict[ProfileId, Identifier]
    status: RunStatus
    operations: list[Operation] = Field(min_length=1, max_length=10)
    operations_hash: Identifier
    comparisons: list[ComparisonEntry]
    resource_delta: ResourceDelta
    operational_validation: Literal["not_verified"]
    provenance: list[Provenance]
    evidence_ids: list[Identifier]
    assumption_ids: list[Identifier]
    code_version: Identifier
    params_hash: Identifier
    result_hash: Identifier
    warnings: list[Identifier]

    @model_validator(mode="after")
    def valid_base_links(self) -> Self:
        if not self.comparisons:
            raise ValueError("Comparison requires per-profile results or reasons")
        profile_ids = {entry.profile_id for entry in self.comparisons}
        if profile_ids != set(self.base_run_ids):
            raise ValueError("Comparison must include every baseline profile")
        if {p.profile_id for p in self.provenance} != profile_ids:
            raise ValueError("Comparison requires provenance for every profile")
        if len(self.provenance) != len(profile_ids):
            raise ValueError("Duplicate profile provenance")
        statuses = {entry.status for entry in self.comparisons}
        expected_status = (
            "insufficient_data"
            if statuses == {"insufficient_data"}
            else "ok"
            if statuses == {"ok"}
            else "partial"
        )
        if self.status != expected_status:
            raise ValueError("Aggregate status disagrees with profile comparison results")
        origins = [(entry.profile_id, entry.origin_id) for entry in self.comparisons]
        if len(set(origins)) != len(origins):
            raise ValueError("Duplicate profile/origin comparison")
        by_profile = {p.profile_id: p for p in self.provenance}
        for entry in self.comparisons:
            if self.base_run_ids.get(entry.profile_id) != entry.base_run_id:
                raise ValueError("Comparison base run does not match the profile")
            provenance = by_profile[entry.profile_id]
            if (
                provenance.bundle_id != self.bundle_id
                or provenance.code_version != self.code_version
            ):
                raise ValueError("Comparison provenance bundle/code mismatch")
            if (
                provenance.profile_version != entry.profile_version
                or provenance.task_set_hash != entry.task_set_hash
            ):
                raise ValueError("Comparison profile/task provenance mismatch")
        return self
