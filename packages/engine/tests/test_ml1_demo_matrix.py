"""Independent, parameterized regression coverage for the finite ML1 fixture matrix."""

import sys
from dataclasses import asdict, replace
from functools import lru_cache
from pathlib import Path
from typing import Literal

import pytest
from a2_engine.scenario.engine import create_scenario_baseline, simulate_scenario
from a2_engine.scenario.feasibility import check_scenario_feasibility
from a2_engine.scenario.identity import scenario_hash
from a2_engine.scenario.models import (
    ScenarioBaseline,
    ScenarioEffect,
    ScenarioInputs,
    ScenarioMeasure,
    ScenarioOperation,
    ScenarioResult,
)
from a2_engine.scenario.overlay import apply_scenario_overlay
from a2_engine.urban.analysis.models import UrbanAnalysis

# This example package is intentionally outside the production wheel.
EXAMPLES = str(Path(__file__).resolve().parents[1] / "examples")
sys.path.insert(0, EXAMPLES)
try:
    from ml1_demo import oracle
    from ml1_demo.cargo import delivery_inputs, delivery_operation, urban_cargo_input
    from ml1_demo.intercity import passenger_inputs, passenger_operation
    from ml1_demo.matrix import (
        delivery_row,
        district_results,
        passenger_row,
        run_comparison,
        urban_cargo_row,
    )
    from ml1_demo.model import CATEGORIES, CITIES, DAY, PAIRS, PRIORITIES, PROOF, City, clock
    from ml1_demo.oracle import Stage, UrbanMode
    from ml1_demo.urban import analyze_car, urban_inputs, urban_operation
finally:
    sys.path.remove(EXAMPLES)


@lru_cache(maxsize=3)
def urban_baseline(city: City) -> ScenarioBaseline:
    return create_scenario_baseline(urban_inputs(city))


def assert_urban(
    analysis: UrbanAnalysis, city: City, mode: UrbanMode, stage: Stage = "before"
) -> None:
    assert analysis.h_seconds.value == city.h
    assert analysis.status == ("partial" if mode == "private_car" else "available")
    assert dict(analysis.selected_categories) == PRIORITIES
    group = next(m for m in analysis.modes if m.passenger_mode == mode)
    assert group.index.status == "available" and group.ranking_eligible
    assert group.index.value == pytest.approx(oracle.urban_index(city, mode, stage))
    assert tuple(p.point.population for p in group.points) == city.population
    for i, p in enumerate(group.points):
        assert p.index.value == pytest.approx(oracle.point_index(city, mode, i, stage))
        assert p.completeness == "full"
        assert {c.category for c in p.categories} == set(CATEGORIES)
        for c in p.categories:
            j = CATEGORIES.index(c.category)
            seconds = oracle.urban_time(city, mode, i, j, stage)
            assert c.best is not None and c.minimum_proven and c.completeness == "full"
            assert c.best.destination.destination_id == f"d{2 * j:02d}"
            assert len(c.objects) == 2
            assert c.best.journey.total_seconds == seconds
            assert c.index.value == pytest.approx(oracle.index(seconds, city.h))
            assert c.index.status == "available"
            if mode != "private_car":
                assert c.threshold is not None and c.threshold.seconds == 2800
                assert c.count.value == int(seconds <= 2800) + int(seconds + 90 <= 2800)
            if mode == "public_transport":
                journey = c.best.journey
                assert journey.walking_seconds == 60 * (1 + i % 3) + 30 * j
                assert journey.riding_seconds == (1560 + 60 * city.number if i < 3 else 600)
                assert journey.transfer_wait_seconds == (
                    (840 if stage == "before" else 360) if i < 3 else 0
                )


@pytest.mark.parametrize("city", CITIES, ids=lambda c: c.id)
@pytest.mark.parametrize("mode", ("walk", "private_car", "public_transport"))
def test_urban_passenger_baseline(city: City, mode: UrbanMode) -> None:
    baseline = urban_baseline(city)
    job = baseline.inputs.urban[0]
    assert len(job.points) == 6 and len(job.destinations) == 18
    if mode == "private_car":
        result = analyze_car(job, city)
        assert len(result.routes) == 108 and all(r.minimum_proven for r in result.routes)
        assert all(r.request.origin.build.traffic_mode == "static" for r in result.routes)
        assert_urban(result.analysis, city, mode)
    else:
        before = baseline.before.urban[0]
        assert len(before.searches) == 216
        assert_urban(before.analysis, city, mode)
        districts = district_results(before, mode)
        for district, start, end in (("west", 0, 3), ("east", 3, 6)):
            assert districts[district] == pytest.approx(
                oracle.population_mean(
                    city.population,
                    lambda i: oracle.point_index(city, mode, i),
                    start,
                    end,
                )
            )


@pytest.mark.parametrize(
    ("origin", "destination"), PAIRS, ids=[f"{a.id}->{b.id}" for a, b in PAIRS]
)
def test_intercity_passenger_baseline(origin: City, destination: City) -> None:
    baseline = create_scenario_baseline(passenger_inputs(origin, destination))
    job = baseline.inputs.intercity[0]
    result = baseline.before.intercity[0][1]
    assert (
        result.scope.comparison_scope.origin_territory_id,
        result.scope.comparison_scope.destination_territory_id,
    ) == (origin.id, destination.id)
    assert job.reference.full_ranking_eligible
    assert job.reference.settled_states == 4
    expected = oracle.passenger(origin, destination)
    row = passenger_row(result)
    assert {k: row[k] for k in asdict(expected)} == pytest.approx(asdict(expected))
    assert row["status"] == "available" and row["eligible"]
    assert row["transfers"] == 1
    assert row["first_departure"] == clock(expected.initial_wait).isoformat()
    assert result.frequency[-1].count == 1


@pytest.mark.parametrize("city", CITIES, ids=lambda c: c.id)
def test_urban_cargo_baseline(city: City) -> None:
    job = urban_cargo_input(city)
    baseline = create_scenario_baseline(ScenarioInputs(urban_cargo=(job,)))
    result = baseline.before.urban_cargo[0][1]
    row = urban_cargo_row(result, city)
    assert row["h_s"] == city.h and row["status"] == "available" and row["eligible"]
    assert row["index"] == pytest.approx(oracle.urban_cargo_index(city))
    assert tuple(r["total_s"] for r in row["sites"]) == oracle.urban_cargo_times(city)
    for site, expected in zip(row["sites"], oracle.urban_cargo_times(city), strict=True):
        assert site["compatibility"] == "confirmed"
        assert site["node"] == "terminal-0"
        assert (site["origin_access_s"], site["destination_access_s"]) == (60, 90)
        assert site["network_s"] == expected - 150
        assert site["index"] == pytest.approx(oracle.index(expected, city.h))
        assert any(
            r.from_id == site["site"]
            and r.compatibility.state == "forbidden"
            and r.seconds is not None
            and r.seconds < expected
            for r in job.routes
        )


def assert_delivery(row: dict[str, object], expected: oracle.DeliveryExpected) -> None:
    for key, value in asdict(expected).items():
        if key == "half_life":
            continue
        assert row[key] == (pytest.approx(value) if isinstance(value, float) else value)
    assert row["status"] == "available" and row["eligible"] is True
    assert row["actions"] == ("load", "road", "transfer", "rail", "unload")


@pytest.mark.parametrize(
    ("origin", "destination"), PAIRS, ids=[f"{a.id}->{b.id}" for a, b in PAIRS]
)
def test_intercity_cargo_baseline(origin: City, destination: City) -> None:
    baseline = create_scenario_baseline(delivery_inputs(origin, destination))
    job = baseline.inputs.cargo[0]
    result = baseline.before.cargo[0][1]
    expected = oracle.delivery(origin, destination)
    assert (result.scope.origin_territory_id, result.scope.destination_territory_id) == (
        origin.id,
        destination.id,
    )
    assert_delivery(delivery_row(result), expected)
    assert job.config.delivery_half_life_seconds[job.profile.urgency] == expected.half_life
    assert all(a.compatibility.state == "confirmed" for a in job.actions)


def effect(result: ScenarioResult, suffix: tuple[str, ...]) -> ScenarioEffect:
    return next(e for e in result.effects if e.key[-len(suffix) :] == suffix)


def measure(rows: tuple[ScenarioMeasure, ...], metric: ScenarioEffect) -> ScenarioMeasure:
    return next(r for r in rows if r.key == metric.key)


@pytest.mark.parametrize("city", CITIES, ids=lambda c: c.id)
@pytest.mark.parametrize("template", ("coordinate_transfer", "additional_departure"))
def test_urban_comparison(
    city: City, template: Literal["coordinate_transfer", "additional_departure"]
) -> None:
    baseline = urban_baseline(city)
    original = scenario_hash(baseline)
    operation, proof = urban_operation(baseline, template)
    result = run_comparison(baseline, operation, proof)
    assert result.complete and scenario_hash(baseline) == original
    assert result.before == baseline.before
    before, after = result.before.urban[0], result.after.urban[0]
    stage: Stage = "coordinate" if template == "coordinate_transfer" else "additional"
    assert_urban(after.analysis, city, "public_transport", stage)
    assert after.analysis.context == before.analysis.context
    assert after.analysis.h_seconds == before.analysis.h_seconds
    assert_urban(after.analysis, city, "walk")
    assert all(e.outcome == "unchanged" for e in result.effects if "walk" in e.key)
    assert result.impact.population_complete
    assert effect(result, ("O0", CATEGORIES[0], "public_transport", "time")).delta.value == 480
    neighbor = effect(result, ("O3", CATEGORIES[0], "public_transport", "time"))
    assert neighbor.delta.value == (-240 if stage == "coordinate" else 0)
    assert len(result.overlay.changed_events) == 2
    if stage == "additional":
        assert result.feasibility.state == "feasible" and proof is not None
        assert all(e.before_departure is None for e in result.overlay.changed_events)
        assert result.impact.known_affected_population == sum(city.population[:3])
        invalid, conflict = urban_operation(baseline, template, conflict=True)
        overlay = apply_scenario_overlay(baseline, invalid)
        rejected = check_scenario_feasibility(baseline, invalid, overlay, conflict)
        assert rejected.state == "infeasible"
        with pytest.raises(ValueError, match="VERIFIED_RESOURCE_TURNAROUND"):
            simulate_scenario(baseline, invalid, feasibility=conflict)
    else:
        assert neighbor.outcome == "worsened"
        assert neighbor.metric_id in result.worsened_metric_ids
        assert result.impact.known_affected_population == sum(city.population)
        with pytest.raises(ValueError, match="DOES_NOT_COORDINATE_TRANSFER"):
            simulate_scenario(baseline, replace(operation, shift_seconds=-60))


@pytest.mark.parametrize(
    ("origin", "destination"), PAIRS, ids=[f"{a.id}->{b.id}" for a, b in PAIRS]
)
def test_intercity_passenger_comparison(origin: City, destination: City) -> None:
    baseline = create_scenario_baseline(passenger_inputs(origin, destination))
    original = scenario_hash(baseline)
    operation = passenger_operation(baseline)
    result = run_comparison(baseline, operation, None)
    assert result.complete and scenario_hash(baseline) == original
    before, after = (passenger_row(r.intercity[0][1]) for r in (result.before, result.after))
    expected = oracle.passenger(origin, destination, after=True)
    assert {k: after[k] for k in asdict(expected)} == pytest.approx(asdict(expected))
    assert after["first_departure"] == before["first_departure"]
    assert after["movement"] == before["movement"]
    assert after["reference"] == before["reference"]
    assert effect(result, ("public", "total_seconds")).delta.value == 1200
    assert effect(result, ("public", "index")).delta.value == pytest.approx(
        expected.index - oracle.passenger(origin, destination).index,
    )
    assert result.overlay.inputs.intercity[0].reference == baseline.inputs.intercity[0].reference
    with pytest.raises(ValueError, match="EXECUTION_HASH_MISMATCH"):
        simulate_scenario(baseline, replace(operation, expected_execution_hash="stale-execution"))


@pytest.mark.parametrize(
    ("origin", "destination"), PAIRS, ids=[f"{a.id}->{b.id}" for a, b in PAIRS]
)
def test_intercity_cargo_comparison(origin: City, destination: City) -> None:
    baseline = create_scenario_baseline(delivery_inputs(origin, destination))
    original = scenario_hash(baseline)
    operation = delivery_operation(baseline)
    result = run_comparison(baseline, operation, None)
    assert result.complete and scenario_hash(baseline) == original
    before, after = (delivery_row(r.cargo[0][1]) for r in (result.before, result.after))
    expected = oracle.delivery(origin, destination, after=True)
    assert_delivery(after, expected)
    assert {k: before[k] for k in ("movement", "handling", "transshipment", "urgency")} == {
        k: after[k] for k in ("movement", "handling", "transshipment", "urgency")
    }
    assert effect(result, ("overall", "delivery_time")).delta.value == 3600
    assert effect(result, ("overall", "index")).delta.value == pytest.approx(
        expected.index - oracle.delivery(origin, destination).index,
    )
    assert len(result.overlay.changed_events) == 1
    assert result.before.urban_cargo == result.after.urban_cargo
    assert result.impact.affected_site_ids == (f"{origin.id}:warehouse",)
    with pytest.raises(ValueError, match="TRANSFER_NOT_CONFIRMED"):
        simulate_scenario(baseline, replace(operation, shift_seconds=-7201))


@pytest.mark.parametrize("city", CITIES, ids=lambda c: c.id)
def test_urban_cargo_comparison_is_blocked(city: City) -> None:
    job = urban_cargo_input(city)
    baseline = create_scenario_baseline(ScenarioInputs(urban_cargo=(job,)))
    operation = ScenarioOperation(
        baseline.baseline_id,
        "unsupported-handling",
        "reduce_handling_wait",
        "cargo",
        job.job_id,
        job.nodes[0].node_id,
        DAY,
        "handling",
        "no-supported-execution",
        -600,
        "none",
        "none",
        PROOF,
    )
    with pytest.raises(ValueError, match="OPERATION_NOT_ALLOWLISTED"):
        simulate_scenario(baseline, operation)
    assert baseline.before.urban_cargo[0][1].by_type[0].full_ranking_eligible


def test_spare_intercity_departure_has_zero_effect_on_the_selected_chain() -> None:
    baseline = create_scenario_baseline(passenger_inputs(*PAIRS[0]))
    result = run_comparison(baseline, passenger_operation(baseline, spare=True), None)
    assert result.complete
    assert len(result.overlay.changed_events) == 2
    assert all(e.outcome == "unchanged" and e.delta.value == 0 for e in result.effects)


@pytest.mark.parametrize("variant", ("connectivity", "unknown"))
def test_connection_gain_and_loss_use_null_delta(
    variant: Literal["connectivity", "unknown"],
) -> None:
    baseline = create_scenario_baseline(urban_inputs(CITIES[0], variant))
    operation, proof = urban_operation(baseline, "coordinate_transfer")
    result = run_comparison(baseline, operation, proof)
    gained = effect(result, ("O0", "school", "public_transport", "time"))
    lost = effect(result, ("O3", "adult_polyclinic", "public_transport", "time"))
    assert gained.delta.value is None and lost.delta.value is None
    assert measure(result.before.metrics, gained).value is None
    assert measure(result.after.metrics, gained).value == 2250
    assert measure(result.before.metrics, lost).value == 2700
    assert measure(result.after.metrics, lost).value is None
    if variant == "unknown":
        assert gained.outcome == lost.outcome == "unknown" and not result.complete
    else:
        assert (gained.outcome, lost.outcome) == ("new_connection", "lost_connection")
        assert lost.metric_id in result.worsened_metric_ids and result.complete


def test_incompatible_intercity_cargo_never_gets_a_delivery_time() -> None:
    inputs = delivery_inputs(*PAIRS[0])
    job = inputs.cargo[0]
    incompatible = replace(
        job,
        actions=tuple(
            replace(a, compatibility=replace(a.compatibility, state="forbidden"))
            if a.action_id == "rail"
            else a
            for a in job.actions
        ),
    )
    baseline = create_scenario_baseline(replace(inputs, cargo=(incompatible,)))
    result = baseline.before.cargo[0][1]
    assert result.overall.best_known is None
    assert all(m.value is None for m in baseline.before.metrics if m.key[-1] == "delivery_time")
    with pytest.raises(ValueError, match="TRANSFER_NOT_CONFIRMED"):
        simulate_scenario(baseline, delivery_operation(baseline))


def test_fixed_fixture_seed_and_overlay_are_reproducible() -> None:
    inputs = urban_inputs(CITIES[0])
    first = create_scenario_baseline(inputs)
    second = create_scenario_baseline(urban_inputs(CITIES[0]))
    assert first == second and first.input_hash == second.input_hash
    operation, proof = urban_operation(first, "coordinate_transfer")
    assert run_comparison(first, operation, proof) == run_comparison(second, operation, proof)
