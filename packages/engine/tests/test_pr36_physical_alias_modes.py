"""Scenario aliases must agree on their accepted movement mode across extracts."""

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from runpy import run_path
from typing import cast

import pytest
from a2_engine import (
    PhysicalDeparture,
    PhysicalService,
    ScenarioBaseline,
    ScenarioFeasibilityInputs,
    ScenarioInputs,
    ScenarioOperation,
    ScenarioResourceBlock,
    TimetableRun,
    TransportModeBinding,
    apply_scenario_overlay,
    check_scenario_feasibility,
    create_scenario_baseline,
    scenario_hash,
    simulate_scenario,
    validate_scenario_result,
)
from a2_engine.intercity.analysis.identity import normalize_physical_services
from a2_engine.intercity.analysis.models import PublicMode
from a2_engine.scenario.overlay import execution_hash
from a2_engine.scenario.recompute import recompute

DEMO = run_path(str(Path(__file__).resolve().parents[1] / "examples" / "synthetic_scenario.py"))
PROOF = cast(tuple[str, ...], DEMO["PROOF"])
MODE_CONFLICT = "INCONSISTENT_SCENARIO_EXECUTION_MODES"


def extracts(
    alias_mode: PublicMode | None, *, same_run: bool = False
) -> tuple[ScenarioInputs, str, TimetableRun, TimetableRun]:
    inputs, incoming, outgoing = cast(tuple[ScenarioInputs, str, str], DEMO["demo_inputs"]())
    network = inputs.timetables[0]
    table = network.timetable
    source = next(run for run in table.runs if run.run_id == outgoing)
    alias = source if same_run else replace(source, source_id="other-feed", transport_type="tram")
    identity = PhysicalService(
        "physical-outgoing", (source.run_id,), (PhysicalDeparture(0, "boarding", PROOF),), PROOF
    )
    rule = replace(table.transfers[0], from_run_id=None, to_run_id=None)
    primary = replace(
        network,
        timetable=replace(table, transfers=(rule,)),
        transport_modes=(TransportModeBinding(source.transport_type, "road", PROOF),),
        physical_services=(identity,),
    )
    alias_bindings: tuple[TransportModeBinding, ...] = (
        (TransportModeBinding(alias.transport_type, alias_mode, PROOF),)
        if alias_mode is not None
        else ()
    )
    if alias_mode == "road" and not same_run:
        # The same raw label has a different meaning in this provider's feed.
        alias_bindings += (TransportModeBinding(source.transport_type, "rail", PROOF),)
    secondary = replace(
        network,
        network_id="second-extract",
        timetable=replace(
            primary.timetable,
            runs=tuple(
                alias if run == source else replace(run, source_id="other-feed")
                for run in table.runs
            ),
        ),
        transport_modes=alias_bindings,
        physical_services=(replace(identity, run_ids=(alias.run_id,)),) if not same_run else (),
    )
    inputs = replace(
        inputs,
        timetables=(primary, secondary),
        urban=inputs.urban
        + (replace(inputs.urban[0], job_id="urban-second", network_id=secondary.network_id),),
    )
    return inputs, incoming, source, alias


def coordinate(baseline: ScenarioBaseline, incoming: str, run: TimetableRun) -> ScenarioOperation:
    return ScenarioOperation(
        baseline.baseline_id,
        "coordinate-aliases",
        "coordinate_transfer",
        "timetable",
        "network",
        run.run_id,
        run.service_date,
        "0",
        execution_hash(baseline, "timetable", "network", run.run_id),
        180,
        incoming,
        "1",
        PROOF,
    )


def resource_proof(
    baseline: ScenarioBaseline, operation: ScenarioOperation, run: TimetableRun
) -> ScenarioFeasibilityInputs:
    scope = baseline.inputs.timetables[0].timetable.scope
    block = ScenarioResourceBlock(
        "vehicle", "network", (run.run_id,), scope.start_at, scope.end_at, (), PROOF, 0, 0
    )
    return ScenarioFeasibilityInputs(
        baseline.baseline_id,
        scenario_hash(operation),
        (block,),
        (run.run_id,),
        True,
        True,
        True,
        scope.start_at,
        scope.end_at,
        PROOF,
    )


def shifted(run: TimetableRun, offset: timedelta) -> TimetableRun:
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


def extra_departure(operation: ScenarioOperation, source: TimetableRun) -> ScenarioOperation:
    new = replace(shifted(source, timedelta(seconds=operation.shift_seconds)), trip_id="extra-trip")
    return replace(
        operation,
        template_id="additional_departure",
        new_run=new,
        new_physical_service=PhysicalService(
            "new-physical", (new.run_id,), (PhysicalDeparture(0, "new-boarding", PROOF),), PROOF
        ),
        demand_evidence_refs=PROOF,
    )


@pytest.mark.parametrize("same_run", [False, True])
def test_conflicting_known_modes_rejected_across_extracts(same_run: bool) -> None:
    inputs, _, _, _ = extracts("rail", same_run=same_run)
    with pytest.raises(ValueError, match=MODE_CONFLICT):
        create_scenario_baseline(inputs)


def test_unknown_mode_cannot_bridge_conflicting_known_aliases() -> None:
    inputs, _, source, alias = extracts("rail")
    primary, secondary = inputs.timetables
    unknown = replace(primary, network_id="a-unbound-extract", transport_modes=())
    # The first extract supplies the identity for source. Its unbound copy must
    # not hide the contradiction between the two later bindings.
    primary = replace(primary, physical_services=())
    assert source.run_id != alias.run_id
    with pytest.raises(ValueError, match=MODE_CONFLICT):
        create_scenario_baseline(replace(inputs, timetables=(unknown, primary, secondary)))


@pytest.mark.parametrize("different_snapshot", [True, False])
def test_same_physical_id_can_have_different_modes_across_execution_boundaries(
    different_snapshot: bool,
) -> None:
    inputs, incoming, source, alias = extracts("rail")
    primary, secondary = inputs.timetables
    if different_snapshot:
        alias = replace(alias, snapshot_id="later-snapshot")
    else:
        tomorrow = timedelta(days=1)
        alias = replace(shifted(alias, tomorrow), service_date=alias.service_date + tomorrow)
    secondary = replace(
        secondary,
        timetable=replace(
            secondary.timetable,
            runs=(alias,),
            scope=replace(secondary.timetable.scope, snapshot_id=alias.snapshot_id),
        ),
        physical_services=(replace(secondary.physical_services[0], run_ids=(alias.run_id,)),),
    )
    baseline = create_scenario_baseline(
        replace(inputs, timetables=(primary, secondary), urban=inputs.urban[:1])
    )
    result = simulate_scenario(baseline, coordinate(baseline, incoming, source))
    assert result.overlay.affected_execution_ids == (source.run_id,)
    assert result.overlay.inputs.timetables[1] == secondary


@pytest.mark.parametrize("rail_first", [True, False])
@pytest.mark.parametrize("explicit_binding", [True, False])
def test_generated_mode_label_requires_a_matching_provider_binding(
    rail_first: bool, explicit_binding: bool
) -> None:
    inputs, _, source, alias = extracts(None)
    table = inputs.timetables[0].timetable
    alias = replace(alias, transport_type="physical:rail")
    table = replace(table, runs=(source, alias), transfers=())
    services = (
        PhysicalService(
            "a-confirmed" if rail_first else "z-confirmed", (source.run_id,), (), PROOF
        ),
        PhysicalService("m-provider", (alias.run_id,), (), PROOF),
    )
    bindings = (TransportModeBinding(source.transport_type, "rail", PROOF),)
    if not explicit_binding:
        with pytest.raises(ValueError, match="RESERVED_PHYSICAL_TRANSPORT_TYPE"):
            normalize_physical_services(table, bindings, services)
    else:
        normalized, modes, _ = normalize_physical_services(
            table, bindings + (TransportModeBinding(alias.transport_type, "rail", PROOF),), services
        )
        assert [dict(modes)[run.transport_type] for run in normalized.runs] == ["rail", "rail"]


def test_generated_unknown_label_cannot_borrow_an_unrelated_provider_binding() -> None:
    inputs, _, source, _ = extracts(None)
    dataset = inputs.timetables[0]
    with pytest.raises(ValueError, match="RESERVED_PHYSICAL_TRANSPORT_TYPE"):
        normalize_physical_services(
            dataset.timetable,
            (TransportModeBinding("unmapped-physical:" + source.transport_type, "rail", PROOF),),
            dataset.physical_services,
        )


def test_public_entry_points_reject_conflicting_alias_modes() -> None:
    inputs, incoming, source, _ = extracts("rail")
    fingerprint = scenario_hash(inputs)
    before = recompute(inputs, fingerprint)
    forged = ScenarioBaseline(
        "baseline:" + fingerprint, fingerprint, scenario_hash(before), inputs, before
    )
    operation = coordinate(forged, incoming, source)
    for apply in (apply_scenario_overlay, simulate_scenario):
        with pytest.raises(ValueError, match=MODE_CONFLICT):
            apply(forged, operation)

    accepted, _, _, _ = extracts("road")
    baseline = create_scenario_baseline(accepted)
    operation = coordinate(baseline, incoming, source)
    overlay = apply_scenario_overlay(baseline, operation)
    primary, secondary = overlay.inputs.timetables
    secondary = replace(secondary, transport_modes=inputs.timetables[1].transport_modes)
    forged_overlay = replace(
        overlay, inputs=replace(overlay.inputs, timetables=(primary, secondary))
    )
    with pytest.raises(ValueError, match=MODE_CONFLICT):
        check_scenario_feasibility(
            baseline, operation, forged_overlay, resource_proof(baseline, operation, source)
        )


@pytest.mark.parametrize("different_occupancy", [True, False])
def test_approved_departure_coverage_requires_consistent_alias_occupancy(
    different_occupancy: bool,
) -> None:
    inputs, incoming, source, _ = extracts("road")
    baseline = create_scenario_baseline(inputs)
    operation = extra_departure(coordinate(baseline, incoming, source), source)
    assert operation.new_run is not None
    overlay = apply_scenario_overlay(baseline, operation)
    primary, secondary = overlay.inputs.timetables
    local_id = next(
        run_id for run_id in overlay.affected_execution_ids if run_id != operation.new_run.run_id
    )
    local_run = next(run for run in secondary.timetable.runs if run.run_id == local_id)
    forged = (
        shifted(local_run, timedelta(days=10))
        if different_occupancy
        else replace(local_run, calendar_evidence_refs=())
    )
    forged_runs = tuple(
        forged if run.run_id == local_id else run for run in secondary.timetable.runs
    )
    secondary = replace(secondary, timetable=replace(secondary.timetable, runs=forged_runs))
    overlay = replace(overlay, inputs=replace(overlay.inputs, timetables=(primary, secondary)))
    proof = resource_proof(baseline, operation, operation.new_run)
    if different_occupancy:
        with pytest.raises(ValueError, match="INCONSISTENT_SCENARIO_PHYSICAL_EXECUTION_COPIES"):
            check_scenario_feasibility(baseline, operation, overlay, proof)
    else:
        feasibility = check_scenario_feasibility(baseline, operation, overlay, proof)
        assert feasibility.state == "unverified"
        assert "AFFECTED_EXECUTION_COVERAGE_UNVERIFIED" in feasibility.reasons


@pytest.mark.parametrize("additional", [False, True])
@pytest.mark.parametrize("alias_mode", ["road", None])
def test_compatible_alias_modes_preserve_local_types_and_apply_to_both_extracts(
    additional: bool, alias_mode: PublicMode | None
) -> None:
    inputs, incoming, source, alias = extracts(alias_mode)
    baseline = create_scenario_baseline(inputs)
    before_hash = scenario_hash(baseline)
    operation = coordinate(baseline, incoming, source)
    proof = None
    shift = timedelta(seconds=operation.shift_seconds)
    if additional:
        operation = extra_departure(operation, source)
        assert operation.new_run is not None
        proof = resource_proof(baseline, operation, operation.new_run)

    result = simulate_scenario(baseline, operation, feasibility=proof)
    assert {change.network_id for change in result.overlay.changed_events} == {
        "network",
        "second-extract",
    }
    updated_ids = set()
    for dataset, original in zip(result.overlay.inputs.timetables, (source, alias), strict=True):
        run_id = original.run_id
        if operation.new_run is not None:
            physical = next(
                group
                for group in dataset.physical_services
                if group.physical_service_id == "new-physical"
            )
            (run_id,) = physical.run_ids
            if dataset.network_id == "network":
                assert run_id == operation.new_run.run_id
            else:
                assert run_id != operation.new_run.run_id
        updated = next(run for run in dataset.timetable.runs if run.run_id == run_id)
        assert updated.events[0].departure_at == original.events[0].departure_at + shift
        assert updated.transport_type == original.transport_type
        updated_ids.add(run_id)
    assert set(result.overlay.affected_execution_ids) == updated_ids
    assert (
        result.overlay.inputs.timetables[1].transport_modes == inputs.timetables[1].transport_modes
    )
    assert scenario_hash(baseline) == before_hash
    validate_scenario_result(baseline, operation, result, feasibility=proof)
    if operation.new_run is not None:
        assert proof is not None
        assert result.feasibility.state == "feasible"
        (local_id,) = updated_ids - {operation.new_run.run_id}
        shared_block = replace(proof.blocks[0], network_id="second-extract", run_ids=(local_id,))
        both_extracts = replace(proof, blocks=proof.blocks + (shared_block,))
        assert (
            check_scenario_feasibility(baseline, operation, result.overlay, both_extracts).state
            == "feasible"
        )
        later_baseline = create_scenario_baseline(result.overlay.inputs)
        later_operation = coordinate(later_baseline, incoming, operation.new_run)
        later = simulate_scenario(later_baseline, later_operation)
        assert set(later.overlay.affected_execution_ids) == updated_ids
        validate_scenario_result(later_baseline, later_operation, later)
