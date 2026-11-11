"""Independent boundary oracles added during the final ML1 audit."""

from decimal import Decimal, localcontext

import pytest
from a2_engine import (
    PipelineBasis,
    ReferenceTime,
    category_index,
    intercity_car_index,
    load_freight_config,
    load_passenger_config,
    pipeline_utilization,
)


@pytest.mark.parametrize("exponent", [1023, 1070, 1074, 1075, 1080])
def test_exponential_subnormal_decimal_oracle(exponent: int) -> None:
    with localcontext() as context:
        context.prec = 100
        expected = float(Decimal(100) * Decimal(2) ** -exponent)
    assert category_index(exponent, 1).value == expected


def test_ratio_scaling_preserves_representable_subnormal() -> None:
    tiny = float.fromhex("0x0.0000000000001p-1022")
    with localcontext() as context:
        context.prec = 100
        expected = float(Decimal.from_float(tiny) * 100 / 2)
    config = load_passenger_config()
    reference = ReferenceTime("reference", tiny, config.reference_profile, "full", True)
    assert intercity_car_index("chain", 2, reference, config=config).path_index.value == expected
    basis = PipelineBasis("oil", "2026", "m3", "connection", "out")
    result = pipeline_utilization(
        tiny,
        2,
        volume_basis=basis,
        capacity_basis=basis,
        connection_confirmed=True,
        config=load_freight_config(),
    )
    assert result.utilization_percent.value == expected


def test_shared_cargo_execution_is_shifted_in_every_job() -> None:
    import runpy
    from dataclasses import replace
    from datetime import date
    from pathlib import Path

    from a2_engine import (
        CargoExecution,
        CargoScenarioInput,
        ScenarioInputs,
        ScenarioOperation,
        create_scenario_baseline,
        execution_hash,
        load_cargo_delivery_rules,
        simulate_scenario,
    )

    demo = runpy.run_path(str(Path(__file__).parents[1] / "examples/synthetic_delivery.py"))
    scope, states, actions, ready = demo["demo_inputs"]()
    job = CargoScenarioInput(
        "first",
        scope,
        demo["START"],
        demo["PROFILE"],
        states,
        actions,
        ready,
        tuple(
            CargoExecution(
                service,
                date(2026, 11, 9),
                tuple(a.action_id for a in actions if a.service_id == service),
                demo["PROOF"],
            )
            for service in ("road-service", "rail-service")
        ),
        load_freight_config(),
        load_cargo_delivery_rules(),
    )
    baseline = create_scenario_baseline(ScenarioInputs(cargo=(job, replace(job, job_id="second"))))
    operation = ScenarioOperation(
        baseline.baseline_id,
        "coord",
        "coordinate_transfer",
        "cargo",
        "first",
        "rail-service",
        date(2026, 11, 9),
        "rail",
        execution_hash(baseline, "cargo", "first", "rail-service"),
        -3600,
        "road-service",
        "road",
        demo["PROOF"],
    )
    result = simulate_scenario(baseline, operation)
    totals = [
        r.overall.best_known.evaluation.total_seconds.value
        for _, r in result.after.cargo
        if r.overall.best_known
    ]
    assert totals == [82800, 82800]
    assert baseline.inputs.cargo[0].actions == job.actions
    assert {e.network_id for e in result.overlay.changed_events} == {"first", "second"}
    from datetime import timedelta

    from a2_engine import ScenarioFeasibilityInputs, ScenarioResourceBlock, scenario_hash

    blocks = tuple(
        ScenarioResourceBlock(
            f"vehicle:{name}",
            name,
            ("rail-service",),
            demo["START"],
            demo["START"] + timedelta(days=2),
            (),
            demo["PROOF"],
            0,
            0,
        )
        for name in ("first", "second")
    )
    proof = ScenarioFeasibilityInputs(
        baseline.baseline_id,
        scenario_hash(operation),
        blocks,
        ("rail-service",),
        True,
        True,
        True,
        demo["START"],
        demo["START"] + timedelta(days=2),
        demo["PROOF"],
    )
    with pytest.raises(ValueError, match="PHYSICAL_EXECUTION_ASSIGNED_MULTIPLE_RESOURCES"):
        simulate_scenario(baseline, operation, feasibility=proof)
    assert simulate_scenario(baseline, operation) == result
    conflicting = replace(
        job,
        job_id="second",
        actions=tuple(
            replace(a, seconds=43201) if a.action_id == "rail" else a for a in job.actions
        ),
    )
    invalid = create_scenario_baseline(ScenarioInputs(cargo=(job, conflicting)))
    with pytest.raises(ValueError, match="INCONSISTENT_SHARED_CARGO_EXECUTION"):
        simulate_scenario(invalid, replace(operation, baseline_id=invalid.baseline_id))


def test_random_decimal_formula_monotonicity() -> None:
    import random

    rng = random.Random(20261109)
    for _ in range(300):
        half_life = rng.uniform(1, 100000)
        travel = rng.uniform(0, 100 * half_life)
        result = category_index(travel, half_life).value
        later = category_index(travel + half_life, half_life).value
        with localcontext() as context:
            context.prec = 60
            oracle = float(
                Decimal(100)
                * Decimal(2) ** (-Decimal.from_float(travel) / Decimal.from_float(half_life))
            )
        assert result is not None and later is not None
        assert result == pytest.approx(oracle, rel=2e-14)
        assert later == pytest.approx(result / 2, rel=2e-14)


def test_reference_against_independent_raw_graph_enumeration() -> None:
    import random
    import runpy
    from dataclasses import replace
    from pathlib import Path

    from a2_engine import (
        ReferenceCalibration,
        ReferenceEdge,
        ReferenceGraph,
        ReferenceOperation,
        ReferenceState,
        build_reference,
    )

    demo = runpy.run_path(str(Path(__file__).parents[1] / "examples/synthetic_reference.py"))
    graph, scope = demo["demo_inputs"]()
    rng = random.Random(8123)
    for trial in range(40):
        size = 6
        costs = {
            (i, j): rng.randrange(1, 30)
            for i in range(size)
            for j in range(size)
            if i != j and j != 0 and i != size - 1 and rng.random() < 0.35
        }
        costs[0, size - 1] = 100
        handling = {i: rng.randrange(0, 8) for i in range(1, size - 1)}
        answers = []

        def enumerate_paths(node: int, cost: int, seen: frozenset[int]) -> None:
            if node == size - 1:
                answers.append(cost)
                return
            for (a, b), seconds in costs.items():
                if a == node and b not in seen:
                    enumerate_paths(b, cost + seconds + handling.get(b, 0), seen | {b})

        enumerate_paths(0, 0, frozenset({0}))
        states = tuple(
            ReferenceState(f"{i}:{phase}", str(i), phase, "rail", f"conditions:{i}")
            for i in range(size)
            for phase in ("arrival", "departure")
        )
        edges = tuple(
            ReferenceEdge(
                f"{a}-{b}",
                f"{a}:departure",
                f"{b}:arrival",
                (
                    ReferenceCalibration(
                        f"cal:{a}-{b}",
                        f"{a}-{b}",
                        f"{a}:departure",
                        f"{b}:arrival",
                        graph.network,
                        seconds,
                        "running_time",
                        ("proof",),
                    ),
                ),
                ("proof",),
                compatible=True,
            )
            for (a, b), seconds in costs.items()
        )
        operations = tuple(
            ReferenceOperation(
                f"op:{i}",
                f"{i}:arrival",
                f"{i}:departure",
                graph.network,
                seconds,
                "mandatory_internal_transfer",
                ("proof",),
                compatible=True,
            )
            for i, seconds in handling.items()
        )
        network = ReferenceGraph(graph.network, states, edges, operations, "full", ("proof",))
        query = replace(
            scope,
            origin_node_ids=("0",),
            destination_node_ids=(str(size - 1),),
            allowed_modes=("rail",),
        )
        result = build_reference(network, query)
        assert result.seconds == min(answers), trial
        assert result.full_ranking_eligible
        permuted = replace(
            network,
            edges=tuple(reversed(edges)),
            states=tuple(reversed(states)),
            operations=tuple(reversed(operations)),
        )
        assert build_reference(permuted, query) == result
