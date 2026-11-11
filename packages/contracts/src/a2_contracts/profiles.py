"""Versioned profile configuration and fixed task denominators."""

from datetime import date, time
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from .common import ContractModel, Identifier, Mode, Positive, ProfileId, Version, valid_timezone
from .journeys import ActivityRequest, TripRules


class ProfileConfig(ContractModel):
    profile_id: ProfileId
    profile_version: Version
    study_area_id: Identifier
    study_area_version: Version
    metric_kind: Literal["activity_feasibility", "time_decay"]
    start_date: date
    end_date: date
    timezone: Identifier
    start_times: list[time] = Field(min_length=1)
    destination_ids: list[Identifier]
    service_type_ids: list[Identifier]
    modes: list[Mode] = Field(min_length=1)
    rules: TripRules
    weighting: Literal["uniform", "explicit"]
    basis: Literal["scenario", "verified"]
    assumption_ids: list[Identifier]
    evidence_ids: list[Identifier]
    half_life_minutes: Positive | None
    horizon_hours: Positive | None

    _timezone = field_validator("timezone")(valid_timezone)

    @model_validator(mode="after")
    def valid_profile(self) -> Self:
        if self.end_date < self.start_date:
            raise ValueError("Profile period is reversed")
        if any(t.tzinfo is not None for t in self.start_times):
            raise ValueError("start_times are local wall times in the profile timezone")
        if self.profile_id == "intercity":
            if self.metric_kind != "time_decay" or self.half_life_minutes is None:
                raise ValueError("Intercity requires time_decay and half_life_minutes")
            if self.horizon_hours is None or self.horizon_hours > 48:
                raise ValueError("Intercity horizon must be positive and at most 48 hours")
        elif self.metric_kind != "activity_feasibility" or (
            self.half_life_minutes is not None or self.horizon_hours is not None
        ):
            raise ValueError("Activity profiles require activity_feasibility and no time decay")
        if self.profile_id == "medical":
            if not self.service_type_ids or self.destination_ids:
                raise ValueError("Medical profile selects services")
        elif not self.destination_ids or self.service_type_ids:
            raise ValueError("Regular/intercity profile selects destinations")
        if self.basis == "scenario" and not self.assumption_ids:
            raise ValueError("Scenario profile requires assumptions")
        if self.basis == "verified" and not self.evidence_ids:
            raise ValueError("Verified profile requires evidence")
        return self


class TaskCase(ActivityRequest):
    case_id: Identifier
    weight: Positive


class TaskSet(ContractModel):
    task_set_hash: Identifier
    generation_rules_hash: Identifier
    profile_id: ProfileId
    profile_version: Version
    start_date: date
    end_date: date
    cases: list[TaskCase] = Field(min_length=1)
    assumption_ids: list[Identifier]

    @model_validator(mode="after")
    def fixed_tasks(self) -> Self:
        if self.end_date < self.start_date:
            raise ValueError("Task period is reversed")
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("Duplicate case_id")
        for case in self.cases:
            if case.profile_id != self.profile_id:
                raise ValueError("Task profile differs from TaskSet")
            if not self.start_date <= case.service_date <= self.end_date:
                raise ValueError("Task date is outside the fixed period")
        return self
