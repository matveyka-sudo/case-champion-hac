"""Exact OD-weighted slice means, then an equal mean of the baseline slices."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from fractions import Fraction
from math import isfinite
from typing import Literal
from zoneinfo import ZoneInfo

from a2_engine.shared.config import UrbanTauRules
from a2_engine.shared.results import MetricResult
from a2_engine.urban.analysis import _check_points
from a2_engine.urban.models import (
    UrbanContext,
    UrbanPoint,
    UrbanPopulationCoverage,
    UrbanThreshold,
    references,
    utc_timestamp,
)
from a2_engine.urban.tau_models import UrbanODScope, UrbanTauSlice, UrbanTauWeek, city_zone


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


def _as_seconds(value: Fraction) -> MetricResult:
    try:
        seconds = float(value)
    except OverflowError:
        return MetricResult(None, "unknown", "NUMERIC_OVERFLOW")
    if not isfinite(seconds):
        return MetricResult(None, "unknown", "NUMERIC_OVERFLOW")
    if value > 0 and seconds == 0:
        return MetricResult(None, "unknown", "NUMERIC_UNDERFLOW")
    return MetricResult(seconds, "available")


def _local_instant(day: date, clock: time, zone: ZoneInfo) -> datetime:
    naive = datetime.combine(day, clock)
    alternatives = [naive.replace(tzinfo=zone, fold=fold).astimezone(UTC) for fold in (0, 1)]
    valid = {
        instant
        for instant in alternatives
        if instant.astimezone(zone).replace(tzinfo=None) == naive
    }
    if not valid:
        raise ValueError("NONEXISTENT_LOCAL_SLICE_TIME")
    if len(valid) != 1:
        raise ValueError("AMBIGUOUS_LOCAL_SLICE_TIME")
    return next(iter(valid))


def _slice_result(
    service_date: date,
    clock: time,
    departure: datetime,
    matrix: UrbanTauSlice | None,
    populations: Mapping[str, Fraction],
    denominator: Fraction,
) -> tuple[UrbanTauSliceResult, Fraction | None]:
    reachable, unreachable, unknown, numerator = Fraction(), Fraction(), Fraction(), Fraction()
    reasons: set[str] = set()
    evidence: list[str] = []
    if matrix is None:
        unknown = denominator
        reasons.add("MISSING_MATRIX_SLICE")
    else:
        evidence.extend(matrix.evidence_refs)
        if not matrix.coverage_confirmed or not matrix.evidence_refs:
            reasons.add("MATRIX_COVERAGE_UNCONFIRMED")
        pairs = {(pair.origin_point_id, pair.destination_point_id): pair for pair in matrix.pairs}
        for origin, origin_population in populations.items():
            for destination, destination_population in populations.items():
                if origin == destination:
                    continue
                weight = origin_population * destination_population
                pair = pairs.get((origin, destination))
                if pair is None:
                    unknown += weight
                    reasons.add("MISSING_OD_PAIR")
                    continue
                evidence.extend(pair.evidence_refs)
                proven = pair.search_complete and pair.rules_confirmed and bool(pair.evidence_refs)
                if proven and pair.state == "reachable":
                    assert pair.seconds is not None
                    reachable += weight
                    numerator += weight * Fraction(pair.seconds)
                elif proven and pair.state == "confirmed_unreachable":
                    unreachable += weight
                else:
                    unknown += weight
                    reasons.add(
                        pair.reason_code
                        or (
                            "UNREACHABILITY_UNCONFIRMED"
                            if pair.state == "confirmed_unreachable"
                            else "PAIR_MINIMUM_UNCONFIRMED"
                            if pair.state == "reachable"
                            else "PAIR_UNKNOWN"
                        )
                    )
    if unknown:
        reasons.add("UNKNOWN_POSITIVE_OD_WEIGHT")
    if not reachable:
        reasons.add("ZERO_REACHABLE_WEIGHT")
    exact_mean = numerator / reachable if not reasons else None
    mean = (
        _as_seconds(exact_mean)
        if exact_mean is not None
        else MetricResult(None, "unknown", sorted(reasons)[0])
    )
    if mean.status == "unknown" and mean.reason_code is not None:
        reasons.add(mean.reason_code)
    return UrbanTauSliceResult(
        service_date,
        clock,
        departure,
        mean,
        reachable,
        unreachable,
        unknown,
        denominator,
        unreachable / denominator if denominator else None,
        tuple(sorted(reasons)),
        references(evidence),
    ), exact_mean


def _week_result(
    week: UrbanTauWeek,
    expected_scope: UrbanODScope,
    monday: date,
    rules: UrbanTauRules,
    populations: Mapping[str, Fraction] | None,
    global_reasons: set[str],
) -> UrbanTauWeekResult:
    reasons = set(global_reasons)
    evidence = list(week.evidence_refs) + list(week.traffic_evidence_refs)
    for day in week.calendar:
        evidence.extend(day.evidence_refs)
    if week.scope != expected_scope:
        reasons.add("MATRIX_SCOPE_MISMATCH")
    if week.week_start >= monday:
        reasons.add("UNFINISHED_BASELINE_WEEK")
    if week.week_start < monday - timedelta(weeks=rules.max_lookback_weeks):
        reasons.add("BASELINE_WEEK_TOO_OLD")
    expected_days = {week.week_start + timedelta(days=offset) for offset in range(7)}
    if {day.service_date for day in week.calendar} != expected_days:
        reasons.add("INCOMPLETE_WEEK_CALENDAR")
    if not week.evidence_refs:
        reasons.add("WEEK_COVERAGE_UNCONFIRMED")
    for day in week.calendar:
        if (
            not day.calendar_confirmed
            or not day.evidence_refs
            or day.ordinary is None
            or day.is_holiday is None
        ):
            reasons.add("CALENDAR_UNCONFIRMED")
        if day.ordinary is False:
            reasons.add("NONORDINARY_BASELINE_WEEK")
        if day.is_holiday is True:
            reasons.add("HOLIDAY_BASELINE_WEEK")
        if (
            expected_scope.passenger_mode == "public_transport"
            and not day.service_calendar_confirmed
        ):
            reasons.add("SERVICE_CALENDAR_UNCONFIRMED")
    if expected_scope.traffic_mode == "typical" and (
        not week.typical_profile_accepted or not week.traffic_evidence_refs
    ):
        reasons.add("TYPICAL_PROFILE_UNCONFIRMED")
    if reasons:
        return UrbanTauWeekResult(
            week.week_id,
            week.week_start,
            week.scope,
            MetricResult(None, "unknown", sorted(reasons)[0]),
            (),
            tuple(sorted(reasons)),
            references(evidence),
        )
    assert populations is not None
    zone = city_zone(expected_scope.timezone)
    try:
        slots = [
            (
                week.week_start + timedelta(days=offset),
                clock,
                _local_instant(week.week_start + timedelta(days=offset), clock, zone),
            )
            for offset in range(rules.tau_days)
            for clock in rules.tau_times
        ]
    except ValueError as exc:
        reason = str(exc)
        return UrbanTauWeekResult(
            week.week_id,
            week.week_start,
            week.scope,
            MetricResult(None, "unknown", reason),
            (),
            (reason,),
            references(evidence),
        )
    supplied = {(s.service_date, s.local_time): s for s in week.slices}
    if set(supplied) - {(day, clock) for day, clock, _ in slots}:
        reasons.add("UNEXPECTED_MATRIX_SLICE")
    denominator = sum(populations.values(), Fraction()) ** 2 - sum(
        (population**2 for population in populations.values()), Fraction()
    )
    slice_results = []
    exact_means = []
    for slot_day, clock, departure in slots:
        matrix = supplied.get((slot_day, clock))
        if matrix is not None and matrix.departure_at != departure:
            raise ValueError("LOCAL_UTC_SLICE_MISMATCH")
        item, exact = _slice_result(slot_day, clock, departure, matrix, populations, denominator)
        slice_results.append(item)
        reasons.update(item.reasons)
        evidence.extend(item.evidence_refs)
        if exact is not None:
            exact_means.append(exact)
    tau = (
        _as_seconds(sum(exact_means, Fraction()) / len(slots))
        if not reasons
        else MetricResult(None, "unknown", sorted(reasons)[0])
    )
    if tau.reason_code is not None:
        reasons.add(tau.reason_code)
    return UrbanTauWeekResult(
        week.week_id,
        week.week_start,
        week.scope,
        tau,
        tuple(slice_results),
        tuple(sorted(reasons)),
        references(evidence),
    )


def calculate_urban_tau(
    baseline_context: UrbanContext,
    points: Sequence[UrbanPoint],
    population_coverage: UrbanPopulationCoverage,
    weeks: Sequence[UrbanTauWeek],
    *,
    passenger_mode: Literal["walk", "public_transport", "private_car"],
    timezone: str,
    reference_at: datetime,
    rules: UrbanTauRules,
) -> UrbanTauResult:
    """Choose the nearest usable one of the preceding completed ISO weeks.

    Population products and weighted means use exact fractions of supplied floats.
    Only reported seconds are converted to float; neither products nor sums overflow.
    """
    reference = utc_timestamp(reference_at)
    zone = city_zone(timezone)
    if baseline_context.territory_id != baseline_context.city_id:
        raise ValueError("TAU_REQUIRES_CITY_SCOPE")
    if baseline_context.versions.methodology_version != rules.methodology_version:
        raise ValueError("TAU_METHODOLOGY_MISMATCH")
    if population_coverage.context != baseline_context:
        raise ValueError("POPULATION_COVERAGE_CONTEXT_MISMATCH")
    point_map = _check_points(points, baseline_context)
    expected_scope = UrbanODScope(
        baseline_context.city_id,
        passenger_mode,
        baseline_context.versions,
        baseline_context.traffic_mode,
        baseline_context.search_parameters_id,
        timezone,
        frozenset(point_map),
        rules.max_total_wait_seconds,
    )
    global_reasons: set[str] = set()
    if (
        not population_coverage.complete
        or population_coverage.excluded_population != 0
        or population_coverage.point_ids != frozenset(point_map)
    ):
        global_reasons.add("INCOMPLETE_POPULATION_COVERAGE")
    if baseline_context.versions.population_version is None:
        global_reasons.add("POPULATION_VERSION_UNAVAILABLE")
    if any(point.population is None for point in point_map.values()):
        global_reasons.add("POPULATION_UNAVAILABLE")
    populations = (
        None
        if global_reasons
        else {
            key: Fraction(point.population)
            for key, point in sorted(point_map.items())
            if point.population is not None and point.population > 0
        }
    )
    ordered = sorted(weeks, key=lambda week: (week.week_start, week.week_id), reverse=True)
    if len({week.week_id for week in ordered}) != len(ordered) or len(
        {week.week_start for week in ordered}
    ) != len(ordered):
        raise ValueError("DUPLICATE_BASELINE_WEEK")
    local_date = reference.astimezone(zone).date()
    monday = local_date - timedelta(days=local_date.weekday())
    results = tuple(
        _week_result(week, expected_scope, monday, rules, populations, global_reasons)
        for week in ordered
    )
    selected = next((week for week in results if week.tau_seconds.status == "available"), None)
    reasons = set(global_reasons)
    if selected is None:
        reasons.add("NO_SUITABLE_BASELINE_WEEK")
        for week in results:
            reasons.update(week.reasons)
    evidence = list(population_coverage.evidence_refs)
    for point in point_map.values():
        evidence.extend(point.source_refs)
    if selected is not None:
        evidence.extend(selected.evidence_refs)
    else:
        for week in results:
            evidence.extend(week.evidence_refs)
    return UrbanTauResult(
        baseline_context,
        passenger_mode,
        timezone,
        reference,
        rules,
        tuple(sorted(point_map.values(), key=lambda point: point.point_id)),
        population_coverage,
        selected.tau_seconds
        if selected is not None
        else MetricResult(None, "unknown", "NO_SUITABLE_BASELINE_WEEK"),
        selected,
        results,
        tuple(sorted(reasons)),
        references(evidence),
    )
