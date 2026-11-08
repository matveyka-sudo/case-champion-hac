"""Structured journeys, references and independent count/return availability."""

from datetime import date
from math import isclose
from typing import Annotated, Literal, Self
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator, model_validator

from a2_contracts.common import utc, valid_timezone

from .base import Completeness, ContractModel, Identifier, NonNegative, Timestamp, TrafficMode


class LineGeometry(ContractModel):
    type: Literal["LineString"]
    coordinates: list[tuple[float, float]] = Field(min_length=2)
    evidence_refs: list[Identifier] = Field(min_length=1)

    @model_validator(mode="after")
    def geographic_coordinates(self) -> Self:
        if any(not -180 <= lon <= 180 or not -90 <= lat <= 90 for lon, lat in self.coordinates):
            raise ValueError("GeoJSON coordinates must be longitude/latitude")
        return self


class Leg(ContractModel):
    mode: Literal[
        "walk", "public_transport", "private_car", "road", "rail", "air", "water", "mixed"
    ]
    kind: Literal["walk", "ride", "wait_initial", "wait_transfer", "handling", "transfer_operation"]
    from_node_id: Identifier
    to_node_id: Identifier
    start_at: Timestamp
    end_at: Timestamp
    duration_seconds: NonNegative
    service_id: Identifier | None
    evidence_refs: list[Identifier]

    @model_validator(mode="after")
    def elapsed_time(self) -> Self:
        if not isclose(
            (utc(self.end_at) - utc(self.start_at)).total_seconds(),
            self.duration_seconds,
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            raise ValueError("Leg duration differs from elapsed UTC time")
        return self


class JourneyResult(ContractModel):
    journey_id: Identifier
    scope: Literal["urban", "intercity"]
    origin_node_id: Identifier
    destination_node_id: Identifier
    requested_departure_at: Timestamp
    departure_at: Timestamp | None
    arrival_at: Timestamp | None
    total_seconds: NonNegative | None
    initial_wait_seconds: NonNegative | None
    path_seconds: NonNegative | None
    total_wait_seconds: NonNegative | None
    legs: list[Leg]
    geometry: LineGeometry | None
    geometry_kind: Literal["actual", "schematic", "none"]
    status: Literal["available", "unknown", "not_applicable"]
    reason_code: Identifier | None
    snapshot_id: Identifier
    completeness: Completeness
    traffic_mode: TrafficMode
    time_kind: Literal["movement", "complete_chain"]
    reference_id: Identifier | None
    destination_object_id: Identifier | None
    point_id: Identifier | None = None
    evidence_refs: list[Identifier]

    @model_validator(mode="after")
    def consistent_chain(self) -> Self:
        if (self.geometry is None) != (self.geometry_kind == "none"):
            raise ValueError("Geometry must be explicitly actual, schematic or absent")
        if self.status != "available":
            if (
                not self.reason_code
                or self.legs
                or any(
                    v is not None
                    for v in (
                        self.departure_at,
                        self.arrival_at,
                        self.total_seconds,
                        self.path_seconds,
                        self.initial_wait_seconds,
                        self.total_wait_seconds,
                    )
                )
            ):
                raise ValueError("Unavailable journey requires a reason and no invented chain")
            return self
        if not self.legs or any(
            v is None
            for v in (
                self.departure_at,
                self.arrival_at,
                self.total_seconds,
                self.initial_wait_seconds,
                self.total_wait_seconds,
            )
        ):
            raise ValueError("Available journey requires its complete timed chain")
        assert self.total_seconds is not None and self.initial_wait_seconds is not None
        assert (
            self.total_wait_seconds is not None
            and self.departure_at is not None
            and self.arrival_at is not None
        )
        if utc(self.legs[0].start_at) != utc(self.requested_departure_at):
            raise ValueError(
                "Journey chain must begin at the requested time, including initial waiting"
            )
        if utc(self.departure_at) < utc(self.requested_departure_at):
            raise ValueError("Actual departure cannot precede requested departure")
        if self.scope == "urban" and utc(self.departure_at) != utc(self.requested_departure_at):
            raise ValueError("Urban chain departs from the selected point at requested time")
        if (
            self.legs[0].from_node_id != self.origin_node_id
            or self.legs[-1].to_node_id != self.destination_node_id
        ):
            raise ValueError("Journey boundary nodes differ from its chain")
        for a, b in zip(self.legs, self.legs[1:]):
            if utc(a.end_at) != utc(b.start_at) or a.to_node_id != b.from_node_id:
                raise ValueError("Journey intervals must connect without gaps or double counting")
        summed = sum(leg.duration_seconds for leg in self.legs)
        wait = sum(
            leg.duration_seconds
            for leg in self.legs
            if leg.kind in ("wait_initial", "wait_transfer")
        )
        initial = sum(leg.duration_seconds for leg in self.legs if leg.kind == "wait_initial")
        for actual, expected in (
            (self.total_seconds, summed),
            (self.total_wait_seconds, wait),
            (self.initial_wait_seconds, initial),
        ):
            if not isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-9):
                raise ValueError("Journey totals must count each interval once")
        if utc(self.legs[-1].end_at) != utc(self.arrival_at):
            raise ValueError("Journey arrival differs from its final leg")
        if self.scope == "intercity":
            if self.path_seconds is None or not isclose(
                self.total_seconds, self.initial_wait_seconds + self.path_seconds
            ):
                raise ValueError("Intercity total equals initial wait plus path time")
            if not isclose(
                (utc(self.arrival_at) - utc(self.departure_at)).total_seconds(), self.path_seconds
            ):
                raise ValueError("Intercity path time must exclude first waiting")
        elif self.total_wait_seconds > 14400:
            raise ValueError("Urban total waiting exceeds four hours")
        return self


class BoundaryNodeSets(ContractModel):
    origin_node_ids: list[Identifier] = Field(min_length=1)
    destination_node_ids: list[Identifier] = Field(min_length=1)


class ReferenceResult(ContractModel):
    reference_id: Identifier
    seconds: NonNegative | None
    status: Literal["available", "unknown", "not_applicable"]
    completeness: Completeness
    reason_code: Identifier | None
    profile_version: Literal["edge-calibrated-v1"]
    graph_version: Identifier
    boundary_node_sets: BoundaryNodeSets
    edges: list[Identifier]
    necessary_operations: list[Identifier]
    calibration_evidence: list[Identifier]

    @model_validator(mode="after")
    def known_reference(self) -> Self:
        if (self.status == "available") != (self.seconds is not None):
            raise ValueError("Only an available reference has a duration")
        if self.status != "available" and not self.reason_code:
            raise ValueError("Unknown reference requires a reason")
        return self


class CountResult(ContractModel):
    metric_id: Identifier
    threshold_seconds: NonNegative | None
    value: Annotated[int, Field(strict=True, ge=0)] | None
    status: Literal["available", "unknown", "not_applicable", "pending"]
    reason_code: Identifier | None
    dependency_job_id: Identifier | None
    evidence_refs: list[Identifier]

    @model_validator(mode="after")
    def independent_threshold(self) -> Self:
        if self.status == "available":
            if self.threshold_seconds is None or self.value is None:
                raise ValueError("Available count requires a calculated threshold and count")
        elif self.value is not None or not self.reason_code:
            raise ValueError("Unknown count cannot invent a number")
        if self.status == "pending" and self.dependency_job_id is None:
            raise ValueError("Pending threshold requires its own job reference")
        return self


class LastReturn(ContractModel):
    point_id: Identifier
    destination_object_id: Identifier | None
    journey_id: Identifier | None
    status: Literal["available", "unknown", "not_applicable"]
    reason_code: Identifier | None
    service_date: date
    timezone: Identifier
    leave_at: Timestamp | None
    first_departure_at: Timestamp | None
    arrival_at: Timestamp | None
    evidence_refs: list[Identifier]

    _timezone = field_validator("timezone")(valid_timezone)

    @model_validator(mode="after")
    def local_departure_day(self) -> Self:
        if self.status == "available":
            if self.destination_object_id is None or self.journey_id is None:
                raise ValueError("Last return requires the selected object and confirmed journey")
            if self.leave_at is None or self.first_departure_at is None or self.arrival_at is None:
                raise ValueError("Confirmed last return requires all three times")
            if not utc(self.leave_at) <= utc(self.first_departure_at) <= utc(self.arrival_at):
                raise ValueError("Last return times are reversed")
            if (
                self.first_departure_at.astimezone(ZoneInfo(self.timezone)).date()
                != self.service_date
            ):
                raise ValueError(
                    "First return departure must be on the selected local service date"
                )
        elif not self.reason_code or any(
            t is not None for t in (self.leave_at, self.first_departure_at, self.arrival_at)
        ):
            raise ValueError("Unavailable/walk-only return has a reason, not fabricated times")
        return self
