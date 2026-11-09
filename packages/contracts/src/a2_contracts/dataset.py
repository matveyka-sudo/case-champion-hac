"""Source and accepted-data contracts. Validation does not perform source QA."""

from collections.abc import Hashable, Sequence
from datetime import UTC, date, time
from typing import Annotated, Literal, Self

from pydantic import Field, HttpUrl, StrictBool, field_validator, model_validator

from .common import (
    ContractModel,
    Count,
    Identifier,
    Mode,
    NonNegative,
    Timestamp,
    valid_timezone,
)

VerificationState = Literal["verified", "absent", "unknown"]
AcceptanceState = Literal["downloaded", "parsed", "validated", "accepted", "quarantined"]


class ValidityPeriod(ContractModel):
    valid_from: date | None
    valid_to: date | None

    @model_validator(mode="after")
    def ordered_dates(self) -> Self:
        if self.valid_from is not None and self.valid_to is not None:
            if self.valid_to < self.valid_from:
                raise ValueError("valid_to precedes valid_from")
        return self


class Source(ContractModel):
    source_id: Identifier
    publisher: Identifier
    url: HttpUrl
    license_status: Literal["permitted", "restricted", "unknown"]


class Evidence(ContractModel):
    evidence_id: Identifier
    source_id: Identifier
    record_id: Identifier
    fetched_at: Timestamp
    valid_period: ValidityPeriod
    content_hash: Identifier
    verification_state: VerificationState

    @field_validator("fetched_at")
    @classmethod
    def fetched_in_utc(cls, value: Timestamp) -> Timestamp:
        return value.astimezone(UTC)


class CompletenessScope(ContractModel):
    origin_ids: list[Identifier]
    destination_ids: list[Identifier]
    modes: list[Mode]
    valid_period: ValidityPeriod


class Coverage(ContractModel):
    scope: CompletenessScope
    is_complete: bool
    missing_fields: list[Identifier]


class Origin(ContractModel):
    origin_id: Identifier
    city_id: Identifier
    anchor_id: Identifier
    lon: Annotated[float, Field(ge=-180, le=180)]
    lat: Annotated[float, Field(ge=-90, le=90)]
    timezone: Identifier
    type: Identifier
    evidence_ids: list[Identifier]

    _timezone = field_validator("timezone")(valid_timezone)


class Destination(ContractModel):
    destination_id: Identifier
    city_id: Identifier
    anchor_id: Identifier
    entrance_id: Identifier
    lon: Annotated[float, Field(ge=-180, le=180)]
    lat: Annotated[float, Field(ge=-90, le=90)]
    timezone: Identifier
    type: Identifier
    verification_state: VerificationState
    evidence_ids: list[Identifier]

    _timezone = field_validator("timezone")(valid_timezone)


class AvailabilityWindow(ContractModel):
    """Recurring local opening window, distinct from a passenger's activity window.

    Weekdays refer to the start date (Monday=0); end_day_offset=1 explicitly
    places the end on the following date. UTC conversion belongs to the engine.
    """

    start: time
    end: time
    end_day_offset: Annotated[int, Field(strict=True, ge=0, le=1)]
    weekdays: Annotated[list[Annotated[int, Field(strict=True, ge=0, le=6)]], Field(min_length=1)]
    valid_period: ValidityPeriod
    timezone: Identifier
    verification_state: VerificationState
    evidence_ids: list[Identifier]

    _timezone = field_validator("timezone")(valid_timezone)

    @model_validator(mode="after")
    def explicit_local_window(self) -> Self:
        if self.start.tzinfo is not None or self.end.tzinfo is not None:
            raise ValueError("opening times must be local wall times with a separate IANA timezone")
        if self.end_day_offset == 0 and self.end <= self.start:
            raise ValueError("same-day opening window must end after its start")
        if len(set(self.weekdays)) != len(self.weekdays):
            raise ValueError("opening weekdays must be unique")
        if self.verification_state == "verified" and not self.evidence_ids:
            raise ValueError("verified opening window requires evidence")
        return self


class Applicability(ContractModel):
    """Known applicability facts; None means unverified, not unrestricted access."""

    passenger_categories: list[Identifier] | None
    requires_referral: StrictBool | None
    restrictions: list[Identifier]
    evidence_ids: Annotated[list[Identifier], Field(min_length=1)]


class ServiceOffering(ValidityPeriod):
    destination_id: Identifier
    service_type_id: Identifier
    eligibility: Identifier
    service_verified: VerificationState
    hours_verified: VerificationState
    evidence_ids: list[Identifier]
    availability_windows: list[AvailabilityWindow] | None = None
    applicability: Applicability | None = None

    @model_validator(mode="after")
    def substantiated_opening_hours(self) -> Self:
        if self.hours_verified == "verified":
            if not self.availability_windows or not self.evidence_ids:
                raise ValueError("verified hours require opening windows and service evidence")
            if any(window.verification_state != "verified" for window in self.availability_windows):
                raise ValueError("verified hours require verified opening windows")
        return self


class Stop(ContractModel):
    stop_id: Identifier
    parent_station_id: Identifier | None
    evidence_ids: list[Identifier]


class Trip(ContractModel):
    trip_id: Identifier
    service_id: Identifier
    operator_id: Identifier
    mode: Mode
    evidence_ids: list[Identifier]


class StopTime(ContractModel):
    trip_id: Identifier
    stop_id: Identifier
    sequence: Count
    arrival_seconds: Count
    departure_seconds: Count
    service_id: Identifier
    pickup_allowed: bool
    dropoff_allowed: bool

    @model_validator(mode="after")
    def event_order(self) -> Self:
        if self.departure_seconds < self.arrival_seconds:
            raise ValueError("departure precedes arrival in service-day seconds")
        return self


class CalendarException(ContractModel):
    service_date: date
    operation: Literal["add", "remove"]
    evidence_ids: list[Identifier]


class Calendar(ContractModel):
    service_id: Identifier
    valid_period: ValidityPeriod
    weekdays: list[Annotated[int, Field(strict=True, ge=0, le=6)]]
    exceptions: list[CalendarException]
    timezone: Identifier
    evidence_ids: list[Identifier]

    _timezone = field_validator("timezone")(valid_timezone)

    @model_validator(mode="after")
    def unique_calendar_rules(self) -> Self:
        if len(set(self.weekdays)) != len(self.weekdays):
            raise ValueError("weekday rules must be unique; Monday is 0")
        dates = [exception.service_date for exception in self.exceptions]
        if len(set(dates)) != len(dates):
            raise ValueError("calendar exceptions must have unique service dates")
        return self


class Transfer(ContractModel):
    from_id: Identifier
    to_id: Identifier
    duration_seconds: NonNegative
    access_state: VerificationState
    basis: Literal["scenario", "verified"]
    kind: Literal["access", "transfer", "egress"]
    restrictions: list[Identifier]
    evidence_ids: list[Identifier]


class Fare(ValidityPeriod):
    fare_id: Identifier
    amount: NonNegative
    currency: Annotated[str, Field(pattern="^[A-Z]{3}$")]
    passenger_category: Identifier
    trip_ids: list[Identifier]
    evidence_ids: list[Identifier]


class SnapshotBundle(ContractModel):
    bundle_id: Identifier
    timetable_snapshot: Identifier
    destinations_snapshot: Identifier
    fares_snapshot: Identifier | None
    transfer_snapshot: Identifier
    hashes: dict[Identifier, Identifier]
    parser_version: Identifier
    acceptance_state: AcceptanceState
    coverage: Coverage
    completeness_scope: CompletenessScope
    completeness_evidence: list[Identifier]
    missing_fields: list[Identifier]
    accepted_by: Identifier | None
    accepted_at: Timestamp | None

    @model_validator(mode="after")
    def explicit_acceptance(self) -> Self:
        if self.acceptance_state == "accepted":
            if self.accepted_by is None or self.accepted_at is None:
                raise ValueError("accepted bundle requires accepted_by and accepted_at")
        if self.coverage.is_complete and not self.completeness_evidence:
            raise ValueError("complete coverage requires explicit completeness evidence")
        return self


class AcceptedData(ContractModel):
    """Loaded tables with validated keys; acceptance remains an external QA decision."""

    bundle: SnapshotBundle
    sources: list[Source]
    evidence: list[Evidence]
    origins: list[Origin]
    destinations: list[Destination]
    service_offerings: list[ServiceOffering]
    stops: list[Stop]
    trips: list[Trip]
    stop_times: list[StopTime]
    calendars: list[Calendar]
    transfers: list[Transfer]
    fares: list[Fare]

    @model_validator(mode="after")
    def accepted_references(self) -> Self:
        if self.bundle.acceptance_state != "accepted":
            raise ValueError("calculation requires an explicitly accepted bundle")

        def unique(values: Sequence[Hashable], table: str) -> None:
            if len(set(values)) != len(values):
                raise ValueError(f"duplicate key in {table}")

        for table, keys in (
            ("sources", [row.source_id for row in self.sources]),
            ("evidence", [row.evidence_id for row in self.evidence]),
            ("origins", [row.origin_id for row in self.origins]),
            ("destinations", [row.destination_id for row in self.destinations]),
            ("stops", [row.stop_id for row in self.stops]),
            ("trips", [row.trip_id for row in self.trips]),
            ("calendars", [row.service_id for row in self.calendars]),
            ("fares", [row.fare_id for row in self.fares]),
            ("stop_times", [(row.trip_id, row.sequence) for row in self.stop_times]),
            (
                "service_offerings",
                [(row.destination_id, row.service_type_id) for row in self.service_offerings],
            ),
        ):
            unique(keys, table)

        def reference(value: str, allowed: set[str], field: str) -> None:
            if value not in allowed:
                raise ValueError(f"unresolved {field}: {value}")

        source_ids = {row.source_id for row in self.sources}
        evidence_ids = {row.evidence_id for row in self.evidence}
        origin_ids = {row.origin_id for row in self.origins}
        destination_ids = {row.destination_id for row in self.destinations}
        stop_ids = {row.stop_id for row in self.stops}
        trip_ids = {row.trip_id for row in self.trips}
        service_ids = {row.service_id for row in self.calendars}
        endpoint_ids = stop_ids | origin_ids | destination_ids
        endpoint_ids |= {row.anchor_id for row in self.origins}
        endpoint_ids |= {row.anchor_id for row in self.destinations}
        endpoint_ids |= {row.entrance_id for row in self.destinations}
        for item in self.evidence:
            reference(item.source_id, source_ids, "evidence.source_id")
        evidence_links = (
            *(row.evidence_ids for row in self.origins),
            *(row.evidence_ids for row in self.destinations),
            *(row.evidence_ids for row in self.service_offerings),
            *(row.evidence_ids for row in self.stops),
            *(row.evidence_ids for row in self.trips),
            *(row.evidence_ids for row in self.calendars),
            *(row.evidence_ids for row in self.transfers),
            *(row.evidence_ids for row in self.fares),
            *(
                exception.evidence_ids
                for calendar in self.calendars
                for exception in calendar.exceptions
            ),
        )
        for links in evidence_links:
            for evidence_id in links:
                reference(evidence_id, evidence_ids, "evidence_ids")
        for evidence_id in self.bundle.completeness_evidence:
            reference(evidence_id, evidence_ids, "completeness_evidence")
        for scope in (self.bundle.coverage.scope, self.bundle.completeness_scope):
            for origin_id in scope.origin_ids:
                reference(origin_id, origin_ids, "scope.origin_ids")
            for destination_id in scope.destination_ids:
                reference(destination_id, destination_ids, "scope.destination_ids")
        for offering in self.service_offerings:
            reference(offering.destination_id, destination_ids, "service_offerings.destination_id")
            for window in offering.availability_windows or []:
                for evidence_id in window.evidence_ids:
                    reference(evidence_id, evidence_ids, "availability_windows.evidence_ids")
            if offering.applicability is not None:
                for evidence_id in offering.applicability.evidence_ids:
                    reference(evidence_id, evidence_ids, "applicability.evidence_ids")
        for stop in self.stops:
            if stop.parent_station_id is not None:
                reference(stop.parent_station_id, stop_ids, "stops.parent_station_id")
        for trip in self.trips:
            reference(trip.service_id, service_ids, "trips.service_id")
        trip_services = {trip.trip_id: trip.service_id for trip in self.trips}
        chains: dict[str, list[StopTime]] = {}
        for event in self.stop_times:
            reference(event.trip_id, trip_ids, "stop_times.trip_id")
            reference(event.stop_id, stop_ids, "stop_times.stop_id")
            reference(event.service_id, service_ids, "stop_times.service_id")
            if event.service_id != trip_services[event.trip_id]:
                raise ValueError("stop time and trip service_id differ")
            chains.setdefault(event.trip_id, []).append(event)
        for events in chains.values():
            ordered = sorted(events, key=lambda event: event.sequence)
            for previous, following in zip(ordered, ordered[1:]):
                if following.arrival_seconds < previous.departure_seconds:
                    raise ValueError("trip chain travels backwards in service-day seconds")
        for transfer in self.transfers:
            reference(transfer.from_id, endpoint_ids, "transfers.from_id")
            reference(transfer.to_id, endpoint_ids, "transfers.to_id")
        for fare in self.fares:
            for trip_id in fare.trip_ids:
                reference(trip_id, trip_ids, "fares.trip_ids")
        return self
