"""tau / models — canonical urban implementation."""

from dataclasses import dataclass
from datetime import date, datetime, time
from fractions import Fraction
from typing import Literal

from a2_engine.shared.config import UrbanTauRules
from a2_engine.shared.results import MetricResult, finite_number
from a2_engine.urban.models import (
    UrbanContext,
    UrbanPoint,
    UrbanPopulationCoverage,
    UrbanThreshold,
    UrbanVersions,
    identifier,
    references,
    utc_timestamp,
)
from a2_engine.urban.tau.calendar import city_zone


@dataclass(frozen=True)
class UrbanTauSliceResult:
    service_date: date
    local_time: time
    departure_at: datetime
    mean_seconds: MetricResult
    reachable_weight: Fraction
    confirmed_unreachable_weight: Fraction
    unknown_weight: Fraction
    weight_denominator: Fraction  # All directed i != j weights, not only reachable ones.
    unreachable_weight_share: Fraction | None
    reasons: tuple[str, ...]
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class UrbanODScope:
    city_id: str
    passenger_mode: Literal["walk", "public_transport", "private_car"]
    versions: UrbanVersions
    traffic_mode: Literal["static", "typical", "live", "historical"]
    search_parameters_id: str
    timezone: str
    point_ids: frozenset[str]
    max_total_wait_seconds: float

    def __post_init__(self) -> None:
        identifier(self.city_id, "city_id")
        identifier(self.search_parameters_id, "search_parameters_id")
        if self.passenger_mode not in ("walk", "public_transport", "private_car"):
            raise ValueError("Unsupported OD passenger mode")
        if self.traffic_mode not in ("static", "typical", "live", "historical"):
            raise ValueError("Unsupported OD traffic mode")
        if not isinstance(self.versions, UrbanVersions):
            raise ValueError("UrbanVersions are required")
        city_zone(self.timezone)
        object.__setattr__(self, "point_ids", frozenset(references(self.point_ids)))
        object.__setattr__(
            self,
            "max_total_wait_seconds",
            finite_number(self.max_total_wait_seconds, "max_total_wait_seconds"),
        )


@dataclass(frozen=True)
class UrbanODPair:
    origin_point_id: str
    destination_point_id: str
    state: Literal["reachable", "confirmed_unreachable", "unknown"]
    seconds: float | None
    search_complete: bool
    rules_confirmed: bool
    evidence_refs: tuple[str, ...]
    reason_code: str | None = None

    def __post_init__(self) -> None:
        identifier(self.origin_point_id, "origin_point_id")
        identifier(self.destination_point_id, "destination_point_id")
        if self.state not in ("reachable", "confirmed_unreachable", "unknown"):
            raise ValueError("Unsupported OD pair state")
        if not isinstance(self.search_complete, bool) or not isinstance(self.rules_confirmed, bool):
            raise ValueError("Pair proofs require explicit booleans")
        if self.seconds is not None:
            object.__setattr__(self, "seconds", finite_number(self.seconds, "OD seconds"))
        if self.state == "reachable" and self.seconds is None:
            raise ValueError("Reachable pair requires a supplied duration")
        if self.state == "confirmed_unreachable" and self.seconds is not None:
            raise ValueError("Unreachable pair has no numeric travel time")
        if self.reason_code is not None:
            identifier(self.reason_code, "reason_code")
        object.__setattr__(self, "evidence_refs", references(self.evidence_refs))


@dataclass(frozen=True)
class UrbanTauDay:
    service_date: date
    ordinary: bool | None
    is_holiday: bool | None
    calendar_confirmed: bool
    service_calendar_confirmed: bool
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.service_date) is not date:
            raise ValueError("service_date must be a local date")
        for name in ("ordinary", "is_holiday"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, bool):
                raise ValueError(f"{name} must be boolean or None")
        if not isinstance(self.calendar_confirmed, bool) or not isinstance(
            self.service_calendar_confirmed, bool
        ):
            raise ValueError("Calendar proofs require explicit booleans")
        object.__setattr__(self, "evidence_refs", references(self.evidence_refs))


@dataclass(frozen=True)
class UrbanTauWeekResult:
    week_id: str
    week_start: date
    scope: UrbanODScope
    tau_seconds: MetricResult
    slices: tuple[UrbanTauSliceResult, ...]
    reasons: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    @property
    def reachable_weight(self) -> Fraction | None:
        return sum((s.reachable_weight for s in self.slices), Fraction()) if self.slices else None

    @property
    def confirmed_unreachable_weight(self) -> Fraction | None:
        return (
            sum((s.confirmed_unreachable_weight for s in self.slices), Fraction())
            if self.slices
            else None
        )

    @property
    def unknown_weight(self) -> Fraction | None:
        return sum((s.unknown_weight for s in self.slices), Fraction()) if self.slices else None

    @property
    def weight_denominator(self) -> Fraction | None:
        return sum((s.weight_denominator for s in self.slices), Fraction()) if self.slices else None

    @property
    def unreachable_weight_share(self) -> Fraction | None:
        denominator = self.weight_denominator
        numerator = self.confirmed_unreachable_weight
        return numerator / denominator if denominator and numerator is not None else None


@dataclass(frozen=True)
class UrbanTauSlice:
    scope: UrbanODScope
    service_date: date
    local_time: time
    departure_at: datetime
    pairs: tuple[UrbanODPair, ...]
    coverage_confirmed: bool
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.scope, UrbanODScope) or type(self.service_date) is not date:
            raise ValueError("A slice requires OD scope and a local date")
        if (
            not isinstance(self.local_time, time)
            or self.local_time.tzinfo is not None
            or self.local_time.second
            or self.local_time.microsecond
        ):
            raise ValueError("A slice requires a naive local minute time")
        departure = utc_timestamp(self.departure_at)
        local = departure.astimezone(city_zone(self.scope.timezone))
        if local.date() != self.service_date or local.time() != self.local_time:
            raise ValueError("LOCAL_UTC_SLICE_MISMATCH")
        if not isinstance(self.coverage_confirmed, bool):
            raise ValueError("OD coverage must be explicitly confirmed")
        seen: set[tuple[str, str]] = set()
        pairs = tuple(self.pairs)
        for pair in pairs:
            if not isinstance(pair, UrbanODPair):
                raise ValueError("UrbanODPair rows are required")
            key = (pair.origin_point_id, pair.destination_point_id)
            if key in seen:
                raise ValueError("DUPLICATE_OD_PAIR")
            if not set(key) <= self.scope.point_ids:
                raise ValueError("UNKNOWN_OD_POINT_ID")
            seen.add(key)
        object.__setattr__(self, "departure_at", departure)
        object.__setattr__(self, "pairs", pairs)
        object.__setattr__(self, "evidence_refs", references(self.evidence_refs))


@dataclass(frozen=True)
class UrbanTauResult:
    baseline_context: UrbanContext
    passenger_mode: Literal["walk", "public_transport", "private_car"]
    timezone: str
    reference_at: datetime
    rules: UrbanTauRules
    points: tuple[UrbanPoint, ...]
    population_coverage: UrbanPopulationCoverage
    tau_seconds: MetricResult
    selected_week: UrbanTauWeekResult | None
    weeks: tuple[UrbanTauWeekResult, ...]
    reasons: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    @property
    def completeness(self) -> Literal["full", "partial"]:
        return "full" if self.tau_seconds.status == "available" else "partial"

    def to_threshold(self) -> UrbanThreshold:
        """No context overrides: preserve the baseline versions, even for future scenarios."""
        context = self.baseline_context
        return UrbanThreshold(
            context.city_id,
            self.passenger_mode,
            context.versions,
            context.traffic_mode,
            context.search_parameters_id,
            self.tau_seconds.value,
            self.evidence_refs,
        )


@dataclass(frozen=True)
class UrbanTauWeek:
    week_id: str
    scope: UrbanODScope
    week_start: date
    calendar: tuple[UrbanTauDay, ...]
    slices: tuple[UrbanTauSlice, ...]
    evidence_refs: tuple[str, ...]
    typical_profile_accepted: bool
    traffic_evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        identifier(self.week_id, "week_id")
        if not isinstance(self.scope, UrbanODScope) or type(self.week_start) is not date:
            raise ValueError("A week requires an OD scope and local date")
        if self.week_start.weekday() != 0:
            raise ValueError("WEEK_MUST_START_ON_MONDAY")
        if not isinstance(self.typical_profile_accepted, bool):
            raise ValueError("Typical profile confirmation must be explicit")
        calendar, slices = tuple(self.calendar), tuple(self.slices)
        if any(not isinstance(day, UrbanTauDay) for day in calendar):
            raise ValueError("UrbanTauDay calendar rows are required")
        if len({day.service_date for day in calendar}) != len(calendar):
            raise ValueError("DUPLICATE_CALENDAR_DAY")
        for item in slices:
            if not isinstance(item, UrbanTauSlice) or item.scope != self.scope:
                raise ValueError("MATRIX_SLICE_SCOPE_MISMATCH")
        if len({item.departure_at for item in slices}) != len(slices) or len(
            {(item.service_date, item.local_time) for item in slices}
        ) != len(slices):
            raise ValueError("DUPLICATE_SLICE_TIMESTAMP")
        object.__setattr__(self, "calendar", calendar)
        object.__setattr__(self, "slices", slices)
        object.__setattr__(self, "evidence_refs", references(self.evidence_refs))
        object.__setattr__(self, "traffic_evidence_refs", references(self.traffic_evidence_refs))
