"""Synthetic oracles for an explicitly approved partial consumer transfer model.

These controls establish engine behavior, not a publisher transfer minimum or a
real Moscow pair. ML2 owns source stop/execution/override and policy receipts.
"""

import importlib.util
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from a2_engine import PhysicalDeparture, PhysicalService, TimetableEvent, TransportModeBinding
from a2_engine.routing.timetable import solve_timetable
from a2_engine.scenario.engine import (
    create_scenario_baseline,
    simulate_scenario,
    validate_scenario_result,
)
from a2_engine.scenario.identity import scenario_hash
from a2_engine.scenario.models import ScenarioInputs, ScenarioOperation
from a2_engine.scenario.overlay import execution_hash

POLICY = "same_exact_gtfs_stop_transfer_180s_v1"
spec = importlib.util.spec_from_file_location(
    "same_stop_policy_fixture",
    Path(__file__).resolve().parents[1] / "examples/synthetic_scenario.py",
)
assert spec and spec.loader
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def policy_inputs() -> tuple[ScenarioInputs, str, str, str]:
    inputs, incoming_id, outgoing_id = fixture.demo_inputs()
    dataset = inputs.timetables[0]
    source = next(r for r in dataset.timetable.runs if r.run_id == outgoing_id)
    outgoing = replace(
        source,
        events=(
            source.events[0],
            TimetableEvent(
                1,
                "M",
                fixture.START + timedelta(seconds=1000),
                fixture.START + timedelta(seconds=1010),
                True,
                True,
                fixture.PROOF,
            ),
            replace(source.events[-1], sequence=2),
        ),
    )
    alias = replace(outgoing, trip_id="accepted-outgoing-alias")
    group = PhysicalService(
        "accepted-outgoing-physical-execution",
        (outgoing.run_id, alias.run_id),
        (
            PhysicalDeparture(0, "accepted-departure-X", fixture.PROOF),
            PhysicalDeparture(1, "accepted-departure-M", fixture.PROOF),
        ),
        fixture.PROOF,
    )
    policy = replace(
        dataset.timetable.transfers[0],
        rule_id=POLICY + ":X",
        minimum_seconds=180,
        evidence_refs=(POLICY, "synthetic-source-stop-and-policy-receipt"),
    )
    table = replace(
        dataset.timetable,
        scope=replace(
            dataset.timetable.scope,
            node_ids=dataset.timetable.scope.node_ids | {"M"},
            transfer_version=POLICY,
            transfers_complete=False,
            limitations=(POLICY, "CONSUMER_TRANSFER_BUFFER_ASSUMPTION"),
        ),
        runs=tuple(outgoing if r.run_id == outgoing_id else r for r in dataset.timetable.runs)
        + (alias,),
        transfers=(policy,),
    )
    return (
        replace(
            inputs,
            timetables=(
                replace(
                    dataset,
                    timetable=table,
                    transport_modes=(TransportModeBinding("bus", "road", fixture.PROOF),),
                    physical_services=(group,),
                ),
            ),
        ),
        incoming_id,
        outgoing_id,
        alias.run_id,
    )


def baseline_operation(inputs: ScenarioInputs, incoming: str, outgoing: str, shift: int) -> Any:
    baseline = create_scenario_baseline(inputs)
    return baseline, ScenarioOperation(
        baseline.baseline_id,
        "same-stop-policy-operation",
        "coordinate_transfer",
        "timetable",
        "network",
        outgoing,
        date(2026, 11, 9),
        "0",
        execution_hash(baseline, "timetable", "network", outgoing),
        shift,
        incoming,
        "1",
        (POLICY,),
    )


@pytest.mark.parametrize("gap", [179, 180])
def test_same_stop_policy_boundary_matches_independent_two_vehicle_oracle(gap: int) -> None:
    inputs, incoming, outgoing, alias = policy_inputs()
    baseline, operation = baseline_operation(inputs, incoming, outgoing, gap - 120)
    before = scenario_hash(baseline)
    # Arrival 600, source departure 720, ride 600: this scalar oracle does not
    # use the engine's transfer search or derive a zero transfer duration.
    expected = None if gap < 180 else 600 + gap + 600
    table = inputs.timetables[0].timetable
    shifted = replace(
        table,
        runs=tuple(
            replace(
                run,
                events=tuple(
                    replace(
                        event,
                        arrival_at=event.arrival_at + timedelta(seconds=gap - 120),
                        departure_at=event.departure_at + timedelta(seconds=gap - 120),
                    )
                    for event in run.events
                ),
            )
            if run.run_id in (outgoing, alias)
            else run
            for run in table.runs
        ),
    )
    independent_rerun = solve_timetable(shifted, "S", "D", fixture.START, max_wait_seconds=14400)
    assert (
        (independent_rerun.best_known.arrival_at - fixture.START).total_seconds()
        if independent_rerun.best_known
        else None
    ) == expected
    if expected is None:
        with pytest.raises(ValueError, match="SCENARIO_DOES_NOT_COORDINATE_TRANSFER"):
            simulate_scenario(baseline, operation)
    else:
        result = simulate_scenario(baseline, operation)
        found = solve_timetable(
            result.overlay.inputs.timetables[0].timetable,
            "S",
            "D",
            fixture.START,
            max_wait_seconds=14400,
        )
        assert found.best_known is not None
        assert (found.best_known.arrival_at - fixture.START).total_seconds() == expected
        assert not found.proven and not result.overlay.inputs.timetables[0].timetable.footpaths
        assert result.feasibility.state == "unverified"
        assert not result.overlay.inputs.timetables[0].timetable.scope.transfers_complete
    assert scenario_hash(baseline) == before


@pytest.mark.parametrize(
    "attack",
    ["different_stop", "pickup", "dropoff", "incoming_calendar", "outgoing_calendar", "date"],
)
def test_same_stop_policy_does_not_admit_coincidence_or_unconfirmed_events(attack: str) -> None:
    inputs, incoming, outgoing, _ = policy_inputs()
    dataset = inputs.timetables[0]
    runs = []
    # Equal geometry is deliberately irrelevant: Y is a different published ID.
    coordinates = {"X": (37.7, 55.6), "Y": (37.7, 55.6)}
    assert coordinates["X"] == coordinates["Y"]
    for run in dataset.timetable.runs:
        if run.run_id == incoming and attack in ("dropoff", "incoming_calendar"):
            run = (
                replace(run, events=(run.events[0], replace(run.events[1], dropoff=False)))
                if attack == "dropoff"
                else replace(run, calendar_evidence_refs=())
            )
        if run.run_id == outgoing:
            if attack == "different_stop":
                run = replace(run, events=(replace(run.events[0], node_id="Y"), *run.events[1:]))
            elif attack == "pickup":
                run = replace(run, events=(replace(run.events[0], pickup=False), *run.events[1:]))
            elif attack == "outgoing_calendar":
                run = replace(run, active=None)
        runs.append(run)
    # Remove aliases for this focused event attack; source-ID checks must not be
    # confused with an independently invalid physical alias shape.
    alias_id = dataset.physical_services[0].run_ids
    table = replace(
        dataset.timetable,
        scope=replace(dataset.timetable.scope, node_ids=dataset.timetable.scope.node_ids | {"Y"}),
        runs=tuple(r for r in runs if r.run_id not in alias_id or r.run_id == outgoing),
    )
    inputs = replace(inputs, timetables=(replace(dataset, timetable=table, physical_services=()),))
    baseline, operation = baseline_operation(inputs, incoming, outgoing, 60)
    if attack == "date":
        operation = replace(operation, service_date=date(2026, 11, 10))
    reason = {
        "different_stop": "ACCEPTED_TRANSFER_RULE_REQUIRED",
        "pickup": "TRANSFER_PICKUP_DROPOFF_REQUIRED",
        "dropoff": "TRANSFER_PICKUP_DROPOFF_REQUIRED",
        "incoming_calendar": "REAL_TRANSFER_PAIR_REQUIRED",
        "outgoing_calendar": "SCENARIO_EXECUTION_CALENDAR_MISMATCH",
        "date": "SCENARIO_SERVICE_DATE_MISMATCH",
    }[attack]
    with pytest.raises(ValueError, match=reason):
        simulate_scenario(baseline, operation)


@pytest.mark.parametrize("missing", ["forbidden", "unknown", "minimum", "evidence"])
def test_same_stop_policy_does_not_override_a_block_or_missing_proof(missing: str) -> None:
    inputs, incoming, outgoing, _ = policy_inputs()
    dataset = inputs.timetables[0]
    changes: dict[str, dict[str, Any]] = {
        "forbidden": {"allowed": False},
        "unknown": {"allowed": None},
        "minimum": {"minimum_seconds": None},
        "evidence": {"evidence_refs": ()},
    }
    inputs = replace(
        inputs,
        timetables=(
            replace(
                dataset,
                timetable=replace(
                    dataset.timetable,
                    transfers=(replace(dataset.timetable.transfers[0], **changes[missing]),),
                ),
            ),
        ),
    )
    baseline, operation = baseline_operation(inputs, incoming, outgoing, 60)
    with pytest.raises(ValueError, match="ACCEPTED_TRANSFER_RULE_REQUIRED"):
        simulate_scenario(baseline, operation)


def test_same_stop_whole_execution_shift_keeps_all_stops_aliases_and_actual_worsening() -> None:
    inputs, incoming, outgoing, alias = policy_inputs()
    baseline, operation = baseline_operation(inputs, incoming, outgoing, 60)
    before = scenario_hash(baseline)
    result = simulate_scenario(baseline, operation)
    old = {r.run_id: r for r in baseline.inputs.timetables[0].timetable.runs}
    assert set(result.overlay.affected_execution_ids) == {outgoing, alias}
    for run in result.overlay.inputs.timetables[0].timetable.runs:
        original = old[run.run_id]
        for a, b in zip(original.events, run.events, strict=True):
            delta = timedelta(seconds=60 if run.run_id in (outgoing, alias) else 0)
            assert b.arrival_at == a.arrival_at + delta
            assert b.departure_at == a.departure_at + delta
            assert (b.sequence, b.node_id, b.pickup, b.dropoff) == (
                a.sequence,
                a.node_id,
                a.pickup,
                a.dropoff,
            )
    previous = next(
        p for p in result.before.urban[0].analysis.modes[0].points if p.point.point_id == "p2"
    )
    following = next(
        p for p in result.after.urban[0].analysis.modes[0].points if p.point.point_id == "p2"
    )
    assert previous.categories[0].best and following.categories[0].best
    assert previous.categories[0].best.journey.total_seconds == 1320
    assert following.categories[0].best.journey.total_seconds == 1380
    # The observed route worsens 60s; partial data must not promote that into a
    # certified category-minimum delta or conceal either rich before/after path.
    assert following.categories[0].index.value is None
    assert not result.complete and any(e.outcome == "unknown" for e in result.effects)
    assert result.before.urban[0].analysis.h_seconds == result.after.urban[0].analysis.h_seconds
    assert result.feasibility.state == "unverified"
    validate_scenario_result(baseline, operation, result)
    assert scenario_hash(baseline) == before
    assert simulate_scenario(baseline, operation) == result
    with pytest.raises(ValueError, match="PHYSICAL_ALIAS_IS_NOT_A_TRANSFER"):
        simulate_scenario(baseline, replace(operation, incoming_execution_id=alias))
