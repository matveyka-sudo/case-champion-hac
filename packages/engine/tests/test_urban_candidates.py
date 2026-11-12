"""Synthetic route chains; none of these records represent a real accepted city."""

import copy
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

import pytest
from a2_engine import (
    MetricResult,
    UrbanAnalysis,
    UrbanArea,
    UrbanCandidate,
    UrbanConfig,
    UrbanContext,
    UrbanCoverage,
    UrbanDestination,
    UrbanGeometry,
    UrbanJourneyRules,
    UrbanLeg,
    UrbanPoint,
    UrbanPopulationCoverage,
    UrbanSearchScope,
    UrbanThreshold,
    UrbanVersions,
    analyze_urban_candidates,
    load_urban_journey_rules,
    urban_journey_rules_from_mapping,
    validate_urban_journey,
)
from a2_engine.urban.models import BOUNDED_URBAN_SEARCH

Step = tuple[Literal["walk", "ride", "wait_initial", "wait_transfer"], float]


def route(
    context: UrbanContext,
    *,
    point_id: str = "p1",
    destination_id: str = "d1",
    journey_id: str = "route",
    mode: str = "walk",
    category: str = "school",
    steps: Sequence[Step] = (("walk", 1200),),
) -> UrbanCandidate:
    start = context.requested_departure_at
    node = f"{point_id}-node"
    legs = []
    for index, (kind, duration) in enumerate(steps):
        target = (
            node
            if kind.startswith("wait")
            else f"{destination_id}-node"
            if index == len(steps) - 1
            else f"{journey_id}-node-{index}"
        )
        end = start + timedelta(seconds=duration)
        leg_mode = "walk" if kind == "walk" else mode
        legs.append(
            UrbanLeg(
                leg_mode,
                kind,
                node,
                target,
                start,
                end,
                duration,
                "synthetic-service" if kind == "ride" else None,
                ("synthetic-leg",),
            )
        )
        start, node = end, target
    return UrbanCandidate(
        journey_id,
        point_id,
        destination_id,
        category,
        mode,
        context,
        f"{point_id}-node",
        f"{destination_id}-node",
        context.requested_departure_at,
        start,
        tuple(legs),
        ("synthetic-route",),
    )


@dataclass
class SyntheticCase:
    config: UrbanConfig
    rules: UrbanJourneyRules
    context: UrbanContext
    area: UrbanArea
    points: list[UrbanPoint]
    destinations: list[UrbanDestination]
    candidates: list[UrbanCandidate]
    coverage: list[UrbanCoverage]
    population: UrbanPopulationCoverage
    thresholds: list[UrbanThreshold]

    def run(self, **changes: Any) -> UrbanAnalysis:
        arguments: dict[str, Any] = {
            "context": self.context,
            "points": self.points,
            "destinations": self.destinations,
            "candidates": self.candidates,
            "coverage": self.coverage,
            "area": self.area,
            "selected_categories": {"school": "medium"},
            "modes": ("walk",),
            "population_coverage": self.population,
            "config": self.config,
            "rules": self.rules,
            "thresholds": self.thresholds,
        }
        arguments.update(changes)
        return analyze_urban_candidates(**arguments)

    def proofs(
        self,
        *,
        modes: Sequence[str] = ("walk",),
        categories: Sequence[str] = ("school",),
        destinations: Sequence[UrbanDestination] | None = None,
    ) -> list[UrbanCoverage]:
        catalog = self.destinations if destinations is None else destinations
        return [
            UrbanCoverage(
                UrbanSearchScope(
                    self.context, point.point_id, category, mode, self.rules.max_total_wait_seconds
                ),
                True,
                True,
                ("synthetic-search-proof",),
                frozenset(
                    d.destination_id
                    for d in catalog
                    if category in d.categories and d.in_service_scope
                ),
            )
            for point in self.points
            for category in categories
            for mode in modes
        ]


@pytest.fixture
def synthetic(config: UrbanConfig) -> SyntheticCase:
    versions = UrbanVersions(
        "synthetic-snapshot",
        "graph-v1",
        "calendar-v1",
        "population-v1",
        "geometry-v1",
        config.methodology_version,
        "config-v1",
        "traffic-v1",
    )
    context = UrbanContext(
        "test-city",
        "test-city",
        datetime(2026, 11, 8, 8, tzinfo=UTC),
        "static",
        versions,
        "synthetic-search-options",
    )
    points = [
        UrbanPoint(
            p,
            f"cell-{p}",
            f"{p}-node",
            f"{p}-node",
            0,
            "test-city",
            population,
            2026,
            "synthetic-estimate",
            ("synthetic-population",),
        )
        for p, population in [("p1", 90), ("p2", 10)]
    ]
    destination = UrbanDestination(
        "d1",
        "physical-school",
        frozenset({"school"}),
        "d1-node",
        "neighbor-city",
        True,
        True,
        ("synthetic-registry",),
    )
    rules = load_urban_journey_rules()
    case = SyntheticCase(
        config,
        rules,
        context,
        UrbanArea("test-city", 100, "geometry-v1", ("synthetic-area",)),
        points,
        [destination],
        [
            route(context, point_id=p, journey_id=p, steps=(("walk", t),))
            for p, t in [("p1", 1200), ("p2", 2400)]
        ],
        [],
        UrbanPopulationCoverage(
            context, True, 0, ("synthetic-population-proof",), frozenset({"p1", "p2"})
        ),
        [
            UrbanThreshold(
                "test-city",
                "walk",
                versions,
                "static",
                "synthetic-search-options",
                1200,
                ("synthetic-tau-proof",),
            )
        ],
    )
    case.coverage = case.proofs()
    return case


def test_acceptance_32_minute_journey(synthetic: SyntheticCase) -> None:
    candidate = route(
        synthetic.context,
        mode="public_transport",
        steps=(("walk", 300), ("wait_initial", 420), ("ride", 900), ("walk", 300)),
    )
    timing = validate_urban_journey(candidate, rules=synthetic.rules)
    assert timing.total_seconds == 1920
    assert timing.walking_seconds == 600
    assert timing.riding_seconds == 900
    assert timing.initial_wait_seconds == timing.total_wait_seconds == 420
    assert timing.transfer_wait_seconds == 0
    assert timing.waiting_share == MetricResult(0.21875, "available")
    assert timing.candidate is candidate and timing.admissible
    assert candidate.departure_at == synthetic.context.requested_departure_at


def test_transfer_walk_is_not_also_waiting(synthetic: SyntheticCase) -> None:
    candidate = route(
        synthetic.context,
        mode="public_transport",
        steps=(
            ("wait_initial", 300),
            ("ride", 600),
            ("walk", 120),
            ("wait_transfer", 180),
            ("ride", 600),
            ("walk", 120),
        ),
    )
    timing = validate_urban_journey(candidate, rules=synthetic.rules)
    assert timing.total_seconds == 1920
    assert timing.walking_seconds == 240
    assert timing.riding_seconds == 1200
    assert timing.initial_wait_seconds == 300
    assert timing.transfer_wait_seconds == 180
    assert timing.total_wait_seconds == 480


@pytest.mark.parametrize("waiting, admissible", [(14400, True), (14460, False)])
def test_wait_limit_uses_sum_not_total_duration(
    synthetic: SyntheticCase,
    waiting: float,
    admissible: bool,
) -> None:
    candidate = route(
        synthetic.context,
        mode="public_transport",
        steps=(
            ("wait_initial", waiting / 2),
            ("ride", 600),
            ("wait_transfer", waiting / 2),
            ("ride", 600),
        ),
    )
    timing = validate_urban_journey(candidate, rules=synthetic.rules)
    assert timing.admissible is admissible
    assert timing.total_seconds == waiting + 1200
    assert timing.reason_code == (None if admissible else "MAX_TOTAL_WAIT_EXCEEDED")
    long = route(synthetic.context, mode="public_transport", steps=(("ride", 18000),))
    assert validate_urban_journey(long, rules=synthetic.rules).admissible


@pytest.mark.parametrize(
    "defect",
    ["overlap", "gap", "nodes", "duration", "departure", "arrival", "origin", "destination"],
)
def test_broken_chain_is_an_input_error(synthetic: SyntheticCase, defect: str) -> None:
    candidate = route(
        synthetic.context,
        mode="public_transport",
        steps=(("walk", 300), ("wait_initial", 420), ("ride", 900), ("walk", 300)),
    )
    legs = list(candidate.legs)
    if defect in ("overlap", "gap"):
        shift = timedelta(seconds=-60 if defect == "overlap" else 60)
        legs[1] = replace(legs[1], start_at=legs[1].start_at + shift, end_at=legs[1].end_at + shift)
    elif defect == "nodes":
        legs[1] = replace(legs[1], from_node_id="wrong-node")
    elif defect == "duration":
        legs[1] = replace(legs[1], duration_seconds=421)
    elif defect == "departure":
        candidate = replace(candidate, departure_at=candidate.departure_at + timedelta(seconds=1))
    elif defect == "arrival":
        candidate = replace(candidate, arrival_at=candidate.arrival_at + timedelta(seconds=1))
    elif defect == "origin":
        candidate = replace(candidate, origin_node_id="wrong-node")
    else:
        candidate = replace(candidate, destination_node_id="wrong-node")
    with pytest.raises(ValueError):
        validate_urban_journey(replace(candidate, legs=tuple(legs)), rules=synthetic.rules)
    with pytest.raises(ValueError):
        synthetic.run(
            candidates=[replace(candidate, legs=tuple(legs))],
            modes=("public_transport",),
            coverage=[],
        )


def test_wait_kind_and_node_are_semantic(synthetic: SyntheticCase) -> None:
    for steps in [
        (("ride", 600), ("wait_initial", 60), ("walk", 100)),
        (("wait_transfer", 60), ("ride", 600)),
    ]:
        with pytest.raises(ValueError):
            validate_urban_journey(
                route(
                    synthetic.context, mode="public_transport", steps=cast(Sequence[Step], steps)
                ),
                rules=synthetic.rules,
            )


def test_utc_offsets_midnight_and_dst(synthetic: SyntheticCase) -> None:
    start = datetime(2026, 11, 8, 23, 45, tzinfo=timezone(timedelta(hours=3)))
    context = replace(synthetic.context, requested_departure_at=start)
    candidate = route(context, steps=(("walk", 900),))
    end = datetime(2026, 11, 9, 1, tzinfo=timezone(timedelta(hours=4)))
    candidate = replace(candidate, arrival_at=end, legs=(replace(candidate.legs[0], end_at=end),))
    assert validate_urban_journey(candidate, rules=synthetic.rules).total_seconds == 900
    zone = ZoneInfo("America/New_York")
    first = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=0)
    second = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=1)
    context = replace(synthetic.context, requested_departure_at=first)
    candidate = route(context, steps=(("walk", 3600),))
    candidate = replace(
        candidate, arrival_at=second, legs=(replace(candidate.legs[0], end_at=second),)
    )
    assert validate_urban_journey(candidate, rules=synthetic.rules).total_seconds == 3600


def test_naive_datetimes_are_rejected(synthetic: SyntheticCase) -> None:
    with pytest.raises(ValueError, match="AWARE_DATETIME_REQUIRED"):
        replace(synthetic.context, requested_departure_at=datetime(2026, 11, 8, 8))
    with pytest.raises(ValueError, match="AWARE_DATETIME_REQUIRED"):
        replace(synthetic.candidates[0].legs[0], start_at=datetime(2026, 11, 8, 8))


@pytest.mark.parametrize("bad", [-1, True, "300", float("nan"), float("inf")])
def test_invalid_route_numbers(synthetic: SyntheticCase, bad: object) -> None:
    with pytest.raises(ValueError):
        replace(synthetic.candidates[0].legs[0], duration_seconds=cast(float, bad))
    with pytest.raises(ValueError):
        replace(synthetic.thresholds[0], seconds=cast(float, bad))


def test_route_limit_is_loaded_not_hardcoded(
    synthetic: SyntheticCase, product: dict[str, Any]
) -> None:
    assert load_urban_journey_rules().max_total_wait_seconds == 14400
    product["methodologies"]["urban_passenger"]["parameters"]["max_total_wait_hours"] = 1
    rules = urban_journey_rules_from_mapping(product)
    assert rules.max_total_wait_seconds == 3600
    candidate = route(
        synthetic.context, mode="public_transport", steps=(("wait_initial", 3601), ("ride", 600))
    )
    assert not validate_urban_journey(candidate, rules=rules).admissible


def test_zero_duration_has_no_wait_fraction(synthetic: SyntheticCase) -> None:
    candidate = route(synthetic.context, steps=(("walk", 0),))
    timing = validate_urban_journey(candidate, rules=synthetic.rules)
    assert timing.waiting_share == MetricResult(None, "not_applicable", "ZERO_DURATION")
    result = synthetic.run(candidates=[candidate])
    assert result.modes[0].points[0].index.value == 100


def test_two_point_population_example(synthetic: SyntheticCase) -> None:
    result = synthetic.run()
    mode = result.modes[0]
    assert result.h_seconds.value == 1200
    assert [point.index.value for point in mode.points] == [50, 25]
    assert mode.index.value == 47.5 and mode.ranking_eligible
    assert mode.completeness == "full"
    assert result.status == "available"
    assert result.context == synthetic.context
    best = result.modes[0].points[0].categories[0].best
    assert best is not None and best.destination.territory_id == "neighbor-city"


def test_tau_boundary_and_unknown_tau_do_not_change_h_index(synthetic: SyntheticCase) -> None:
    result = synthetic.run()
    assert [p.categories[0].count.value for p in result.modes[0].points] == [1, 0]
    without_tau = synthetic.run(thresholds=[])
    assert without_tau.modes[0].index.value == 47.5
    assert without_tau.modes[0].points[0].categories[0].count == MetricResult(
        None, "unknown", "TAU_UNAVAILABLE"
    )
    unknown_tau = synthetic.run(
        thresholds=[replace(synthetic.thresholds[0], seconds=None, evidence_refs=())]
    )
    assert unknown_tau.modes[0].index.value == 47.5
    assert unknown_tau.modes[0].points[0].categories[0].count.value is None


def test_minimum_time_not_record_order_selects_object(synthetic: SyntheticCase) -> None:
    second = replace(
        synthetic.destinations[0],
        destination_id="d2",
        object_identity="other-school",
        access_node_id="d2-node",
    )
    destinations = [synthetic.destinations[0], second]
    candidates = [
        route(synthetic.context, journey_id="closer-by-distance", steps=(("walk", 2400),)),
        route(
            synthetic.context,
            destination_id="d2",
            journey_id="faster-by-time",
            steps=(("walk", 1800),),
        ),
    ]
    result = synthetic.run(
        destinations=destinations,
        candidates=candidates,
        coverage=synthetic.proofs(destinations=destinations),
    )
    best = result.modes[0].points[0].categories[0].best
    assert best is not None and best.destination.destination_id == "d2"
    assert best.journey.total_seconds == 1800


def test_physical_identity_deduplicates_entrances_routes_and_counts(
    synthetic: SyntheticCase,
) -> None:
    entrance = replace(synthetic.destinations[0], destination_id="d2", access_node_id="d2-node")
    destinations = [synthetic.destinations[0], entrance]
    candidates = [
        route(synthetic.context, journey_id="slow", steps=(("walk", 2400),)),
        route(synthetic.context, journey_id="a", destination_id="d2"),
        route(synthetic.context, journey_id="b", destination_id="d2"),
    ]
    args = dict(destinations=destinations, coverage=synthetic.proofs(destinations=destinations))
    result = synthetic.run(candidates=candidates, **args)
    category = result.modes[0].points[0].categories[0]
    assert len(category.objects) == category.count.value == 1
    assert (
        category.best is not None and category.best.destination.object_identity == "physical-school"
    )
    assert category.best.destination.destination_id == "d2"
    assert category.best.journey.candidate.journey_id == "a"
    assert synthetic.run(candidates=list(reversed(candidates)), **args) == result


def test_equal_time_tie_breaks_by_physical_identity_then_record_id(
    synthetic: SyntheticCase,
) -> None:
    destinations = [
        replace(synthetic.destinations[0], object_identity="z-school"),
        replace(
            synthetic.destinations[0],
            destination_id="d2",
            access_node_id="d2-node",
            object_identity="a-school",
        ),
    ]
    candidates = [
        route(synthetic.context, destination_id=d.destination_id, journey_id=d.destination_id)
        for d in destinations
    ]
    first = synthetic.run(
        destinations=destinations,
        candidates=candidates,
        coverage=synthetic.proofs(destinations=destinations),
    )
    second = synthetic.run(
        destinations=list(reversed(destinations)),
        candidates=list(reversed(candidates)),
        coverage=list(reversed(synthetic.proofs(destinations=destinations))),
    )
    assert first == second
    best = first.modes[0].points[0].categories[0].best
    assert best is not None and best.destination.object_identity == "a-school"


def test_one_institution_can_serve_two_categories(synthetic: SyntheticCase) -> None:
    destinations = [
        replace(synthetic.destinations[0], categories=frozenset({"school", "kindergarten"}))
    ]
    candidates = [
        replace(c, category=category, journey_id=f"{c.journey_id}-{category}")
        for c in synthetic.candidates
        for category in ("school", "kindergarten")
    ]
    result = synthetic.run(
        destinations=destinations,
        candidates=candidates,
        selected_categories={"school": "low", "kindergarten": "high"},
        coverage=synthetic.proofs(destinations=destinations, categories=("school", "kindergarten")),
    )
    assert result.modes[0].index.value == 47.5
    assert len(result.modes[0].points[0].categories) == 2
    assert all(len(c.objects) == 1 for c in result.modes[0].points[0].categories)


def test_public_transport_accepts_pure_walking_and_rejects_cars(synthetic: SyntheticCase) -> None:
    candidates = [replace(c, passenger_mode="public_transport") for c in synthetic.candidates]
    result = synthetic.run(
        candidates=candidates,
        modes=("public_transport",),
        coverage=synthetic.proofs(modes=("public_transport",)),
    )
    assert result.modes[0].index.value == 47.5
    car = route(synthetic.context, mode="private_car", steps=(("ride", 600),))
    with pytest.raises(ValueError, match="PASSENGER_MODE_MISMATCH"):
        synthetic.run(candidates=[replace(car, passenger_mode="public_transport")])


def test_modes_have_separate_results(synthetic: SyntheticCase) -> None:
    candidates = [
        route(
            synthetic.context,
            point_id=p,
            journey_id=f"{p}-{mode}",
            mode=mode,
            steps=(("ride" if mode == "private_car" else "walk", seconds),),
        )
        for mode, times in [
            ("walk", (1200, 2400)),
            ("public_transport", (2400, 4800)),
            ("private_car", (1200, 1200)),
        ]
        for p, seconds in zip(("p1", "p2"), times, strict=True)
    ]
    modes = ("walk", "public_transport", "private_car")
    result = synthetic.run(
        candidates=candidates, modes=modes, coverage=synthetic.proofs(modes=modes)
    )
    assert {m.passenger_mode: m.index.value for m in result.modes} == {
        "walk": 47.5,
        "public_transport": 23.125,
        "private_car": 50,
    }


def test_full_absence_is_a_zero_index_not_a_zero_time(synthetic: SyntheticCase) -> None:
    result = synthetic.run(candidates=[])
    assert result.journeys == ()
    for point in result.modes[0].points:
        category = point.categories[0]
        assert category.index == MetricResult(0, "available", "NO_FEASIBLE_ROUTE")
        assert category.best is None and category.objects == ()
        assert category.count.value == 0
        assert category.minimum_proven and category.completeness == "full"
        assert "synthetic-search-proof" in category.evidence_refs


@pytest.mark.parametrize("found", [True, False])
def test_incomplete_search_or_provider_failure_never_becomes_proven_absence(
    synthetic: SyntheticCase, found: bool
) -> None:
    coverage = [
        replace(c, search_complete=False, provider_error="PROVIDER_TIMEOUT")
        for c in synthetic.coverage
    ]
    result = synthetic.run(coverage=coverage, candidates=synthetic.candidates if found else [])
    category = result.modes[0].points[0].categories[0]
    assert category.index == MetricResult(None, "unknown", "DATA_UNAVAILABLE")
    assert category.count.value is None and not category.minimum_proven
    assert (category.best is not None) is found
    assert not result.modes[0].ranking_eligible and result.modes[0].index.value is None


def test_missing_coverage_and_partial_catalog_are_not_implicitly_full(
    synthetic: SyntheticCase,
) -> None:
    for coverage in ([], [replace(c, catalog_complete=False) for c in synthetic.coverage]):
        result = synthetic.run(coverage=coverage)
        assert result.modes[0].index.value is None
        category = result.modes[0].points[0].categories[0]
        assert category.best is not None and category.best.journey.total_seconds == 1200
        assert category.index.reason_code == "INCOMPLETE_COVERAGE"


def test_scope_proofs_bind_actual_point_and_catalog_sets(synthetic: SyntheticCase) -> None:
    result = synthetic.run(
        points=synthetic.points[:1],
        candidates=synthetic.candidates[:1],
        coverage=synthetic.coverage[:1],
    )
    assert result.modes[0].index.reason_code == "INCOMPLETE_POPULATION_COVERAGE"
    coverage = [
        replace(c, catalog_destination_ids=frozenset({"d1", "missing-record"}))
        for c in synthetic.coverage
    ]
    result = synthetic.run(coverage=coverage)
    assert result.modes[0].points[0].categories[0].index.value is None


@pytest.mark.parametrize("excluded", [1, None])
def test_lost_or_unknown_excluded_population_blocks_rating(
    synthetic: SyntheticCase, excluded: float | None
) -> None:
    result = synthetic.run(
        population_coverage=replace(synthetic.population, excluded_population=excluded)
    )
    assert result.modes[0].index == MetricResult(None, "unknown", "INCOMPLETE_POPULATION_COVERAGE")
    assert not result.modes[0].ranking_eligible


def test_population_unknown_zero_and_empty_denominators(synthetic: SyntheticCase) -> None:
    unknown = [
        synthetic.points[0],
        replace(synthetic.points[1], population=None, population_year=None, population_method=None),
    ]
    assert synthetic.run(points=unknown).modes[0].index.reason_code == "POPULATION_UNAVAILABLE"
    zero = [synthetic.points[0], replace(synthetic.points[1], population=0)]
    result = synthetic.run(points=zero, coverage=synthetic.coverage[:1])
    assert result.modes[0].points[1].index.value is None
    assert result.modes[0].index.value == 50 and result.modes[0].ranking_eligible
    result = synthetic.run(points=[replace(p, population=0) for p in synthetic.points])
    assert result.modes[0].index == MetricResult(None, "not_applicable", "ZERO_WEIGHT")
    assert not result.modes[0].ranking_eligible


def test_duplicate_weights_are_errors_but_prepared_shares_are_allowed(
    synthetic: SyntheticCase,
) -> None:
    with pytest.raises(ValueError, match="DUPLICATE_POINT_ID"):
        synthetic.run(points=synthetic.points + synthetic.points[:1])
    shared_cell = [
        synthetic.points[0],
        replace(synthetic.points[1], cell_id=synthetic.points[0].cell_id),
    ]
    with pytest.raises(ValueError, match="DUPLICATE_POPULATION_WEIGHT"):
        synthetic.run(points=shared_cell)
    prepared = [replace(p, population_share_id=p.point_id) for p in shared_cell]
    assert synthetic.run(points=prepared).modes[0].index.value == 47.5


@pytest.mark.parametrize(
    "field",
    [
        "snapshot_id",
        "graph_version",
        "calendar_version",
        "population_version",
        "geometry_version",
        "config_hash",
        "traffic_version",
    ],
)
def test_candidate_versions_cannot_be_mixed(synthetic: SyntheticCase, field: str) -> None:
    context = replace(
        synthetic.context, versions=replace(synthetic.context.versions, **{field: "different"})
    )
    with pytest.raises(ValueError, match="CANDIDATE_CONTEXT_MISMATCH"):
        synthetic.run(candidates=[replace(synthetic.candidates[0], context=context)])


def test_no_traffic_or_time_fallback(synthetic: SyntheticCase) -> None:
    for context in (
        replace(synthetic.context, traffic_mode="typical"),
        replace(
            synthetic.context,
            requested_departure_at=synthetic.context.requested_departure_at + timedelta(minutes=15),
        ),
    ):
        with pytest.raises(ValueError, match="CANDIDATE_CONTEXT_MISMATCH"):
            synthetic.run(candidates=[replace(synthetic.candidates[0], context=context)])
    threshold = replace(synthetic.thresholds[0], traffic_mode="typical")
    with pytest.raises(ValueError, match="TAU_CONTEXT_MISMATCH"):
        synthetic.run(thresholds=[threshold])
    wrong_limit = [
        replace(c, scope=replace(c.scope, max_total_wait_seconds=1)) for c in synthetic.coverage
    ]
    with pytest.raises(ValueError, match="INVALID_OR_DUPLICATE_SEARCH_SCOPE"):
        synthetic.run(coverage=wrong_limit)


def test_wait_rejection_is_retained_and_not_confused_with_broken_input(
    synthetic: SyntheticCase,
) -> None:
    candidate = route(
        synthetic.context, mode="public_transport", steps=(("wait_initial", 14460), ("ride", 600))
    )
    result = synthetic.run(
        candidates=[candidate],
        modes=("public_transport",),
        coverage=synthetic.proofs(modes=("public_transport",)),
    )
    category = result.modes[0].points[0].categories[0]
    assert category.best is None and category.index.reason_code == "NO_FEASIBLE_ROUTE"
    assert category.rejected[0].reason_code == "MAX_TOTAL_WAIT_EXCEEDED"


def test_unselected_unknown_category_does_not_block_selected_scores(
    synthetic: SyntheticCase,
) -> None:
    unknown = replace(
        synthetic.destinations[0],
        destination_id="airport",
        object_identity="airport",
        categories=frozenset({"airport"}),
        eligible=None,
    )
    assert (
        synthetic.run(destinations=synthetic.destinations + [unknown]).modes[0].index.value == 47.5
    )
    result = synthetic.run(destinations=[replace(synthetic.destinations[0], eligible=None)])
    assert (
        result.modes[0].points[0].categories[0].index.reason_code
        == "DESTINATION_ELIGIBILITY_UNKNOWN"
    )


def test_source_connector_and_actual_geometry_are_preserved(synthetic: SyntheticCase) -> None:
    candidate = route(
        synthetic.context,
        mode="public_transport",
        steps=(("walk", 300), ("wait_initial", 420), ("ride", 900), ("walk", 300)),
    )
    point = replace(synthetic.points[0], access_node_id="route-node-0", access_distance_m=100)
    geometry = UrbanGeometry(((30, 50), (30.01, 50.01)), ("synthetic-geometry",))
    candidate = replace(candidate, geometry=geometry)
    args = dict(
        points=[point, synthetic.points[1]],
        modes=("public_transport",),
        coverage=synthetic.proofs(modes=("public_transport",)),
    )
    result = synthetic.run(candidates=[candidate], **args)
    best = result.modes[0].points[0].categories[0].best
    assert best is not None and best.journey.candidate.geometry is geometry
    assert best.journey.total_seconds == 1920 and best.journey.candidate.geometry_kind == "actual"
    missing = route(synthetic.context, mode="public_transport", steps=(("ride", 900),))
    with pytest.raises(ValueError, match="ACCESS_CONNECTOR_REQUIRED"):
        synthetic.run(candidates=[missing], **args)


def test_analysis_does_not_mutate_input_collections_or_config(synthetic: SyntheticCase) -> None:
    inputs = (
        synthetic.context,
        synthetic.points,
        synthetic.destinations,
        synthetic.candidates,
        synthetic.coverage,
        synthetic.population,
        synthetic.thresholds,
    )
    before = copy.deepcopy(inputs)
    weights = dict(synthetic.config.priority_weights)
    selection = {"school": "medium"}
    result = synthetic.run(selected_categories=selection)
    assert before == inputs and weights == dict(synthetic.config.priority_weights)
    selection["school"] = "low"
    assert result.selected_categories["school"] == "medium"
    with pytest.raises(TypeError):
        cast(dict[str, str], result.selected_categories)["school"] = "high"


def test_timestamps_are_the_authoritative_duration(synthetic: SyntheticCase) -> None:
    candidate = synthetic.candidates[0]
    declared_with_float_noise = replace(candidate.legs[0], duration_seconds=1200 + 5e-10)
    timing = validate_urban_journey(
        replace(candidate, legs=(declared_with_float_noise,)), rules=synthetic.rules
    )
    assert timing.total_seconds == timing.walking_seconds == 1200


def test_ineligible_fast_object_is_not_selected(synthetic: SyntheticCase) -> None:
    rejected = replace(
        synthetic.destinations[0],
        destination_id="private",
        access_node_id="private-node",
        object_identity="unfit-school",
        eligible=False,
    )
    destinations = synthetic.destinations + [rejected]
    candidates = synthetic.candidates + [
        route(
            synthetic.context,
            destination_id="private",
            journey_id="fast-unfit",
            steps=(("walk", 60),),
        )
    ]
    result = synthetic.run(
        destinations=destinations,
        candidates=candidates,
        coverage=synthetic.proofs(destinations=destinations),
    )
    assert result.modes[0].index.value == 47.5
    best = result.modes[0].points[0].categories[0].best
    assert best is not None and best.destination.destination_id == "d1"


def test_admissible_alternative_survives_a_wait_limit_failure(synthetic: SyntheticCase) -> None:
    too_much_wait = route(
        synthetic.context,
        journey_id="wait-failure",
        mode="public_transport",
        steps=(("wait_initial", 14460), ("ride", 60)),
    )
    allowed = route(
        synthetic.context,
        journey_id="valid-alternative",
        mode="public_transport",
        steps=(("ride", 18000),),
    )
    result = synthetic.run(
        candidates=[too_much_wait, allowed],
        modes=("public_transport",),
        coverage=synthetic.proofs(modes=("public_transport",)),
    )
    category = result.modes[0].points[0].categories[0]
    assert (
        category.best is not None
        and category.best.journey.candidate.journey_id == "valid-alternative"
    )
    assert len(category.rejected) == 1 and category.index.status == "available"


def test_population_coverage_flag_and_version_are_required(synthetic: SyntheticCase) -> None:
    partial = synthetic.run(population_coverage=replace(synthetic.population, complete=False))
    assert partial.modes[0].index.reason_code == "INCOMPLETE_POPULATION_COVERAGE"
    context = replace(
        synthetic.context, versions=replace(synthetic.context.versions, population_version=None)
    )
    coverage = [replace(c, scope=replace(c.scope, context=context)) for c in synthetic.coverage]
    candidates = [replace(c, context=context) for c in synthetic.candidates]
    thresholds = [replace(t, versions=context.versions) for t in synthetic.thresholds]
    result = synthetic.run(
        context=context,
        coverage=coverage,
        candidates=candidates,
        thresholds=thresholds,
        population_coverage=replace(synthetic.population, context=context),
    )
    assert result.modes[0].index.reason_code == "POPULATION_VERSION_UNAVAILABLE"


@pytest.mark.parametrize("field", ["city_id", "versions", "search_parameters_id"])
def test_tau_scope_is_checked(synthetic: SyntheticCase, field: str) -> None:
    changes: dict[str, Any] = {
        field: replace(synthetic.context.versions, graph_version="other")
        if field == "versions"
        else "other"
    }
    with pytest.raises(ValueError, match="TAU_CONTEXT_MISMATCH"):
        synthetic.run(thresholds=[replace(synthetic.thresholds[0], **changes)])


def test_unknown_area_does_not_guess_h_or_hide_known_routes(synthetic: SyntheticCase) -> None:
    result = synthetic.run(area=replace(synthetic.area, area_km2=None), thresholds=[])
    assert result.h_seconds.reason_code == "AREA_UNAVAILABLE"
    assert result.modes[0].index.value is None
    category = result.modes[0].points[0].categories[0]
    assert category.best is not None and category.minimum_proven
    assert category.index.reason_code == "H_UNAVAILABLE" and result.status == "partial"


def test_bad_request_ids_and_duplicate_proofs_are_rejected(synthetic: SyntheticCase) -> None:
    cases: list[dict[str, Any]] = [
        dict(selected_categories={}),
        dict(selected_categories={"school": "invalid-priority"}),
        dict(selected_categories={"invalid-category": "low"}),
        dict(modes=()),
        dict(coverage=synthetic.coverage + synthetic.coverage[:1]),
        dict(candidates=synthetic.candidates + synthetic.candidates[:1]),
        dict(destinations=synthetic.destinations * 2),
        dict(candidates=[replace(synthetic.candidates[0], point_id="unknown-point")]),
        dict(area=replace(synthetic.area, geometry_version="different")),
    ]
    for changes in cases:
        with pytest.raises(ValueError):
            synthetic.run(**changes)


def test_bounded_population_uses_original_weights_and_leaves_absence_unknown(
    synthetic: SyntheticCase,
) -> None:
    context = replace(synthetic.context, search_parameters_id=BOUNDED_URBAN_SEARCH)
    candidates = [replace(candidate, context=context) for candidate in synthetic.candidates]
    coverage = [
        replace(row, scope=replace(row.scope, context=context)) for row in synthetic.coverage
    ]
    basis = replace(synthetic.population, context=context, complete=False, excluded_population=90)
    result = synthetic.run(
        context=context,
        candidates=candidates,
        coverage=coverage,
        population_coverage=basis,
        thresholds=(),
    ).modes[0]
    assert result.index.value == pytest.approx(0.9 * 50 + 0.1 * 25)
    assert result.completeness == "partial" and not result.ranking_eligible
    assert result.points[0].categories[0].count.status == "not_applicable"
    missing = synthetic.run(
        context=context,
        candidates=candidates[:1],
        coverage=coverage,
        population_coverage=basis,
        thresholds=(),
    ).modes[0]
    assert missing.points[1].categories[0].index.value is None
    assert missing.index.value is None
