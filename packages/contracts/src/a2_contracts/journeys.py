"""Activity requests and engine results; HTTP and tools share these validators."""

from datetime import date
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from .common import (
    Assumption,
    ConstraintState,
    ContractModel,
    Count,
    Feasibility,
    Identifier,
    Mode,
    NonNegative,
    ProfileId,
    Provenance,
    RunStatus,
    Timestamp,
    Version,
    utc,
    validate_feasibility,
)


class Budget(ContractModel):
    amount: NonNegative
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]


class TripRules(ContractModel):
    max_one_way_minutes: NonNegative | None
    max_transfers: Count | None
    max_access_walk_minutes: NonNegative | None
    budget: Budget | None
    require_step_free: bool = Field(strict=True)


class ActivityRequest(TripRules):
    profile_id: ProfileId
    origin_id: Identifier
    destination_id: Identifier | None = None
    service_type_id: Identifier | None = None
    service_date: date
    earliest_departure: Timestamp
    activity_start: Timestamp | None = None
    activity_end: Timestamp | None = None
    arrival_buffer_min_minutes: NonNegative | None = None
    arrival_buffer_max_minutes: NonNegative | None = None
    return_by: Timestamp | None = None
    activity_basis: Literal["scenario", "verified"] | None = None

    @model_validator(mode="after")
    def valid_activity(self) -> Self:
        if (self.destination_id is None) == (self.service_type_id is None):
            raise ValueError("Exactly one destination_id or service_type_id is required")
        if self.profile_id == "medical" and self.service_type_id is None:
            raise ValueError("Medical requests require service_type_id")
        if self.profile_id != "medical" and self.destination_id is None:
            raise ValueError("Regular/intercity requests require destination_id")
        activity = (
            self.activity_start,
            self.activity_end,
            self.return_by,
            self.arrival_buffer_min_minutes,
            self.arrival_buffer_max_minutes,
            self.activity_basis,
        )
        if self.profile_id != "intercity" or any(x is not None for x in activity):
            if any(x is None for x in activity):
                raise ValueError(
                    "Activity, arrival buffers, basis and return_by are required together"
                )
            assert self.activity_start is not None and self.activity_end is not None
            assert self.return_by is not None
            assert self.arrival_buffer_min_minutes is not None
            assert self.arrival_buffer_max_minutes is not None
            if (
                not utc(self.earliest_departure)
                < utc(self.activity_start)
                < utc(self.activity_end)
                <= utc(self.return_by)
            ):
                raise ValueError("Expected departure < activity_start < activity_end <= return_by")
            if self.arrival_buffer_min_minutes > self.arrival_buffer_max_minutes:
                raise ValueError("Minimum arrival buffer exceeds maximum")
        return self


class JourneyRequest(ActivityRequest):
    bundle_id: Identifier
    profile_version: Version


class Leg(ContractModel):
    mode: Mode
    from_id: Identifier
    to_id: Identifier
    trip_id: Identifier | None
    departure_at: Timestamp
    arrival_at: Timestamp
    duration_seconds: NonNegative
    evidence_ids: list[Identifier]

    @model_validator(mode="after")
    def valid_duration(self) -> Self:
        if (utc(self.arrival_at) - utc(self.departure_at)).total_seconds() != self.duration_seconds:
            raise ValueError("Leg duration must equal elapsed seconds")
        if self.mode != "walk" and self.trip_id is None:
            raise ValueError("Transport leg requires trip_id")
        return self


class Journey(ContractModel):
    journey_id: Identifier
    legs: list[Leg] = Field(min_length=1)
    outbound_minutes: NonNegative
    return_minutes: NonNegative | None
    waiting_minutes: NonNegative
    transfers: Count
    total_fare: NonNegative | None
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] | None

    @model_validator(mode="after")
    def valid_chain(self) -> Self:
        if (self.total_fare is None) != (self.currency is None):
            raise ValueError("Fare and currency must be supplied together")
        for before, after in zip(self.legs, self.legs[1:]):
            if utc(before.arrival_at) > utc(after.departure_at) or before.to_id != after.from_id:
                raise ValueError("Journey legs must form a chronological connected chain")
        return self


class JourneyResult(ContractModel):
    status: RunStatus
    routes: list[Journey]
    overall_feasibility: Feasibility
    constraints: dict[Identifier, ConstraintState]
    missing_fields: list[Identifier]
    reason_codes: list[Identifier]
    evidence_ids: list[Identifier]
    assumptions: list[Assumption]
    assumption_ids: list[Identifier]
    provenance: Provenance
    optimality: Literal["proven_in_scope", "not_proven"]

    @model_validator(mode="after")
    def valid_outcome(self) -> Self:
        validate_feasibility(self.overall_feasibility, self.constraints)
        if self.status == "ok" and self.overall_feasibility == "unknown":
            raise ValueError("Unknown feasibility requires a partial or insufficient_data result")
        if self.overall_feasibility == "pass" and not self.routes:
            raise ValueError("Pass requires a confirmed route")
        if self.overall_feasibility == "fail" and not (
            self.provenance.network_complete and self.provenance.search_complete
        ):
            raise ValueError("Fail requires completed search in complete accepted scope")
        if self.status == "insufficient_data" and (
            self.routes
            or self.overall_feasibility != "unknown"
            or not self.missing_fields
            or not self.reason_codes
        ):
            raise ValueError("Insufficient data requires unknown, missing fields and a reason")
        if self.optimality == "proven_in_scope" and not (
            self.provenance.network_complete and self.provenance.search_complete
        ):
            raise ValueError("Optimality requires complete search and network scope")
        return self
