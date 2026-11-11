"""Scenario regressions across accepted physical extracts and transfer endpoints."""

import runpy
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from a2_engine import (
    PhysicalDeparture,
    PhysicalService,
    TimetableFootpath,
    TimetableRun,
    TimetableTransfer,
)
from a2_engine.scenario.engine import (
    create_scenario_baseline,
    simulate_scenario,
    validate_scenario_result,
)
from a2_engine.scenario.feasibility import check_scenario_feasibility
from a2_engine.scenario.identity import scenario_hash
from a2_engine.scenario.models import (
    ScenarioBaseline,
    ScenarioFeasibilityInputs,
    ScenarioInputs,
    ScenarioOperation,
    ScenarioResourceBlock,
    ScenarioResourceLink,
)
from a2_engine.scenario.overlay import apply_scenario_overlay, execution_hash

DEMO: dict[str, Any] = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "examples" / "synthetic_scenario.py")
)
PROOF = DEMO["PROOF"]


def demo_inputs() -> tuple[ScenarioInputs, str, str]:
    return cast(tuple[ScenarioInputs, str, str], DEMO["demo_inputs"]())


def operation_for(
    baseline: ScenarioBaseline, incoming: str, outgoing: TimetableRun
) -> ScenarioOperation:
    return ScenarioOperation(
        baseline.baseline_id,
        "test-transfer",
        "coordinate_transfer",
        "timetable",
        "network",
        outgoing.run_id,
        outgoing.service_date,
        "0",
        execution_hash(baseline, "timetable", "network", outgoing.run_id),
        180,
        incoming,
        "1",
        PROOF,
    )


def shifted(run: TimetableRun, seconds: int) -> TimetableRun:
    offset = timedelta(seconds=seconds)
    return replace(
        run,
        events=tuple(
            replace(
                event,
                arrival_at=event.arrival_at + offset,
                departure_at=event.departure_at + offset,
            )
            for event in run.events
        ),
    )


@pytest.mark.parametrize("additional", [False, True])
def test_same_physical_identity_updates_disjoint_extracts(additional: bool) -> None:
    inputs, incoming, outgoing = demo_inputs()
    network = inputs.timetables[0]
    table = network.timetable
    source = next(run for run in table.runs if run.run_id == outgoing)
    feed_alias = replace(source, source_id="second-accepted-feed")
    identity = PhysicalService(
        "physical-outgoing",
        (outgoing,),
        (PhysicalDeparture(0, "physical-boarding", PROOF),),
        PROOF,
    )
    rule = replace(table.transfers[0], from_run_id=None, to_run_id=None)
    network = replace(
        network, timetable=replace(table, transfers=(rule,)), physical_services=(identity,)
    )
    alias_network = replace(
        network,
        network_id="second-extract",
        timetable=replace(
            network.timetable, runs=tuple(feed_alias if r == source else r for r in table.runs)
        ),
        physical_services=(replace(identity, run_ids=(feed_alias.run_id,)),),
    )
    other_date = replace(
        shifted(source, 86400), service_date=source.service_date + timedelta(days=1)
    )
    date_network = replace(
        network,
        network_id="next-date",
        timetable=replace(network.timetable, runs=(other_date,)),
        physical_services=(replace(identity, run_ids=(other_date.run_id,)),),
    )
    inputs = replace(
        inputs,
        timetables=(network, alias_network, date_network),
        urban=inputs.urban
        + (replace(inputs.urban[0], job_id="urban-second", network_id="second-extract"),),
    )
    baseline = create_scenario_baseline(inputs)
    frozen_hash = scenario_hash(baseline)
    operation = operation_for(baseline, incoming, source)
    feasibility = None
    if additional:
        new = replace(shifted(source, 180), trip_id="accepted-extra-trip")
        operation = replace(
            operation,
            template_id="additional_departure",
            new_run=new,
            new_physical_service=PhysicalService(
                "physical-extra-trip",
                (new.run_id,),
                (PhysicalDeparture(0, "physical-extra-boarding", PROOF),),
                PROOF,
            ),
            demand_evidence_refs=PROOF,
        )
        block = ScenarioResourceBlock(
            "extra-vehicle",
            "network",
            (new.run_id,),
            table.scope.start_at,
            table.scope.end_at,
            (),
            PROOF,
            0,
            0,
        )
        feasibility = ScenarioFeasibilityInputs(
            baseline.baseline_id,
            scenario_hash(operation),
            (block,),
            (new.run_id,),
            True,
            True,
            True,
            table.scope.start_at,
            table.scope.end_at,
            PROOF,
        )

    result = simulate_scenario(baseline, operation, feasibility=feasibility)
    affected_networks = {row.network_id for row in result.overlay.changed_events}
    assert affected_networks == {"network", "second-extract"}
    assert scenario_hash(baseline) == frozen_hash
    assert (
        next(table for table in result.overlay.inputs.timetables if table.network_id == "next-date")
        == date_network
    )
    after_time = {
        measure.key[0]: measure.value
        for measure in result.after.metrics
        if measure.key[1:] == ("point", "p1", "school", "public_transport", "time")
    }
    assert after_time == {"urban": 1500, "urban-second": 1500}
    validate_scenario_result(baseline, operation, result, feasibility=feasibility)


@pytest.mark.parametrize("terminal", ["incoming-first", "outgoing-last"])
def test_transfer_requires_real_incoming_and_outgoing_legs(terminal: str) -> None:
    inputs, incoming, outgoing = demo_inputs()
    network = inputs.timetables[0]
    table = network.timetable
    incoming_run = next(run for run in table.runs if run.run_id == incoming)
    outgoing_run = next(run for run in table.runs if run.run_id == outgoing)
    i, j = (0, 0) if terminal == "incoming-first" else (1, 1)
    rule = TimetableTransfer(
        "synthetic-nonexistent-leg",
        incoming_run.events[i].node_id,
        outgoing_run.events[j].node_id,
        0,
        True,
        PROOF,
        incoming,
        outgoing,
    )
    # A proven walk and a generous gap alone cannot make either terminal a transfer.
    table = replace(
        table,
        transfers=(rule,),
        footpaths=(TimetableFootpath("walk", rule.from_node_id, rule.to_node_id, 0, True, PROOF),),
    )
    baseline = create_scenario_baseline(
        replace(inputs, timetables=(replace(network, timetable=table),))
    )
    operation = replace(
        operation_for(baseline, incoming, outgoing_run), incoming_event_id=str(i), event_id=str(j)
    )
    with pytest.raises(ValueError, match="TRANSFER_PICKUP_DROPOFF_REQUIRED"):
        simulate_scenario(baseline, operation)


def test_transfer_honours_run_specific_rules_on_physical_aliases() -> None:
    inputs, incoming, outgoing = demo_inputs()
    network = inputs.timetables[0]
    table = network.timetable
    source = next(run for run in table.runs if run.run_id == outgoing)
    alias = replace(source, trip_id="another-feed-trip")
    group = PhysicalService("same-vehicle-trip", (source.run_id, alias.run_id), (), PROOF)
    rule = table.transfers[0]
    generic = replace(rule, from_run_id=None, to_run_id=None)
    denial = replace(rule, rule_id="denial-on-alias", to_run_id=alias.run_id, allowed=False)
    baseline = create_scenario_baseline(
        replace(
            inputs,
            timetables=(
                replace(
                    network,
                    timetable=replace(
                        table, runs=table.runs + (alias,), transfers=(generic, denial)
                    ),
                    physical_services=(group,),
                ),
            ),
        )
    )
    with pytest.raises(ValueError, match="ACCEPTED_TRANSFER_RULE_REQUIRED"):
        simulate_scenario(baseline, operation_for(baseline, incoming, source))


def test_partial_alias_cannot_erase_confirmed_cross_network_resource_overlap() -> None:
    inputs, incoming, outgoing = demo_inputs()
    network = inputs.timetables[0]
    table = network.timetable
    source = next(run for run in table.runs if run.run_id == outgoing)
    conflict = replace(shifted(source, 480), trip_id="other-vehicle-assignment")
    partial_alias = replace(conflict, source_id="partial-feed", calendar_evidence_refs=())
    identity = PhysicalService("physical-other-assignment", (conflict.run_id,), (), PROOF)
    inputs = replace(
        inputs,
        timetables=(
            network,
            replace(
                network,
                network_id="other-assignment",
                timetable=replace(table, runs=(conflict,), transfers=()),
                physical_services=(identity,),
            ),
            replace(
                network,
                network_id="z-partial-feed",
                timetable=replace(table, runs=(partial_alias,), transfers=()),
                physical_services=(replace(identity, run_ids=(partial_alias.run_id,)),),
            ),
        ),
    )
    baseline = create_scenario_baseline(inputs)
    operation = operation_for(baseline, incoming, source)
    blocks = tuple(
        ScenarioResourceBlock(
            "shared-vehicle",
            network_id,
            (run_id,),
            table.scope.start_at,
            table.scope.end_at,
            (),
            PROOF,
            0,
            0,
        )
        for network_id, run_id in (
            ("network", outgoing),
            ("other-assignment", conflict.run_id),
            ("z-partial-feed", partial_alias.run_id),
        )
    )
    proof = ScenarioFeasibilityInputs(
        baseline.baseline_id,
        scenario_hash(operation),
        blocks,
        (outgoing,),
        True,
        True,
        True,
        table.scope.start_at,
        table.scope.end_at,
        PROOF,
    )
    result = simulate_scenario(baseline, operation, feasibility=proof)
    assert result.feasibility.state == "infeasible"
    assert "RESOURCE_EXECUTIONS_OVERLAP" in result.feasibility.reasons


@pytest.mark.parametrize("terminal", ["initial", "final"])
def test_additional_departure_rejects_vehicle_overlap_during_terminal_dwell(terminal: str) -> None:
    inputs, incoming, outgoing = demo_inputs()
    network = inputs.timetables[0]
    table = network.timetable
    source = next(run for run in table.runs if run.run_id == outgoing)
    first, last = source.events
    minute = timedelta(minutes=1)
    if terminal == "initial":
        first = replace(first, arrival_at=first.arrival_at - minute)
    else:
        last = replace(last, departure_at=last.departure_at + minute)
    source = replace(source, events=(first, last))
    extra = replace(shifted(source, 180), trip_id="extra-physical-departure")
    if terminal == "initial":
        busy_end = extra.events[0].arrival_at + minute / 2
        busy_start = busy_end - minute
        preceding, following = "busy", "extra"
    else:
        busy_start = extra.events[-1].arrival_at + minute / 2
        busy_end = busy_start + minute
        preceding, following = "extra", "busy"
    busy = replace(
        source,
        trip_id="concurrent-resource-execution",
        events=(
            replace(first, arrival_at=busy_start, departure_at=busy_start),
            replace(last, arrival_at=busy_end, departure_at=busy_end),
        ),
    )
    roster = tuple(source if run.run_id == outgoing else run for run in table.runs) + (busy,)
    baseline = create_scenario_baseline(
        replace(inputs, timetables=(replace(network, timetable=replace(table, runs=roster)),))
    )
    operation = replace(
        operation_for(baseline, incoming, source),
        template_id="additional_departure",
        new_run=extra,
        new_physical_service=PhysicalService(
            "extra-physical-departure",
            (extra.run_id,),
            (PhysicalDeparture(0, "extra-physical-boarding", PROOF),),
            PROOF,
        ),
        demand_evidence_refs=PROOF,
    )
    run_ids = {"busy": busy.run_id, "extra": extra.run_id}
    block = ScenarioResourceBlock(
        "accepted-vehicle",
        "network",
        (busy.run_id, extra.run_id),
        table.scope.start_at,
        table.scope.end_at,
        (ScenarioResourceLink(run_ids[preceding], run_ids[following], 0, PROOF),),
        PROOF,
        0,
        0,
    )
    proof = ScenarioFeasibilityInputs(
        baseline.baseline_id,
        scenario_hash(operation),
        (block,),
        (extra.run_id,),
        True,
        True,
        True,
        table.scope.start_at,
        table.scope.end_at,
        PROOF,
    )
    overlay = apply_scenario_overlay(baseline, operation)
    feasibility = check_scenario_feasibility(baseline, operation, overlay, proof)
    assert feasibility.state == "infeasible"
    assert "RESOURCE_EXECUTIONS_OVERLAP" in feasibility.reasons
    with pytest.raises(ValueError, match="ADDITIONAL_DEPARTURE_REQUIRES_VERIFIED_RESOURCE"):
        simulate_scenario(baseline, operation, feasibility=proof)


def test_cross_network_turnaround_link_uses_accepted_physical_identity() -> None:
    inputs, incoming, outgoing = demo_inputs()
    network = inputs.timetables[0]
    table = network.timetable
    source = next(run for run in table.runs if run.run_id == outgoing)
    next_run = replace(shifted(source, 1200), trip_id="other-vehicle-assignment")
    next_alias = replace(next_run, source_id="other-feed")
    identity = PhysicalService("physical-next-assignment", (next_run.run_id,), (), PROOF)
    inputs = replace(
        inputs,
        timetables=(
            network,
            replace(
                network,
                network_id="other-assignment",
                timetable=replace(table, runs=(next_run,), transfers=()),
                physical_services=(identity,),
            ),
            replace(
                network,
                network_id="z-other-feed",
                timetable=replace(table, runs=(next_alias,), transfers=()),
                physical_services=(replace(identity, run_ids=(next_alias.run_id,)),),
            ),
        ),
    )
    baseline = create_scenario_baseline(inputs)
    operation = operation_for(baseline, incoming, source)
    link = ScenarioResourceLink(outgoing, next_run.run_id, 420, ("accepted-turnaround",))
    blocks = tuple(
        ScenarioResourceBlock(
            "shared-vehicle",
            network_id,
            (run_id,),
            table.scope.start_at,
            table.scope.end_at,
            links,
            PROOF,
            0,
            0,
        )
        for network_id, run_id, links in (
            ("network", outgoing, (link,)),
            ("other-assignment", next_run.run_id, ()),
            ("z-other-feed", next_alias.run_id, ()),
        )
    )
    proof = ScenarioFeasibilityInputs(
        baseline.baseline_id,
        scenario_hash(operation),
        blocks,
        (outgoing,),
        True,
        True,
        True,
        table.scope.start_at,
        table.scope.end_at,
        PROOF,
    )
    result = simulate_scenario(baseline, operation, feasibility=proof)
    assert result.feasibility.state == "feasible"
    assert "accepted-turnaround" in result.feasibility.evidence_refs
