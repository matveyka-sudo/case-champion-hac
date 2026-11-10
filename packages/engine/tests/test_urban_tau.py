"""Synthetic OD matrices, never a claim of measured Kaliningrad travel times."""

import copy
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from fractions import Fraction
from math import isfinite
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

import pytest
from a2_engine import (
    UrbanAnalysis,
    UrbanArea,
    UrbanCandidate,
    UrbanContext,
    UrbanCoverage,
    UrbanDestination,
    UrbanLeg,
    UrbanODPair,
    UrbanODScope,
    UrbanPoint,
    UrbanPopulationCoverage,
    UrbanSearchScope,
    UrbanTauDay,
    UrbanTauResult,
    UrbanTauRules,
    UrbanTauSlice,
    UrbanTauWeek,
    UrbanVersions,
    analyze_urban_candidates,
    calculate_urban_tau,
    load_urban_config,
    load_urban_journey_rules,
    load_urban_tau_rules,
    urban_tau_rules_from_mapping,
)

LATEST = date(2026, 10, 26)
REFERENCE = datetime(2026, 11, 8, 12, tzinfo=UTC)


def pair(origin: str, destination: str, seconds: float) -> UrbanODPair:
    return UrbanODPair(
        origin, destination, "reachable", seconds, True, True, ("synthetic-pair-proof",)
    )


def week(
    scope: UrbanODScope,
    rules: UrbanTauRules,
    *,
    start: date = LATEST,
    rows: Sequence[UrbanODPair] | None = None,
) -> UrbanTauWeek:
    pairs = tuple(rows) if rows is not None else (pair("p1", "p2", 600), pair("p2", "p1", 1800))
    calendar = tuple(
        UrbanTauDay(
            start + timedelta(days=offset), True, False, True, True, ("synthetic-calendar-proof",)
        )
        for offset in range(7)
    )
    matrices = tuple(
        UrbanTauSlice(
            scope,
            start + timedelta(days=offset),
            clock,
            datetime.combine(start + timedelta(days=offset), clock, ZoneInfo(scope.timezone)),
            pairs,
            True,
            ("synthetic-matrix-proof",),
        )
        for offset in range(rules.tau_days)
        for clock in rules.tau_times
    )
    return UrbanTauWeek(
        f"synthetic-week-{start}",
        scope,
        start,
        calendar,
        matrices,
        ("synthetic-week-proof",),
        False,
        (),
    )


@dataclass
class TauCase:
    context: UrbanContext
    points: list[UrbanPoint]
    population: UrbanPopulationCoverage
    scope: UrbanODScope
    rules: UrbanTauRules
    baseline: UrbanTauWeek

    def run(self, **changes: Any) -> UrbanTauResult:
        args: dict[str, Any] = dict(
            baseline_context=self.context,
            points=self.points,
            population_coverage=self.population,
            weeks=(self.baseline,),
            passenger_mode=self.scope.passenger_mode,
            timezone=self.scope.timezone,
            reference_at=REFERENCE,
            rules=self.rules,
        )
        args.update(changes)
        return calculate_urban_tau(**args)

    def analyze(self, result: UrbanTauResult, context: UrbanContext | None = None) -> UrbanAnalysis:
        context = self.context if context is None else context
        destination = UrbanDestination(
            "school",
            "physical-school",
            frozenset({"school"}),
            "school-node",
            context.city_id,
            True,
            True,
            ("synthetic-registry",),
        )
        candidates = []
        for point, seconds in zip(self.points, (1200, 2400), strict=True):
            start, end = (
                context.requested_departure_at,
                context.requested_departure_at + timedelta(seconds=seconds),
            )
            leg = UrbanLeg(
                "walk",
                "walk",
                point.origin_node_id,
                "school-node",
                start,
                end,
                seconds,
                None,
                ("synthetic-leg",),
            )
            candidates.append(
                UrbanCandidate(
                    f"route-{point.point_id}",
                    point.point_id,
                    "school",
                    "school",
                    self.scope.passenger_mode,
                    context,
                    point.origin_node_id,
                    "school-node",
                    start,
                    end,
                    (leg,),
                    ("synthetic-route",),
                )
            )
        coverage = [
            UrbanCoverage(
                UrbanSearchScope(
                    context,
                    p.point_id,
                    "school",
                    self.scope.passenger_mode,
                    self.rules.max_total_wait_seconds,
                ),
                True,
                True,
                ("synthetic-search-proof",),
                frozenset({"school"}),
            )
            for p in self.points
        ]
        return analyze_urban_candidates(
            context,
            self.points,
            [destination],
            candidates,
            coverage,
            area=UrbanArea(
                context.city_id, 100, context.versions.geometry_version, ("synthetic-area",)
            ),
            selected_categories={"school": "medium"},
            modes=(self.scope.passenger_mode,),
            population_coverage=replace(self.population, context=context),
            config=load_urban_config(),
            rules=load_urban_journey_rules(),
            thresholds=(result.to_threshold(),),
        )


@pytest.fixture
def case() -> TauCase:
    rules = load_urban_tau_rules()
    versions = UrbanVersions(
        "synthetic-snapshot",
        "graph-v1",
        "calendar-v1",
        "population-v1",
        "geometry-v1",
        rules.methodology_version,
        "config-v1",
        "traffic-v1",
    )
    context = UrbanContext(
        "synthetic-city", "synthetic-city", REFERENCE, "static", versions, "search-v1"
    )
    points = [
        UrbanPoint(
            p,
            f"cell-{p}",
            p,
            p,
            0,
            context.city_id,
            population,
            2026,
            "synthetic-population",
            ("synthetic-population-proof",),
        )
        for p, population in [("p1", 90), ("p2", 10)]
    ]
    ids = frozenset(p.point_id for p in points)
    population = UrbanPopulationCoverage(context, True, 0, ("synthetic-population-coverage",), ids)
    scope = UrbanODScope(
        context.city_id,
        "walk",
        versions,
        "static",
        "search-v1",
        "Europe/Kaliningrad",
        ids,
        rules.max_total_wait_seconds,
    )
    return TauCase(context, points, population, scope, rules, week(scope, rules))


def three_points(
    case: TauCase, populations: tuple[float, float, float]
) -> tuple[list[UrbanPoint], UrbanODScope, UrbanPopulationCoverage]:
    points = [
        replace(
            case.points[0],
            point_id=f"p{i}",
            cell_id=f"cell-{i}",
            origin_node_id=f"p{i}",
            access_node_id=f"p{i}",
            population=p,
        )
        for i, p in enumerate(populations, 1)
    ]
    ids = frozenset(p.point_id for p in points)
    return points, replace(case.scope, point_ids=ids), replace(case.population, point_ids=ids)


def test_directed_two_point_control_and_count_integration(case: TauCase) -> None:
    result = case.run()
    assert result.tau_seconds.value == 1200
    assert result.completeness == "full" and result.selected_week is not None
    chosen = result.selected_week
    assert chosen.week_start == LATEST and len(chosen.slices) == 28
    assert all(s.mean_seconds.value == 1200 for s in chosen.slices)
    assert all(s.reachable_weight == s.weight_denominator == 1800 for s in chosen.slices)
    assert chosen.reachable_weight == 50400
    assert chosen.confirmed_unreachable_weight == chosen.unknown_weight == 0
    assert chosen.unreachable_weight_share == 0
    analysis = case.analyze(result)
    assert [p.index.value for p in analysis.modes[0].points] == [50, 25]
    assert analysis.modes[0].index.value == 47.5
    assert [p.categories[0].count.value for p in analysis.modes[0].points] == [1, 0]


def test_population_products_three_point_control(case: TauCase) -> None:
    points, scope, population = three_points(case, (1, 2, 3))
    rows = tuple(
        pair(a, b, minutes * 60)
        for a, b, minutes in [
            ("p1", "p2", 10),
            ("p2", "p1", 20),
            ("p1", "p3", 30),
            ("p3", "p1", 40),
            ("p2", "p3", 50),
            ("p3", "p2", 60),
        ]
    )
    result = case.run(
        points=points, population_coverage=population, weeks=(week(scope, case.rules, rows=rows),)
    )
    assert result.tau_seconds.value == pytest.approx((930 / 22) * 60, abs=1e-10)
    assert result.selected_week is not None
    assert result.selected_week.slices[0].reachable_weight == 22


def test_mean_of_slice_means_not_a_pooled_mean(case: TauCase) -> None:
    points = [replace(p, population=1) for p in case.points]
    first = (pair("p1", "p2", 600), pair("p2", "p1", 1200))
    second = (
        pair("p1", "p2", 3600),
        UrbanODPair(
            "p2", "p1", "confirmed_unreachable", None, True, True, ("synthetic-absence-proof",)
        ),
    )
    matrices = tuple(
        replace(s, pairs=first if index < 14 else second)
        for index, s in enumerate(case.baseline.slices)
    )
    result = case.run(points=points, weeks=(replace(case.baseline, slices=matrices),))
    assert result.tau_seconds.value == 2250
    assert result.tau_seconds.value != 1800  # Incorrect pooled-pair result.
    assert result.selected_week is not None
    assert [s.mean_seconds.value for s in result.selected_week.slices] == [900] * 14 + [3600] * 14
    assert result.selected_week.confirmed_unreachable_weight == 14
    assert result.selected_week.weight_denominator == 56
    assert result.selected_week.unreachable_weight_share == Fraction(1, 4)


@pytest.mark.parametrize("state", ["unknown", "reachable", "confirmed_unreachable"])
def test_unproven_positive_pair_is_not_excluded_as_unreachable(case: TauCase, state: str) -> None:
    unproven = UrbanODPair(
        "p2",
        "p1",
        cast(Literal["reachable", "confirmed_unreachable", "unknown"], state),
        1800 if state == "reachable" else None,
        False,
        True,
        (),
        "PROVIDER_TIMEOUT" if state == "unknown" else None,
    )
    baseline = week(case.scope, case.rules, rows=(pair("p1", "p2", 600), unproven))
    result = case.run(weeks=(baseline,))
    assert result.tau_seconds.value is None and result.selected_week is None
    diagnostic = result.weeks[0].slices[0]
    assert diagnostic.unknown_weight == 900 and diagnostic.confirmed_unreachable_weight == 0
    assert "UNKNOWN_POSITIVE_OD_WEIGHT" in diagnostic.reasons


def test_missing_pairs_and_slices_cannot_be_covered_by_a_global_flag(case: TauCase) -> None:
    missing_pair = week(case.scope, case.rules, rows=(pair("p1", "p2", 600),))
    result = case.run(weeks=(missing_pair,))
    assert "MISSING_OD_PAIR" in result.reasons and result.tau_seconds.value is None
    missing_slice = replace(case.baseline, slices=case.baseline.slices[:-1])
    result = case.run(weeks=(missing_slice,))
    assert "MISSING_MATRIX_SLICE" in result.reasons
    assert len(result.weeks[0].slices) == 28
    assert result.weeks[0].slices[-1].unknown_weight == 1800


def test_one_zero_reachable_denominator_blocks_tau(case: TauCase) -> None:
    absent = tuple(
        UrbanODPair(a, b, "confirmed_unreachable", None, True, True, ("synthetic-absence-proof",))
        for a, b in [("p1", "p2"), ("p2", "p1")]
    )
    matrices = case.baseline.slices[:-1] + (replace(case.baseline.slices[-1], pairs=absent),)
    result = case.run(weeks=(replace(case.baseline, slices=matrices),))
    assert result.tau_seconds.value is None and "ZERO_REACHABLE_WEIGHT" in result.reasons
    last = result.weeks[0].slices[-1]
    assert last.reachable_weight == 0 and last.confirmed_unreachable_weight == 1800
    assert last.unreachable_weight_share == 1


def test_zero_population_pairs_and_diagonal_need_no_routes(case: TauCase) -> None:
    points, scope, population = three_points(case, (90, 10, 0))
    rows = case.baseline.slices[0].pairs + (pair("p1", "p1", 1e200),)
    result = case.run(
        points=points, population_coverage=population, weeks=(week(scope, case.rules, rows=rows),)
    )
    assert result.tau_seconds.value == 1200
    assert result.selected_week is not None and result.selected_week.slices[0].unknown_weight == 0
    assert result.selected_week.slices[0].weight_denominator == 1800


def test_unknown_population_is_not_zero(case: TauCase) -> None:
    points = [
        case.points[0],
        replace(case.points[1], population=None, population_year=None, population_method=None),
    ]
    result = case.run(points=points)
    assert result.tau_seconds.value is None and "POPULATION_UNAVAILABLE" in result.reasons
    assert result.weeks[0].unknown_weight is None  # Unknown mass is not reported as zero.


@pytest.mark.parametrize("excluded", [277.8, None])
def test_lost_or_unknown_excluded_population_blocks_city_tau(
    case: TauCase, excluded: float | None
) -> None:
    result = case.run(population_coverage=replace(case.population, excluded_population=excluded))
    assert result.tau_seconds.value is None
    assert "INCOMPLETE_POPULATION_COVERAGE" in result.reasons


def test_population_coverage_and_roster_must_be_confirmed(case: TauCase) -> None:
    for coverage in [
        replace(case.population, complete=False),
        replace(case.population, point_ids=frozenset({"p1"})),
    ]:
        result = case.run(population_coverage=coverage)
        assert result.tau_seconds.value is None
        assert "INCOMPLETE_POPULATION_COVERAGE" in result.reasons


def test_all_zero_population_is_unknown_not_a_zero_tau(case: TauCase) -> None:
    result = case.run(points=[replace(p, population=0) for p in case.points])
    assert result.tau_seconds.value is None
    assert result.weeks[0].slices[0].weight_denominator == 0
    assert result.weeks[0].slices[0].unreachable_weight_share is None


def test_duplicate_pair_and_timestamp_are_errors(case: TauCase) -> None:
    matrix = case.baseline.slices[0]
    with pytest.raises(ValueError, match="DUPLICATE_OD_PAIR"):
        replace(matrix, pairs=matrix.pairs + matrix.pairs[:1])
    with pytest.raises(ValueError, match="DUPLICATE_SLICE_TIMESTAMP"):
        replace(case.baseline, slices=case.baseline.slices + case.baseline.slices[:1])
    with pytest.raises(ValueError, match="DUPLICATE_BASELINE_WEEK"):
        case.run(weeks=(case.baseline, case.baseline))
    with pytest.raises(ValueError, match="UNKNOWN_OD_POINT_ID"):
        replace(matrix, pairs=(pair("unknown-point", "p1", 10),))


@pytest.mark.parametrize("bad", [-1, True, "600", float("nan"), float("inf"), 10**400])
def test_invalid_matrix_and_population_numbers(case: TauCase, bad: object) -> None:
    with pytest.raises(ValueError):
        replace(case.baseline.slices[0].pairs[0], seconds=cast(float, bad))
    with pytest.raises(ValueError):
        replace(case.points[0], population=cast(float, bad))


def test_population_products_and_weighted_sums_do_not_overflow(case: TauCase) -> None:
    points = [replace(p, population=1e308) for p in case.points]
    rows = (pair("p1", "p2", 1e308), pair("p2", "p1", 1e308))
    result = case.run(points=points, weeks=(week(case.scope, case.rules, rows=rows),))
    assert result.tau_seconds.value == 1e308 and isfinite(result.tau_seconds.value)
    assert result.selected_week is not None
    assert result.selected_week.slices[0].reachable_weight > Fraction(10**600)


def test_positive_underflow_does_not_become_a_false_zero(case: TauCase) -> None:
    points, scope, population = three_points(case, (1e308, 1e-308, 1e308))
    rows = tuple(
        pair(a, b, 5e-324 if (a, b) == ("p1", "p2") else 0)
        for a in scope.point_ids
        for b in scope.point_ids
        if a != b
    )
    result = case.run(
        points=points, population_coverage=population, weeks=(week(scope, case.rules, rows=rows),)
    )
    assert result.tau_seconds.value is None and "NUMERIC_UNDERFLOW" in result.reasons


@pytest.mark.parametrize("age, available", [(8, True), (9, False), (0, False), (-1, False)])
def test_completed_iso_week_window_boundaries(case: TauCase, age: int, available: bool) -> None:
    start = date(2026, 11, 2) - timedelta(weeks=age)
    result = case.run(weeks=(week(case.scope, case.rules, start=start),))
    assert (result.tau_seconds.value is not None) is available
    if age > 8:
        assert "BASELINE_WEEK_TOO_OLD" in result.reasons
    elif age <= 0:
        assert "UNFINISHED_BASELINE_WEEK" in result.reasons


def test_week_finishes_at_local_monday_midnight(case: TauCase) -> None:
    midnight = datetime(2026, 11, 2, tzinfo=ZoneInfo(case.scope.timezone))
    assert case.run(reference_at=midnight).tau_seconds.value == 1200
    assert case.run(reference_at=midnight - timedelta(microseconds=1)).tau_seconds.value is None


@pytest.mark.parametrize(
    "defect, reason",
    [
        ("holiday", "HOLIDAY_BASELINE_WEEK"),
        ("ordinary", "NONORDINARY_BASELINE_WEEK"),
        ("calendar", "CALENDAR_UNCONFIRMED"),
        ("missing-day", "INCOMPLETE_WEEK_CALENDAR"),
        ("missing-slice", "MISSING_MATRIX_SLICE"),
    ],
)
def test_selects_earlier_usable_week_and_retains_rejection(
    case: TauCase, defect: str, reason: str
) -> None:
    latest = case.baseline
    if defect == "missing-slice":
        latest = replace(latest, slices=latest.slices[:-1])
    elif defect == "missing-day":
        latest = replace(latest, calendar=latest.calendar[:-1])
    else:
        changes: dict[str, Any] = (
            {"is_holiday": True}
            if defect == "holiday"
            else {"ordinary": False}
            if defect == "ordinary"
            else {"calendar_confirmed": False}
        )
        latest = replace(
            latest, calendar=(replace(latest.calendar[0], **changes),) + latest.calendar[1:]
        )
    earlier = week(case.scope, case.rules, start=LATEST - timedelta(weeks=1))
    result = case.run(weeks=(earlier, latest))
    assert result.tau_seconds.value == 1200 and result.selected_week is not None
    assert result.selected_week.week_start == LATEST - timedelta(weeks=1)
    assert reason in result.weeks[0].reasons


def test_public_transport_needs_confirmed_service_dates(case: TauCase) -> None:
    scope = replace(case.scope, passenger_mode="public_transport")
    baseline = week(scope, case.rules)
    baseline = replace(
        baseline,
        calendar=tuple(replace(d, service_calendar_confirmed=False) for d in baseline.calendar),
    )
    result = case.run(passenger_mode="public_transport", weeks=(baseline,))
    assert result.tau_seconds.value is None and "SERVICE_CALENDAR_UNCONFIRMED" in result.reasons


def test_typical_needs_an_accepted_profile_and_never_falls_back_to_static(case: TauCase) -> None:
    context = replace(case.context, traffic_mode="typical")
    population = replace(case.population, context=context)
    scope = replace(case.scope, traffic_mode="typical")
    baseline = week(scope, case.rules)
    result = case.run(baseline_context=context, population_coverage=population, weeks=(baseline,))
    assert result.tau_seconds.value is None and "TYPICAL_PROFILE_UNCONFIRMED" in result.reasons
    accepted = replace(
        baseline, typical_profile_accepted=True, traffic_evidence_refs=("accepted-typical-proof",)
    )
    result = case.run(baseline_context=context, population_coverage=population, weeks=(accepted,))
    assert result.tau_seconds.value == 1200 and result.to_threshold().traffic_mode == "typical"


@pytest.mark.parametrize(
    "field",
    [
        "city_id",
        "passenger_mode",
        "point_ids",
        "search_parameters_id",
        "max_total_wait_seconds",
        "snapshot_id",
        "graph_version",
        "calendar_version",
        "population_version",
        "geometry_version",
        "methodology_version",
        "config_hash",
        "traffic_version",
    ],
)
def test_matrix_scope_and_versions_cannot_be_relabelled(case: TauCase, field: str) -> None:
    if field == "city_id":
        scope = replace(case.scope, city_id="other")
    elif field == "search_parameters_id":
        scope = replace(case.scope, search_parameters_id="other")
    elif field == "passenger_mode":
        scope = replace(case.scope, passenger_mode="public_transport")
    elif field == "point_ids":
        scope = replace(case.scope, point_ids=case.scope.point_ids | {"other-point"})
    elif field == "max_total_wait_seconds":
        scope = replace(case.scope, max_total_wait_seconds=1)
    else:
        scope = replace(
            case.scope, versions=replace(case.scope.versions, **{field: "other-version"})
        )
    result = case.run(weeks=(week(scope, case.rules),))
    assert result.tau_seconds.value is None and "MATRIX_SCOPE_MISMATCH" in result.reasons


def test_bad_timezones_local_timestamps_and_mixed_slice_scopes_are_errors(case: TauCase) -> None:
    with pytest.raises(ValueError, match="INVALID_IANA_TIMEZONE"):
        case.run(timezone="not-a-timezone")
    with pytest.raises(ValueError, match="AWARE_DATETIME_REQUIRED"):
        case.run(reference_at=datetime(2026, 11, 8, 12))
    with pytest.raises(ValueError, match="LOCAL_UTC_SLICE_MISMATCH"):
        replace(
            case.baseline.slices[0],
            departure_at=case.baseline.slices[0].departure_at + timedelta(hours=1),
        )
    other = replace(
        case.baseline.slices[0],
        scope=replace(case.scope, versions=replace(case.scope.versions, graph_version="other")),
    )
    with pytest.raises(ValueError, match="MATRIX_SLICE_SCOPE_MISMATCH"):
        replace(case.baseline, slices=(other,) + case.baseline.slices[1:])


def test_unknown_tau_leaves_h_indices_available(case: TauCase) -> None:
    result = case.run(weeks=())
    assert result.tau_seconds.value is None
    analysis = case.analyze(result)
    assert analysis.modes[0].index.value == 47.5
    assert all(
        p.categories[0].count.reason_code == "TAU_UNAVAILABLE" for p in analysis.modes[0].points
    )


def test_fixed_threshold_is_reused_after_query_time_changes(case: TauCase) -> None:
    result = case.run()
    changed = replace(case.context, requested_departure_at=REFERENCE + timedelta(days=8))
    assert case.analyze(result, changed).modes[0].index.value == 47.5
    assert result.to_threshold().versions is case.context.versions
    assert result.selected_week is not None and result.selected_week.week_start == LATEST
    changed_graph = replace(
        changed, versions=replace(changed.versions, graph_version="scenario-overlay")
    )
    with pytest.raises(ValueError, match="TAU_CONTEXT_MISMATCH"):
        case.analyze(result, changed_graph)


def test_rules_load_times_and_days_from_configuration(
    case: TauCase, product: dict[str, Any]
) -> None:
    rules = load_urban_tau_rules()
    assert rules.tau_days == 7 and rules.tau_times == (time(8), time(12), time(18), time(22))
    assert rules.max_lookback_weeks == 8
    params = product["methodologies"]["urban_passenger"]["parameters"]
    params["tau_days"], params["tau_times"] = 2, ["09:00", "21:00"]
    modified = urban_tau_rules_from_mapping(product)
    assert modified.tau_days == 2 and modified.tau_times == (time(9), time(21))
    result = case.run(rules=modified, weeks=(week(case.scope, modified),))
    assert result.selected_week is not None and len(result.selected_week.slices) == 4
    assert result.tau_seconds.value == 1200


def test_inputs_are_immutable_and_result_is_order_independent(case: TauCase) -> None:
    inputs = (case.context, case.points, case.population, case.baseline, case.rules)
    before = copy.deepcopy(inputs)
    result = case.run()
    reversed_week = replace(
        case.baseline,
        calendar=tuple(reversed(case.baseline.calendar)),
        slices=tuple(
            replace(s, pairs=tuple(reversed(s.pairs))) for s in reversed(case.baseline.slices)
        ),
    )
    assert case.run(points=list(reversed(case.points)), weeks=(reversed_week,)) == result
    assert before == inputs


def test_proofs_are_required_for_rows_matrices_and_weeks(case: TauCase) -> None:
    original = case.baseline.slices[0]
    variants = [
        replace(case.baseline, evidence_refs=()),
        replace(
            case.baseline,
            slices=(replace(original, coverage_confirmed=False),) + case.baseline.slices[1:],
        ),
        replace(
            case.baseline, slices=(replace(original, evidence_refs=()),) + case.baseline.slices[1:]
        ),
        replace(
            case.baseline,
            slices=(
                replace(
                    original, pairs=tuple(replace(p, rules_confirmed=False) for p in original.pairs)
                ),
            )
            + case.baseline.slices[1:],
        ),
        replace(
            case.baseline,
            slices=(
                replace(
                    original, pairs=tuple(replace(p, evidence_refs=()) for p in original.pairs)
                ),
            )
            + case.baseline.slices[1:],
        ),
    ]
    for candidate in variants:
        assert case.run(weeks=(candidate,)).tau_seconds.value is None


def test_iana_zone_uses_each_actual_service_date(case: TauCase) -> None:
    scope = replace(case.scope, timezone="America/New_York")
    result = case.run(timezone=scope.timezone, weeks=(week(scope, case.rules),))
    assert result.tau_seconds.value == 1200 and result.selected_week is not None
    by_date = {(s.service_date, s.local_time): s.departure_at for s in result.selected_week.slices}
    assert by_date[(date(2026, 10, 31), time(8))].hour == 12
    assert by_date[(date(2026, 11, 1), time(8))].hour == 13
    assert case.run(timezone="UTC").tau_seconds.value is None


def test_final_slice_average_underflow_is_not_reported_as_zero(case: TauCase) -> None:
    matrices = tuple(
        replace(
            s,
            pairs=(
                pair("p1", "p2", 5e-324 if i == 0 else 0),
                pair("p2", "p1", 5e-324 if i == 0 else 0),
            ),
        )
        for i, s in enumerate(case.baseline.slices)
    )
    result = case.run(weeks=(replace(case.baseline, slices=matrices),))
    assert result.tau_seconds.value is None and "NUMERIC_UNDERFLOW" in result.reasons
    assert all(s.mean_seconds.status == "available" for s in result.weeks[0].slices)


@pytest.mark.parametrize(
    "field, bad",
    [
        ("tau_days", 0),
        ("tau_days", True),
        ("tau_days", 8),
        ("tau_times", []),
        ("tau_times", ["08:00", "08:00"]),
        ("tau_times", ["8:00"]),
        ("max_total_wait_hours", float("nan")),
    ],
)
def test_invalid_tau_configuration(product: dict[str, Any], field: str, bad: object) -> None:
    product["methodologies"]["urban_passenger"]["parameters"][field] = bad
    with pytest.raises(ValueError):
        urban_tau_rules_from_mapping(product)
