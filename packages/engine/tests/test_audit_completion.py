"""New raw-enumeration oracles and integration-gate regressions."""

import math
import runpy
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest
from a2_engine import (
    PhysicalDeparture,
    PhysicalService,
    ScenarioFeasibilityInputs,
    ScenarioResourceBlock,
    UrbanODCollector,
    UrbanODObservation,
    UrbanODPair,
    calculate_urban_tau,
    create_scenario_baseline,
    load_urban_tau_rules,
    scenario_hash,
    simulate_scenario,
)
from a2_engine.routing.timetable import solve_timetable

W = runpy.run_path(str(Path(__file__).parents[1] / "examples/audit_completion_workloads.py"))


@pytest.mark.parametrize("seed", range(30))
def test_new_urban_and_intercity_raw_oracle(seed: int) -> None:
    scope, table, raw = W["timetable_case"](16, seed)
    start = table.scope.start_at
    paths = W["enumerate_schedule"](raw, 14400)
    actual = solve_timetable(table, "A", "B", start, max_wait_seconds=14400)
    expected = min((p[1] for p in paths), default=None)
    assert (actual.best_known.total_seconds if actual.best_known else None) == expected
    assert actual.proven
    all_paths = W["enumerate_schedule"](raw)
    result = W["passenger"](scope, table)
    scored = [
        (
            math.sqrt(100 * min(1, 1800 / (arr - first)) * 100 * 2 ** (-first / 43200)),
            first,
            arr,
            modes,
            path,
        )
        for first, arr, modes, path in all_paths
    ]
    for group in result.by_mode + (result.public,):
        choices = [
            p
            for p in scored
            if group.category == "public"
            or (p[3][0] if len(p[3]) == 1 else "mixed") == group.category
        ]
        if choices:
            assert group.best_known is not None
            best = max(p[0] for p in choices)
            assert group.best_known.evaluation.index.value == pytest.approx(best)
            assert (group.best_known.initial_wait_seconds, group.best_known.total_seconds) in {
                (p[1], p[2]) for p in choices if abs(p[0] - best) < 1e-10
            }
        else:
            assert group.best_known is None
    for freq in result.frequency:
        choices = [
            p
            for p in scored
            if freq.category == "public"
            or (p[3][0] if len(p[3]) == 1 else "mixed") == freq.category
        ]
        assert freq.count == len({p[4][0] for p in choices})
    limited = W["passenger"](scope, table, 0)
    assert limited.search.interrupted and not limited.public.optimum_proven
    assert not solve_timetable(
        table, "A", "B", start, max_wait_seconds=14400, resource_limit=0
    ).proven


@pytest.mark.parametrize("width", [1, 2, 3, 5])
def test_new_cargo_raw_completion_oracle(width: int) -> None:
    case = W["cargo_case"](width)
    expected = min(W["enumerate_cargo"](case), key=lambda p: (p[0], len(p[1]), p[1]))
    actual = W["cargo"](case)
    best = actual.overall.best_known
    assert best is not None and actual.overall.optimum_proven
    assert (best.evaluation.total_seconds.value, best.action_ids) == expected
    assert best.evaluation.index.value == pytest.approx(100 * 2 ** (-expected[0] / 86400))
    assert not W["cargo"](case, 2).overall.optimum_proven
    if width > 1:
        m, scope, states, actions, ready = case
        changed = tuple(
            replace(a, seconds=None) if a.action_id == "unload:0" else a for a in actions
        )
        partial = W["cargo"]((m, scope, states, changed, ready))
        assert partial.overall.best_known is not None and not partial.overall.optimum_proven
        oracle = min(W["enumerate_cargo"]((m, scope, states, changed, ready)))
        assert partial.overall.best_known.evaluation.total_seconds.value == oracle[0]


@pytest.mark.parametrize("size", [3, 7])
def test_new_od_fraction_oracle_and_batch_attacks(size: int) -> None:
    plan, context, population = W["od_case"](size)
    actual = W["od"](size)
    weights = {p.point_id: Fraction(p.population) for p in plan.points}
    sums = []
    for slot in range(28):
        denominator = sum(weights[a] * weights[b] for a in weights for b in weights if a != b)
        sums.append(
            sum(
                weights[a] * weights[b] * (100 + 7 * int(a) + int(b) + slot)
                for a in weights
                for b in weights
                if a != b
            )
            / denominator
        )
    assert actual.tau_seconds.value == float(sum(sums) / 28)
    tasks = list(plan.tasks())
    rows = [
        UrbanODObservation(
            t,
            UrbanODPair(
                t.origin.point_id,
                t.destination.point_id,
                "reachable",
                100
                + 7 * int(t.origin.point_id)
                + int(t.destination.point_id)
                + plan.departures.index(t.departure_at),
                True,
                True,
                ("proof",),
            ),
        )
        for t in tasks
    ]
    collector = UrbanODCollector(plan)
    collector.add(reversed(rows))
    collector.add(rows[:3])
    week = collector.to_week()
    repeat = calculate_urban_tau(
        context,
        plan.points,
        population,
        (week,),
        passenger_mode="private_car",
        timezone="UTC",
        reference_at=plan.reference_at,
        rules=load_urban_tau_rules(),
    )
    assert repeat == actual
    with pytest.raises(ValueError, match="CONFLICTING_OD_REPLAY"):
        collector.add((replace(rows[0], pair=replace(rows[0].pair, seconds=999)),))
    with pytest.raises(ValueError, match="FOREIGN_OD_TASK"):
        collector.add(
            (
                replace(
                    rows[0],
                    task=replace(rows[0].task, scope=replace(plan.scope, passenger_mode="walk")),
                ),
            )
        )
    partial = UrbanODCollector(plan)
    partial.add(rows[1:])
    result = calculate_urban_tau(
        context,
        plan.points,
        population,
        (partial.to_week(),),
        passenger_mode="private_car",
        timezone="UTC",
        reference_at=plan.reference_at,
        rules=load_urban_tau_rules(),
    )
    assert result.tau_seconds.value is None


def test_additional_departure_updates_all_shared_timetable_views() -> None:
    from datetime import timedelta

    m = W["demo"]("scenario")
    inputs, _, _ = m["demo_inputs"]()
    baseline, op = m["demo_baseline"]()
    ds = inputs.timetables[0]
    generic = replace(ds.timetable.transfers[0], from_run_id=None, to_run_id=None)
    ds = replace(ds, timetable=replace(ds.timetable, transfers=(generic,)))
    inputs = replace(
        inputs,
        timetables=(ds, replace(ds, network_id="other")),
        urban=inputs.urban + (replace(inputs.urban[0], job_id="other", network_id="other"),),
    )
    baseline = create_scenario_baseline(inputs)
    source = next(r for r in ds.timetable.runs if r.run_id == op.execution_id)
    new = replace(
        source,
        trip_id="new",
        events=tuple(
            replace(
                e,
                arrival_at=e.arrival_at + timedelta(minutes=30),
                departure_at=e.departure_at + timedelta(minutes=30),
            )
            for e in source.events
        ),
    )
    op = replace(
        op,
        baseline_id=baseline.baseline_id,
        template_id="additional_departure",
        shift_seconds=1800,
        new_run=new,
        new_physical_service=PhysicalService(
            "new", (new.run_id,), (PhysicalDeparture(0, "new", m["PROOF"]),), m["PROOF"]
        ),
        demand_evidence_refs=m["PROOF"],
    )
    block = ScenarioResourceBlock(
        "bus",
        "network",
        (new.run_id,),
        m["START"] - timedelta(days=1),
        m["START"] + timedelta(days=1),
        (),
        m["PROOF"],
        0,
        0,
    )
    proof = ScenarioFeasibilityInputs(
        baseline.baseline_id,
        scenario_hash(op),
        (block,),
        (new.run_id,),
        True,
        True,
        True,
        m["START"] - timedelta(days=1),
        m["START"] + timedelta(days=1),
        m["PROOF"],
    )
    result = simulate_scenario(baseline, op, feasibility=proof)
    assert all(
        new.run_id in {r.run_id for r in d.timetable.runs} for d in result.overlay.inputs.timetables
    )
    values = [r.analysis.modes[0].index.value for r in result.after.urban]
    assert values[0] == values[1]
    from a2_engine import validate_scenario_result

    validate_scenario_result(baseline, op, result, feasibility=proof)
    assert all(
        new.run_id not in {r.run_id for r in d.timetable.runs} for d in baseline.inputs.timetables
    )
    assert simulate_scenario(baseline, op, feasibility=proof) == result
    with pytest.raises(ValueError, match="SUBSTITUTED_OR_WORSENING_HIDDEN"):
        validate_scenario_result(
            baseline, op, replace(result, effects=result.effects[1:]), feasibility=proof
        )
    with pytest.raises(ValueError):
        simulate_scenario(baseline, replace(op, shift_seconds=1801), feasibility=proof)


def test_window_boundary_continues_after_day_seven() -> None:
    from datetime import timedelta

    scope, table, _ = W["timetable_case"](2)
    runs = []
    for i, offset in enumerate((7 * 86400 - 1, 7 * 86400)):
        run = table.runs[i]
        dep = table.scope.start_at + timedelta(seconds=offset)
        runs.append(
            replace(
                run,
                events=(
                    replace(run.events[0], node_id="A", arrival_at=dep, departure_at=dep),
                    replace(
                        run.events[1],
                        node_id="B",
                        arrival_at=dep + timedelta(seconds=600),
                        departure_at=dep + timedelta(seconds=600),
                    ),
                ),
            )
        )
    result = W["passenger"](scope, replace(table, runs=tuple(runs)))
    assert result.public.best_known is not None
    assert result.public.best_known.initial_wait_seconds == 7 * 86400 - 1
    assert result.public.best_known.path_seconds == 600
    assert next(f.count for f in result.frequency if f.category == "public") == 1


@pytest.mark.parametrize("dst", [False, True])
def test_last_return_utc_oracle_across_midnight_and_dst(dst: bool) -> None:
    from datetime import UTC, datetime, timedelta

    from a2_engine import UrbanTransitNetwork, search_urban_last_return

    m = W["demo"]("scenario")
    inputs, _, _ = m["demo_inputs"]()
    job = inputs.urban[0]
    ds = inputs.timetables[0]
    first = (
        datetime(2026, 10, 25, 0, tzinfo=UTC) if dst else datetime(2026, 11, 9, 23, 0, tzinfo=UTC)
    )
    departures = (
        (first, first + timedelta(hours=1)) if dst else (first, first + timedelta(minutes=59))
    )
    runs = []
    for i, departure in enumerate(departures):
        source = ds.timetable.runs[0]
        runs.append(
            replace(
                source,
                trip_id=f"return:{i}",
                service_date=first.date(),
                timezone="Europe/Berlin" if dst else "UTC",
                events=(
                    replace(
                        source.events[0], node_id="D", arrival_at=departure, departure_at=departure
                    ),
                    replace(
                        source.events[1],
                        node_id="S",
                        arrival_at=departure + timedelta(minutes=30),
                        departure_at=departure + timedelta(minutes=30),
                    ),
                ),
            )
        )
    table = replace(
        ds.timetable,
        scope=replace(
            ds.timetable.scope,
            timezone="Europe/Berlin" if dst else "UTC",
            start_at=first - timedelta(days=1),
            end_at=first + timedelta(days=2),
        ),
        runs=tuple(runs),
        transfers=(),
    )
    result = search_urban_last_return(
        UrbanTransitNetwork(job.context, table, m["PROOF"]),
        job.points[0],
        job.destinations[0],
        local_day=first.date(),
        rules=job.rules,
    )
    assert result.proven and result.leave_at == departures[-1]
    limited = search_urban_last_return(
        UrbanTransitNetwork(job.context, table, m["PROOF"]),
        job.points[0],
        job.destinations[0],
        local_day=first.date(),
        rules=job.rules,
        resource_limit=0,
    )
    assert not limited.proven


def test_cargo_first_departure_31_day_boundary_and_completion_after_window() -> None:
    from datetime import timedelta

    case = W["cargo_case"](1)
    m, scope, states, actions, ready = case
    for offset, exists in ((31 * 86400 - 1, True), (31 * 86400, False)):
        original = next(a for a in actions if a.kind == "movement" and a.mode == "road")
        delta = timedelta(seconds=offset) - (original.scheduled_at - m["START"])
        changed = tuple(
            replace(a, scheduled_at=a.scheduled_at + delta) if a.scheduled_at else a
            for a in actions
        )
        result = W["cargo"]((m, scope, states, changed, ready))
        oracle = W["enumerate_cargo"]((m, scope, states, changed, ready))
        assert bool(result.candidates) == bool(oracle) == exists
        if exists:
            assert result.overall.best_known is not None
            assert (
                result.overall.best_known.evaluation.total_seconds.value
                == min(p[0] for p in oracle)
                > 31 * 86400
            )
