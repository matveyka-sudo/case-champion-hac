"""Real overlays, independent full reruns and attacks against hidden worsening."""

import importlib.util
import os
import random
import subprocess
import sys
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal, cast

import pytest
from a2_engine import (
    PhysicalDeparture,
    PhysicalService,
    TimetableEvent,
    TransportModeBinding,
)
from a2_engine.comparison.deltas import DeltaResult
from a2_engine.scenario.effects import compare_scenario_metrics, validate_effect_coverage
from a2_engine.scenario.engine import (
    create_scenario_baseline,
    simulate_scenario,
    validate_scenario_result,
)
from a2_engine.scenario.identity import scenario_hash
from a2_engine.scenario.models import (
    ScenarioFeasibilityInputs,
    ScenarioInputs,
    ScenarioMeasure,
    ScenarioOperation,
    ScenarioResourceBlock,
)
from a2_engine.scenario.overlay import apply_scenario_overlay, execution_hash

ENGINE = Path(__file__).resolve().parents[1]


def example(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(
        name, ENGINE / "examples" / f"synthetic_{name}.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


m = example("scenario")


def baseline_with(
    inputs: Any, incoming: str, outgoing: str, delta: int = 180, **kwargs: Any
) -> tuple[Any, Any]:
    baseline = create_scenario_baseline(inputs)
    operation = ScenarioOperation(
        baseline.baseline_id,
        "operation",
        "coordinate_transfer",
        "timetable",
        "network",
        outgoing,
        date(2026, 11, 9),
        "0",
        execution_hash(baseline, "timetable", "network", outgoing),
        delta,
        incoming,
        "1",
        m.PROOF,
        **kwargs,
    )
    return baseline, operation


def proof(
    baseline: Any,
    operation: Any,
    blocks: tuple[ScenarioResourceBlock, ...],
    covered: tuple[str, ...] | None = None,
) -> ScenarioFeasibilityInputs:
    return ScenarioFeasibilityInputs(
        baseline.baseline_id,
        scenario_hash(operation),
        blocks,
        covered or (operation.execution_id,),
        True,
        True,
        True,
        m.START - timedelta(days=1),
        m.START + timedelta(days=10),
        m.PROOF,
    )


def test_real_transfer_overlay_full_effects_districts_population_and_immutability() -> None:
    result = m.run_demo()
    baseline, operation = m.demo_baseline()
    assert result == simulate_scenario(baseline, operation)
    assert result.before.urban[0].analysis.modes[0].points[0].categories[0].best is None
    after = result.after.urban[0].analysis
    assert after.modes[0].points[0].categories[0].best.journey.total_seconds == 1500
    assert after.h_seconds.value == 1200
    assert after.modes[0].points[0].categories[0].threshold.seconds == 1800
    assert result.impact.known_affected_population == 100
    assert len(result.impact.affected_cell_ids) == 2
    assert result.after.urban[0].districts.modes[0].overall.city_index == after.modes[0].index
    assert result.feasibility.state == "unverified" and "Модельный эксперимент" in result.label
    assert any(e.outcome == "worsened" for e in result.effects)
    validate_scenario_result(baseline, operation, result)


def test_signed_comparison_keeps_30_to35_and_60_to40() -> None:
    left = tuple(
        ScenarioMeasure((name,), "seconds", False, value, "known", True, "frozen", "old-route")
        for name, value in (("A", 30), ("B", 60))
    )
    right = tuple(
        replace(metric, value=value, route_id="different-route")
        for metric, value in zip(left, (35, 40), strict=True)
    )
    effects = compare_scenario_metrics(left, right)
    assert [(e.delta.value, e.outcome) for e in effects] == [(-5, "worsened"), (20, "improved")]
    with pytest.raises(ValueError):
        validate_effect_coverage(left, right, effects[1:], ())


@pytest.mark.parametrize(
    "attack",
    [
        "omit",
        "duplicate",
        "metric_id",
        "null",
        "new_connection",
        "unknown_after",
        "drop_after",
        "units",
        "semantic_key",
        "route_id",
        "worsened_ids",
    ],
)
def test_publication_recomputes_and_rejects_hidden_worsening(attack: str) -> None:
    baseline, operation = m.demo_baseline()
    result = simulate_scenario(baseline, operation)
    bad = result
    worsening = next(e for e in result.effects if e.outcome == "worsened")
    if attack == "omit":
        bad = replace(result, effects=tuple(e for e in result.effects if e != worsening))
    elif attack == "duplicate":
        bad = replace(result, effects=result.effects + (worsening,))
    elif attack == "metric_id":
        bad = replace(
            result,
            effects=tuple(
                replace(e, metric_id="other") if e == worsening else e for e in result.effects
            ),
        )
    elif attack in ("null", "new_connection"):
        bad = replace(
            result,
            effects=tuple(
                replace(
                    e,
                    delta=DeltaResult(None, "unknown", "fabricated"),
                    outcome="new_connection" if attack == "new_connection" else "unknown",
                )
                if e == worsening
                else e
                for e in result.effects
            ),
        )
    elif attack == "worsened_ids":
        bad = replace(result, worsened_metric_ids=())
    else:
        values = list(result.after.metrics)
        i = next(i for i, value in enumerate(values) if value.key == worsening.key)
        if attack == "drop_after":
            values.pop(i)
        else:
            changes: dict[str, Any] = {}
            if attack == "unknown_after":
                changes = dict(state="unknown", value=None, proven=False)
            if attack == "units":
                changes = dict(unit="count")
            if attack == "semantic_key":
                changes = dict(key=("other-job",) + values[i].key[1:])
            if attack == "route_id":
                changes = dict(route_id="hide-behind-new-route")
            values[i] = replace(values[i], **changes)
        bad = replace(result, after=replace(result.after, metrics=tuple(values)))
        if attack == "unknown_after":
            # Even a self-consistent partial report cannot replace a known loss
            # with unknown. The engine reconstructs actual after from inputs.
            effects = compare_scenario_metrics(result.before.metrics, bad.after.metrics)
            bad = replace(
                bad,
                effects=effects,
                complete=False,
                worsened_metric_ids=tuple(
                    sorted(
                        e.metric_id for e in effects if e.outcome in ("worsened", "lost_connection")
                    )
                ),
            )
    with pytest.raises(ValueError):
        validate_scenario_result(baseline, operation, bad)


def test_unknown_to_known_never_claimed_as_new_connection() -> None:
    unknown = ScenarioMeasure(("metric",), "seconds", False, None, "unknown", False, "scope")
    known = replace(unknown, value=30, state="known", proven=True)
    assert compare_scenario_metrics((unknown,), (known,))[0].outcome == "unknown"
    assert compare_scenario_metrics((known,), (unknown,))[0].outcome == "unknown"
    absent = replace(unknown, state="absent", proven=True)
    assert compare_scenario_metrics((absent,), (known,))[0].outcome == "new_connection"
    assert compare_scenario_metrics((known,), (absent,))[0].outcome == "lost_connection"
    assert compare_scenario_metrics((known,), (absent,))[0].delta.value is None


def test_tau_fixed_1500_to900_count_and_weighted_territory() -> None:
    inputs, incoming, outgoing = m.demo_inputs()
    ds = inputs.timetables[0]
    runs = []
    for run in ds.timetable.runs:
        change = -600 if run.run_id == incoming else 180
        runs.append(
            replace(
                run,
                events=tuple(
                    replace(
                        e,
                        arrival_at=e.arrival_at + timedelta(seconds=change),
                        departure_at=e.departure_at + timedelta(seconds=change),
                    )
                    for e in run.events
                ),
            )
        )
    job = inputs.urban[0]
    threshold = replace(job.thresholds[0], seconds=1200)
    inputs = replace(
        inputs,
        timetables=(replace(ds, timetable=replace(ds.timetable, runs=tuple(runs))),),
        urban=(replace(job, thresholds=(threshold,)),),
    )
    baseline, operation = baseline_with(inputs, incoming, outgoing, -600)
    result = simulate_scenario(baseline, operation)
    before_point = result.before.urban[0].analysis.modes[0].points[1]
    after_point = result.after.urban[0].analysis.modes[0].points[1]
    assert (
        before_point.categories[0].best is not None and after_point.categories[0].best is not None
    )
    assert before_point.categories[0].best.journey.total_seconds == 1500
    assert after_point.categories[0].best.journey.total_seconds == 900
    assert (
        before_point.categories[0].count.value == 0 and after_point.categories[0].count.value == 1
    )
    assert after_point.categories[0].threshold is threshold
    assert result.impact.known_affected_population == 10
    value = after_point.index.value
    assert value is not None
    assert result.after.urban[0].analysis.modes[0].index.value == pytest.approx(value / 10)


def test_tail_model_updates_only_dependents_and_bad_dwell_is_rejected() -> None:
    inputs, incoming, outgoing = m.demo_inputs()
    ds = inputs.timetables[0]
    run = next(r for r in ds.timetable.runs if r.run_id == outgoing)
    # Anchor is an intermediate stop, with a known incoming movement and dwell.
    events = (
        TimetableEvent(0, "S", m.START, m.START, True, True, m.PROOF),
        TimetableEvent(
            1,
            "X",
            m.START + timedelta(seconds=600),
            m.START + timedelta(seconds=720),
            True,
            True,
            m.PROOF,
        ),
        replace(run.events[-1], sequence=2),
    )
    run = replace(run, events=events)
    inputs = replace(
        inputs,
        timetables=(
            replace(
                ds,
                timetable=replace(
                    ds.timetable,
                    runs=tuple(run if r.run_id == outgoing else r for r in ds.timetable.runs),
                ),
            ),
        ),
    )
    baseline, original = baseline_with(inputs, incoming, outgoing)
    operation = replace(original, event_id="1", timing_model="departure_tail")
    overlay = apply_scenario_overlay(baseline, operation)
    changed = next(r for r in overlay.inputs.timetables[0].timetable.runs if r.run_id == outgoing)
    assert changed.events[0] == run.events[0]
    assert changed.events[1].arrival_at == run.events[1].arrival_at
    assert changed.events[1].departure_at == run.events[1].departure_at + timedelta(seconds=180)
    assert changed.events[2].arrival_at == run.events[2].arrival_at + timedelta(seconds=180)
    with pytest.raises(ValueError, match="DWELL"):
        apply_scenario_overlay(baseline, replace(operation, shift_seconds=-121))


def test_only_concrete_service_date_is_shifted_and_overlay_is_idempotent() -> None:
    inputs, incoming, outgoing = m.demo_inputs()
    ds = inputs.timetables[0]
    source = next(r for r in ds.timetable.runs if r.run_id == outgoing)
    tomorrow = replace(
        source,
        service_date=source.service_date + timedelta(days=1),
        events=tuple(
            replace(
                e,
                arrival_at=e.arrival_at + timedelta(days=1),
                departure_at=e.departure_at + timedelta(days=1),
            )
            for e in source.events
        ),
    )
    inputs = replace(
        inputs,
        timetables=(
            replace(ds, timetable=replace(ds.timetable, runs=ds.timetable.runs + (tomorrow,))),
        ),
    )
    baseline, operation = baseline_with(inputs, incoming, outgoing)
    before = scenario_hash(baseline)
    overlay = apply_scenario_overlay(baseline, operation)
    assert apply_scenario_overlay(baseline, operation) == overlay
    assert (
        next(r for r in overlay.inputs.timetables[0].timetable.runs if r.run_id == tomorrow.run_id)
        == tomorrow
    )
    with pytest.raises(ValueError):
        apply_scenario_overlay(cast(Any, overlay), operation)
    assert scenario_hash(baseline) == before
    with pytest.raises(ValueError):
        simulate_scenario(baseline, replace(operation, service_date=tomorrow.service_date))


def test_resource_states_unverified_feasible_and_confirmed_overlap() -> None:
    baseline, operation = m.demo_baseline()
    block = ScenarioResourceBlock(
        "vehicle",
        "network",
        (operation.execution_id,),
        m.START - timedelta(hours=1),
        m.START + timedelta(days=1),
        (),
        m.PROOF,
        0,
        0,
    )
    verified = simulate_scenario(
        baseline, operation, feasibility=proof(baseline, operation, (block,))
    )
    assert verified.feasibility.state == "feasible"
    unknown = simulate_scenario(
        baseline,
        operation,
        feasibility=proof(baseline, operation, (replace(block, minimum_before_seconds=None),)),
    )
    assert unknown.feasibility.state == "unverified"
    inputs, incoming, outgoing = m.demo_inputs()
    ds = inputs.timetables[0]
    source = next(r for r in ds.timetable.runs if r.run_id == outgoing)
    busy = replace(
        source,
        trip_id="other-execution",
        events=tuple(
            replace(
                e,
                arrival_at=e.arrival_at + timedelta(seconds=480),
                departure_at=e.departure_at + timedelta(seconds=480),
            )
            for e in source.events
        ),
    )
    inputs = replace(
        inputs,
        timetables=(
            replace(ds, timetable=replace(ds.timetable, runs=ds.timetable.runs + (busy,))),
        ),
    )
    baseline, operation = baseline_with(inputs, incoming, outgoing)
    block = replace(block, run_ids=(outgoing, busy.run_id))
    result = simulate_scenario(
        baseline, operation, feasibility=proof(baseline, operation, (block,))
    )
    assert result.feasibility.state == "infeasible"
    assert "RESOURCE_EXECUTIONS_OVERLAP" in result.feasibility.reasons


def test_partial_inputs_never_create_proven_connections_or_full_impact() -> None:
    inputs, incoming, outgoing = m.demo_inputs()
    ds = inputs.timetables[0]
    inputs = replace(
        inputs,
        timetables=(
            replace(
                ds,
                timetable=replace(
                    ds.timetable, scope=replace(ds.timetable.scope, continuations_complete=False)
                ),
            ),
        ),
    )
    baseline, operation = baseline_with(inputs, incoming, outgoing)
    result = simulate_scenario(baseline, operation)
    assert not result.complete and any(e.outcome == "unknown" for e in result.effects)
    assert not any(e.outcome == "new_connection" for e in result.effects)
    assert not result.impact.population_complete


def test_aliases_shift_together_without_fake_transfers() -> None:
    inputs, incoming, outgoing = m.demo_inputs()
    ds = inputs.timetables[0]
    source = next(r for r in ds.timetable.runs if r.run_id == outgoing)
    alias = replace(source, trip_id="marketing-alias")
    service = PhysicalService(
        "physical-outgoing",
        (source.run_id, alias.run_id),
        (PhysicalDeparture(0, "physical-departure", m.PROOF),),
        m.PROOF,
    )
    inputs = replace(
        inputs,
        timetables=(
            replace(
                ds,
                timetable=replace(ds.timetable, runs=ds.timetable.runs + (alias,)),
                transport_modes=(TransportModeBinding("bus", "road", m.PROOF),),
                physical_services=(service,),
            ),
        ),
    )
    baseline, operation = baseline_with(inputs, incoming, outgoing)
    result = simulate_scenario(baseline, operation)
    assert set(result.overlay.affected_execution_ids) == {outgoing, alias.run_id}
    changed = [
        r
        for r in result.overlay.inputs.timetables[0].timetable.runs
        if r.run_id in (outgoing, alias.run_id)
    ]
    assert changed[0].events == changed[1].events
    assert result.complete and result.impact.known_affected_population == 100


def test_allowlist_scope_hash_and_forged_baseline_protection() -> None:
    baseline, operation = m.demo_baseline()
    original = scenario_hash(baseline)
    for bad in (
        replace(operation, template_id="change_handling_duration"),
        replace(operation, template_id="pipeline_capacity"),
        replace(operation, baseline_id="wrong"),
        replace(operation, expected_execution_hash="other"),
    ):
        with pytest.raises(ValueError):
            simulate_scenario(baseline, bad)
    fake_metrics = (
        replace(baseline.before.metrics[0], value=99, state="known"),
    ) + baseline.before.metrics[1:]
    fake_before = replace(baseline.before, metrics=fake_metrics)
    with pytest.raises(ValueError, match="BASELINE_RESULT"):
        simulate_scenario(
            replace(baseline, before=fake_before, result_hash=scenario_hash(fake_before)), operation
        )
    assert scenario_hash(baseline) == original


def test_independent_manual_full_recompute_on_small_schedules() -> None:
    rng = random.Random(772)
    for shift in (180, 181, 300, 600, 900) + tuple(rng.randrange(180, 900) for _ in range(6)):
        baseline, original = m.demo_baseline()
        operation = replace(original, shift_seconds=shift)
        result = simulate_scenario(baseline, operation)
        ds = baseline.inputs.timetables[0]
        modified = []
        for run in ds.timetable.runs:
            if run.run_id == operation.execution_id:
                run = replace(
                    run,
                    events=tuple(
                        replace(
                            e,
                            arrival_at=e.arrival_at + timedelta(seconds=shift),
                            departure_at=e.departure_at + timedelta(seconds=shift),
                            evidence_refs=tuple(
                                sorted(set(e.evidence_refs + operation.evidence_refs))
                            ),
                        )
                        for e in run.events
                    ),
                )
            modified.append(run)
        manual = replace(
            ds.timetable,
            runs=tuple(modified),
            scope=result.overlay.inputs.timetables[0].timetable.scope,
        )
        raw = replace(baseline.inputs, timetables=(replace(ds, timetable=manual),))
        expected = create_scenario_baseline(raw).before
        assert expected.urban == result.after.urban
        assert [(v.key, v.value, v.state, v.proven) for v in expected.metrics] == [
            (v.key, v.value, v.state, v.proven) for v in result.after.metrics
        ]


def test_fresh_process_ids_and_input_permutations(tmp_path: Path) -> None:
    baseline, operation = m.demo_baseline()
    source = baseline.inputs.urban[0]
    ds = baseline.inputs.timetables[0]
    inputs = replace(
        baseline.inputs,
        urban=(
            replace(
                source,
                points=tuple(reversed(source.points)),
                destinations=tuple(reversed(source.destinations)),
                coverage=tuple(reversed(source.coverage)),
                district_shares=tuple(reversed(source.district_shares)),
            ),
        ),
        timetables=(
            replace(ds, timetable=replace(ds.timetable, runs=tuple(reversed(ds.timetable.runs)))),
        ),
    )
    permuted = create_scenario_baseline(inputs)
    assert permuted == baseline
    assert simulate_scenario(permuted, operation) == simulate_scenario(baseline, operation)
    outputs = set()
    code = "import runpy,sys; print(runpy.run_path(sys.argv[1])['run_demo']().scenario_id)"
    for seed in ("0", "81", "random"):
        completed = subprocess.run(
            [sys.executable, "-c", code, str(ENGINE / "examples/synthetic_scenario.py")],
            cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": str(ENGINE / "src"), "PYTHONHASHSEED": seed},
            check=True,
            capture_output=True,
            text=True,
        )
        outputs.add(completed.stdout)
    assert len(outputs) == 1


def test_additional_departure_requires_demand_identity_and_verified_resources() -> None:
    inputs, incoming, outgoing = m.demo_inputs()
    ds = inputs.timetables[0]
    # An accepted node-level rule applies to the added execution. Run-specific
    # permission is never copied to a new run by guessing its identity.
    generic = replace(ds.timetable.transfers[0], from_run_id=None, to_run_id=None)
    inputs = replace(
        inputs, timetables=(replace(ds, timetable=replace(ds.timetable, transfers=(generic,))),)
    )
    baseline, old = baseline_with(inputs, incoming, outgoing)
    source = next(r for r in ds.timetable.runs if r.run_id == outgoing)
    new = replace(
        source,
        trip_id="accepted-new-departure",
        events=tuple(
            replace(
                e,
                arrival_at=e.arrival_at + timedelta(seconds=1800),
                departure_at=e.departure_at + timedelta(seconds=1800),
            )
            for e in source.events
        ),
    )
    identity = PhysicalService(
        "new-physical", (new.run_id,), (PhysicalDeparture(0, "new-departure", m.PROOF),), m.PROOF
    )
    operation = replace(
        old,
        template_id="additional_departure",
        shift_seconds=1800,
        new_run=new,
        new_physical_service=identity,
        demand_evidence_refs=("accepted-demand",),
    )
    with pytest.raises(ValueError, match="VERIFIED_RESOURCE"):
        simulate_scenario(baseline, operation)
    block = ScenarioResourceBlock(
        "extra-vehicle",
        "network",
        (new.run_id,),
        m.START - timedelta(hours=1),
        m.START + timedelta(days=1),
        (),
        m.PROOF,
        0,
        0,
    )
    accepted = proof(baseline, operation, (block,), (new.run_id,))
    result = simulate_scenario(baseline, operation, feasibility=accepted)
    assert result.feasibility.state == "feasible"
    assert all(
        e.before_arrival is None and e.before_departure is None
        for e in result.overlay.changed_events
    )
    assert len(result.overlay.inputs.timetables[0].timetable.runs) == len(ds.timetable.runs) + 1
    assert result.impact.known_affected_population == 90
    with pytest.raises(ValueError):
        simulate_scenario(
            baseline, replace(operation, demand_evidence_refs=()), feasibility=accepted
        )
    with pytest.raises(ValueError):
        simulate_scenario(baseline, replace(operation, new_run=source), feasibility=accepted)


def test_intercity_reuses_common_reference_and_recounts_physical_departures() -> None:
    from a2_engine import build_reference, load_intercity_passenger_rules, load_passenger_config
    from a2_engine.scenario.models import IntercityScenarioInput

    ic = example("intercity")
    inputs, incoming, outgoing = m.demo_inputs()
    scope, _ = ic.demo_inputs()
    common = replace(scope.comparison_scope, origin_node_ids=("S",), destination_node_ids=("D",))
    scope = replace(scope, comparison_scope=common)
    graph = ic.demo_reference(scope, 1200)
    graph = replace(
        graph,
        states=tuple(replace(s, node_id="S" if s.node_id == "A" else "D") for s in graph.states),
    )
    reference = build_reference(graph, common)
    ds = inputs.timetables[0]
    identities = tuple(
        PhysicalService(
            "physical:" + r.trip_id,
            (r.run_id,),
            (PhysicalDeparture(0, "depart:" + r.trip_id, m.PROOF),),
            m.PROOF,
        )
        for r in ds.timetable.runs
    )
    ds = replace(
        ds,
        transport_modes=(TransportModeBinding("bus", "road", m.PROOF),),
        physical_services=identities,
    )
    intercity = IntercityScenarioInput(
        "intercity",
        "network",
        scope,
        m.START,
        reference,
        load_passenger_config(),
        load_intercity_passenger_rules(),
        m.START,
        m.START + timedelta(days=7),
    )
    inputs = replace(inputs, timetables=(ds,), intercity=(intercity,))
    baseline, operation = baseline_with(inputs, incoming, outgoing)
    result = simulate_scenario(baseline, operation)
    before = result.before.intercity[0][1]
    after = result.after.intercity[0][1]
    assert before.public.best_known is None and before.frequency[-1].count == 0
    assert after.public.best_known is not None and after.frequency[-1].count == 1
    assert after.public.best_known.path_seconds == 1500
    assert after.public.best_known.initial_wait_seconds == 0
    assert after.public.best_known.evaluation.index.value == pytest.approx(80**0.5 * 10)
    assert after.public.best_known.evaluation.reference.reference_id == reference.reference_id
    assert result.overlay.inputs.intercity[0].reference is reference


def test_real_cargo_schedule_recomputation_and_unchanged_urban_cargo_pipeline() -> None:
    from a2_engine import CargoSite, load_cargo_delivery_rules, load_freight_config
    from a2_engine.scenario.models import (
        CargoExecution,
        CargoScenarioInput,
        PipelineScenarioInput,
        UrbanCargoScenarioInput,
    )

    delivery = example("delivery")
    urban = example("urban_cargo")
    pipeline = example("pipeline")
    scope, states, actions, ready = delivery.demo_inputs()
    executions = tuple(
        CargoExecution(
            service,
            date(2026, 11, 9),
            tuple(a.action_id for a in actions if a.service_id == service),
            delivery.PROOF,
        )
        for service in ("road-service", "rail-service")
    )
    job = CargoScenarioInput(
        "cargo",
        scope,
        delivery.START,
        delivery.PROFILE,
        states,
        actions,
        ready,
        executions,
        load_freight_config(),
        load_cargo_delivery_rules(),
        physical_sites=(CargoSite("warehouse", delivery.CONTEXT, delivery.PROOF),),
    )
    city = urban.run_demo()
    route_pairs = (urban.truck("site1", "port", 1200), urban.truck("site2", "port", 2400))
    city_job = UrbanCargoScenarioInput(
        "urban-cargo",
        urban.PROFILE,
        urban.START,
        (
            urban.CargoSite("site1", urban.CONTEXT, urban.PROOF),
            urban.CargoSite("site2", urban.CONTEXT, urban.PROOF),
        ),
        (urban.CargoNode("port", "port", "port", urban.CONTEXT, urban.PROOF),),
        tuple(p[0] for p in route_pairs),
        tuple(p[1] for p in route_pairs),
        city.coverage,
        ("port",),
        100,
        load_freight_config(),
    )
    pipe_scope, observation = pipeline.demo_inputs()
    pipe_job = PipelineScenarioInput("pipeline", pipe_scope, (observation,), load_freight_config())
    inputs = ScenarioInputs(cargo=(job,), urban_cargo=(city_job,), pipelines=(pipe_job,))
    baseline = create_scenario_baseline(inputs)
    operation = ScenarioOperation(
        baseline.baseline_id,
        "cargo-coordinate",
        "coordinate_transfer",
        "cargo",
        "cargo",
        "rail-service",
        date(2026, 11, 9),
        "rail",
        execution_hash(baseline, "cargo", "cargo", "rail-service"),
        -3600,
        "road-service",
        "road",
        delivery.PROOF,
    )
    result = simulate_scenario(baseline, operation)
    before = result.before.cargo[0][1]
    after = result.after.cargo[0][1]
    assert before.overall.best_known is not None and after.overall.best_known is not None
    assert before.overall.best_known.evaluation.total_seconds.value == 86400
    assert after.overall.best_known.evaluation.total_seconds.value == 82800
    after_index = after.overall.best_known.evaluation.index.value
    before_index = before.overall.best_known.evaluation.index.value
    assert after_index is not None and before_index is not None and after_index > before_index
    assert after.profile is before.profile and after.profile.urgency == "urgent"
    assert result.impact.affected_site_ids == ("warehouse",) and result.impact.sites_complete
    assert result.before.urban_cargo == result.after.urban_cargo
    assert result.before.pipelines == result.after.pipelines
    assert result.complete
    block = ScenarioResourceBlock(
        "rail-resource",
        "cargo",
        ("rail-service",),
        delivery.START,
        delivery.START + timedelta(days=2),
        (),
        delivery.PROOF,
        0,
        0,
    )
    verified = simulate_scenario(
        baseline, operation, feasibility=proof(baseline, operation, (block,), ("rail-service",))
    )
    assert verified.feasibility.state == "feasible"
    with pytest.raises(ValueError, match="ALLOWLISTED"):
        simulate_scenario(baseline, replace(operation, template_id="change_handling_duration"))
    with pytest.raises(ValueError):
        simulate_scenario(baseline, replace(operation, template_id="additional_departure"))


def test_new_best_object_and_last_return_are_recomputed() -> None:
    from a2_engine import TimetableFootpath, UrbanDestination

    inputs, incoming, outgoing = m.demo_inputs()
    ds = inputs.timetables[0]
    job = inputs.urban[0]
    # The other physical object is independently reachable by a walking path.
    other = UrbanDestination(
        "other",
        "other-physical",
        frozenset({"school", "airport"}),
        "E",
        "city",
        True,
        True,
        m.PROOF,
    )
    table = replace(
        ds.timetable,
        scope=replace(ds.timetable.scope, node_ids=ds.timetable.scope.node_ids | {"E"}),
        footpaths=(TimetableFootpath("other-object", "S", "E", 1600, True, m.PROOF),),
    )
    coverage = tuple(
        replace(c, catalog_destination_ids=frozenset({"d", "other"})) for c in job.coverage
    )
    job = replace(
        job,
        destinations=job.destinations + (other,),
        coverage=coverage,
        return_day=date(2026, 11, 9),
    )
    inputs = replace(inputs, timetables=(replace(ds, timetable=table),), urban=(job,))
    baseline, operation = baseline_with(inputs, incoming, outgoing)
    result = simulate_scenario(baseline, operation)
    old = result.before.urban[0].analysis.modes[0].points[0].categories[0]
    new = result.after.urban[0].analysis.modes[0].points[0].categories[0]
    assert old.best is not None and new.best is not None
    assert old.best.destination.destination_id == "other"
    assert new.best.destination.destination_id == "d"
    assert old.best.journey.total_seconds == 1600 and new.best.journey.total_seconds == 1500
    assert any(e.delta.value == 100 and e.key[-1] == "time" for e in result.effects)
    assert result.after.urban[0].returns


def test_resource_conflict_across_different_network_blocks_is_not_missed() -> None:
    inputs, incoming, outgoing = m.demo_inputs()
    ds = inputs.timetables[0]
    source = next(r for r in ds.timetable.runs if r.run_id == outgoing)
    foreign = replace(
        source,
        trip_id="foreign",
        events=tuple(
            replace(
                e,
                arrival_at=e.arrival_at + timedelta(seconds=480),
                departure_at=e.departure_at + timedelta(seconds=480),
            )
            for e in source.events
        ),
    )
    other = replace(
        ds,
        network_id="other-network",
        timetable=replace(ds.timetable, runs=(foreign,), transfers=()),
    )
    inputs = replace(inputs, timetables=inputs.timetables + (other,))
    baseline, operation = baseline_with(inputs, incoming, outgoing)
    blocks = tuple(
        ScenarioResourceBlock(
            "same-vehicle",
            network,
            (run_id,),
            m.START - timedelta(days=1),
            m.START + timedelta(days=1),
            (),
            m.PROOF,
            0,
            0,
        )
        for network, run_id in (("network", outgoing), ("other-network", foreign.run_id))
    )
    result = simulate_scenario(baseline, operation, feasibility=proof(baseline, operation, blocks))
    assert (
        result.feasibility.state == "infeasible"
        and "RESOURCE_EXECUTIONS_OVERLAP" in result.feasibility.reasons
    )


def test_missing_event_proof_is_not_filled_by_overlay_evidence() -> None:
    inputs, incoming, outgoing = m.demo_inputs()
    ds = inputs.timetables[0]
    source = next(r for r in ds.timetable.runs if r.run_id == outgoing)
    source = replace(source, events=(replace(source.events[0], evidence_refs=()), source.events[1]))
    inputs = replace(
        inputs,
        timetables=(
            replace(
                ds,
                timetable=replace(
                    ds.timetable,
                    runs=tuple(source if r.run_id == outgoing else r for r in ds.timetable.runs),
                ),
            ),
        ),
    )
    baseline, operation = baseline_with(inputs, incoming, outgoing)
    with pytest.raises(ValueError, match="PROOF_REQUIRED"):
        simulate_scenario(baseline, operation)


def test_real_downstream_connection_loss_retains_null_time_and_worsening() -> None:
    from a2_engine import TimetableTransfer

    inputs, incoming, outgoing = m.demo_inputs()
    ds, job = inputs.timetables[0], inputs.urban[0]
    source = next(r for r in ds.timetable.runs if r.run_id == outgoing)
    downstream = replace(
        source,
        trip_id="downstream",
        events=(
            TimetableEvent(
                0,
                "D",
                m.START + timedelta(minutes=24),
                m.START + timedelta(minutes=24),
                True,
                True,
                m.PROOF,
            ),
            TimetableEvent(
                1,
                "Z",
                m.START + timedelta(minutes=30),
                m.START + timedelta(minutes=30),
                True,
                True,
                m.PROOF,
            ),
        ),
    )
    table = replace(
        ds.timetable,
        scope=replace(ds.timetable.scope, node_ids=ds.timetable.scope.node_ids | {"Z"}),
        runs=ds.timetable.runs + (downstream,),
        transfers=ds.timetable.transfers
        + (
            TimetableTransfer(
                "downstream-transfer", "D", "D", 120, True, m.PROOF, outgoing, downstream.run_id
            ),
        ),
    )
    job = replace(job, destinations=tuple(replace(d, access_node_id="Z") for d in job.destinations))
    baseline, operation = baseline_with(
        replace(inputs, timetables=(replace(ds, timetable=table),), urban=(job,)),
        incoming,
        outgoing,
    )
    result = simulate_scenario(baseline, operation)
    lost = next(
        e for e in result.effects if e.key[1:4] == ("point", "p2", "school") and e.key[-1] == "time"
    )
    assert lost.outcome == "lost_connection" and lost.delta.value is None
    assert lost.metric_id in result.worsened_metric_ids
    after = next(v for v in result.after.metrics if v.metric_id == lost.metric_id)
    assert after.state == "absent" and after.value is None and after.proven
    assert result.impact.known_affected_population == 10 and result.complete


def test_unknown_population_is_explicit_lower_bound() -> None:
    inputs, incoming, outgoing = m.demo_inputs()
    job = inputs.urban[0]
    job = replace(
        job,
        points=tuple(replace(p, population=None) if p.point_id == "p1" else p for p in job.points),
        original_cells=tuple(
            replace(c, population=None) if c.cell_id == "cell:p1" else c for c in job.original_cells
        ),
        population_coverage=replace(
            job.population_coverage, complete=False, excluded_population=None
        ),
        districts=(),
        district_shares=(),
        district_coverage=None,
    )
    baseline, operation = baseline_with(replace(inputs, urban=(job,)), incoming, outgoing)
    result = simulate_scenario(baseline, operation)
    assert result.impact.known_affected_population == 10
    assert not result.impact.population_complete
    assert result.impact.unknown_cell_ids == (("city", "cell:p1", "population"),)
    assert "AFFECTED_POPULATION_LOWER_BOUND" in result.diagnostics


def test_partial_population_share_cannot_be_claimed_as_full_original_cell() -> None:
    inputs, incoming, outgoing = m.demo_inputs()
    job = inputs.urban[0]
    job = replace(
        job,
        original_cells=(),
        districts=(),
        district_shares=(),
        district_coverage=None,
        points=tuple(
            replace(p, population_share_id="accepted-share:" + p.point_id) for p in job.points
        ),
    )
    baseline, operation = baseline_with(replace(inputs, urban=(job,)), incoming, outgoing)
    result = simulate_scenario(baseline, operation)
    assert result.impact.known_affected_population == 100
    assert not result.impact.population_complete and len(result.impact.unknown_cell_ids) == 2


def test_out_of_period_resource_evidence_cannot_confirm_violation_or_feasibility() -> None:
    baseline, operation = m.demo_baseline()
    block = ScenarioResourceBlock(
        "bus",
        "network",
        (operation.execution_id,),
        m.START,
        m.START + timedelta(minutes=20),
        (),
        m.PROOF,
        0,
        0,
    )
    envelope = proof(baseline, operation, (block,))
    envelope = replace(
        envelope,
        constraints_valid_from=m.START - timedelta(days=2),
        constraints_valid_to=m.START - timedelta(days=1),
    )
    result = simulate_scenario(baseline, operation, feasibility=envelope)
    assert result.feasibility.state == "unverified"
    assert "FEASIBILITY_EVIDENCE_OUTSIDE_PERIOD" in result.feasibility.reasons


@pytest.mark.parametrize("traffic_mode", ["static", "typical"])
def test_frozen_traffic_tau_and_all_baseline_versions_survive_scenario(
    traffic_mode: Literal["static", "typical"],
) -> None:
    from a2_engine import (
        UrbanODPair,
        UrbanODScope,
        UrbanTauDay,
        UrbanTauSlice,
        UrbanTauWeek,
        calculate_urban_tau,
        load_urban_tau_rules,
    )

    inputs, incoming, outgoing = m.demo_inputs()
    job = inputs.urban[0]
    context = replace(job.context, traffic_mode=traffic_mode)
    population = replace(job.population_coverage, context=context)
    rules = load_urban_tau_rules()
    scope = UrbanODScope(
        context.city_id,
        "public_transport",
        context.versions,
        traffic_mode,
        context.search_parameters_id,
        "UTC",
        frozenset(p.point_id for p in job.points),
        job.rules.max_total_wait_seconds,
    )
    monday = date(2026, 11, 2)
    pairs = (
        UrbanODPair("p1", "p2", "reachable", 1800, True, True, m.PROOF),
        UrbanODPair("p2", "p1", "reachable", 1800, True, True, m.PROOF),
    )
    week = UrbanTauWeek(
        "week",
        scope,
        monday,
        tuple(
            UrbanTauDay(monday + timedelta(days=d), True, False, True, True, m.PROOF)
            for d in range(7)
        ),
        tuple(
            UrbanTauSlice(
                scope,
                monday + timedelta(days=d),
                t,
                m.START.replace(hour=t.hour, minute=t.minute) - timedelta(days=7 - d),
                pairs,
                True,
                m.PROOF,
            )
            for d in range(rules.tau_days)
            for t in rules.tau_times
        ),
        m.PROOF,
        traffic_mode == "typical",
        m.PROOF if traffic_mode == "typical" else (),
    )
    tau = calculate_urban_tau(
        context,
        job.points,
        population,
        (week,),
        passenger_mode="public_transport",
        timezone="UTC",
        reference_at=m.START,
        rules=rules,
    )
    assert tau.tau_seconds.value == 1800
    job = replace(
        job,
        context=context,
        population_coverage=population,
        coverage=tuple(replace(c, scope=replace(c.scope, context=context)) for c in job.coverage),
        district_coverage=replace(job.district_coverage, context=context),
        thresholds=(tau.to_threshold(),),
        tau_results=(tau,),
    )
    snapshot = example("traffic").synthetic_snapshot()
    baseline, operation = baseline_with(
        replace(inputs, urban=(job,), traffic_snapshots=(snapshot,)), incoming, outgoing
    )
    result = simulate_scenario(baseline, operation)
    after_job = result.overlay.inputs.urban[0]
    assert after_job.tau_results[0] is tau
    assert after_job.context is context and after_job.config is job.config
    assert result.overlay.inputs.traffic_snapshots[0] is snapshot
    assert result.before.urban[0].analysis.h_seconds == result.after.urban[0].analysis.h_seconds
    assert (
        result.after.urban[0].analysis.modes[0].points[0].categories[0].threshold
        == tau.to_threshold()
    )


@pytest.mark.parametrize("missing", ["inactive", "unknown_calendar", "event_proof"])
def test_unproven_resource_occupancy_cannot_confirm_a_conflict(missing: str) -> None:
    inputs, incoming, outgoing = m.demo_inputs()
    ds = inputs.timetables[0]
    original = next(r for r in ds.timetable.runs if r.run_id == outgoing)
    neighbour = replace(
        original,
        trip_id="unproven-neighbour",
        events=tuple(
            replace(
                e,
                arrival_at=e.arrival_at + timedelta(minutes=8),
                departure_at=e.departure_at + timedelta(minutes=8),
            )
            for e in original.events
        ),
    )
    if missing == "inactive":
        neighbour = replace(neighbour, active=False)
    elif missing == "unknown_calendar":
        neighbour = replace(neighbour, active=None)
    else:
        neighbour = replace(
            neighbour, events=tuple(replace(e, evidence_refs=()) for e in neighbour.events)
        )
    baseline, operation = baseline_with(
        replace(
            inputs,
            timetables=(
                replace(ds, timetable=replace(ds.timetable, runs=ds.timetable.runs + (neighbour,))),
            ),
        ),
        incoming,
        outgoing,
    )
    block = ScenarioResourceBlock(
        "bus",
        "network",
        (outgoing, neighbour.run_id),
        m.START,
        m.START + timedelta(days=1),
        (),
        m.PROOF,
        0,
        0,
    )
    result = simulate_scenario(
        baseline, operation, feasibility=proof(baseline, operation, (block,))
    )
    assert result.feasibility.state == "unverified"
    assert "RESOURCE_EXECUTIONS_OVERLAP" not in result.feasibility.reasons


def test_conflicting_physical_identity_across_dataset_copies_is_rejected() -> None:
    inputs, _, outgoing = m.demo_inputs()
    ds = inputs.timetables[0]

    def identity(name: str) -> PhysicalService:
        return PhysicalService(name, (outgoing,), (), m.PROOF)

    first = replace(ds, physical_services=(identity("accepted-physical-A"),))
    second = replace(
        ds, network_id="duplicate-extract", physical_services=(identity("accepted-physical-B"),)
    )
    with pytest.raises(ValueError, match="CONFLICTING_SCENARIO_PHYSICAL_IDENTITY"):
        create_scenario_baseline(replace(inputs, timetables=(first, second)))


def test_unknown_middle_occupancy_cannot_mask_confirmed_resource_overlap() -> None:
    inputs, incoming, outgoing = m.demo_inputs()
    ds = inputs.timetables[0]
    source = next(r for r in ds.timetable.runs if r.run_id == outgoing)

    def execution(name: str, first: int, last: int, active: bool | None) -> Any:
        return replace(
            source,
            trip_id=name,
            active=active,
            events=(
                replace(
                    source.events[0],
                    arrival_at=m.START + timedelta(minutes=first),
                    departure_at=m.START + timedelta(minutes=first),
                ),
                replace(
                    source.events[1],
                    arrival_at=m.START + timedelta(minutes=last),
                    departure_at=m.START + timedelta(minutes=last),
                ),
            ),
        )

    long = execution("long-confirmed", 10, 40, True)
    uncertain = execution("short-uncertain", 12, 13, None)
    inputs = replace(
        inputs,
        timetables=(
            replace(
                ds, timetable=replace(ds.timetable, runs=ds.timetable.runs + (long, uncertain))
            ),
        ),
    )
    baseline, operation = baseline_with(inputs, incoming, outgoing)
    block = ScenarioResourceBlock(
        "bus",
        "network",
        (long.run_id, uncertain.run_id, outgoing),
        m.START,
        m.START + timedelta(days=1),
        (),
        m.PROOF,
        0,
        0,
    )
    result = simulate_scenario(
        baseline, operation, feasibility=proof(baseline, operation, (block,))
    )
    assert result.feasibility.state == "infeasible"
    assert "RESOURCE_EXECUTIONS_OVERLAP" in result.feasibility.reasons
