"""Run and report the engine acceptance matrix from immutable fixture inputs."""

from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any, Literal

from a2_engine.freight.delivery.models import IntercityCargoAnalysis
from a2_engine.freight.urban_analysis import UrbanCargoAnalysis
from a2_engine.intercity.analysis.models import IntercityPassengerAnalysis
from a2_engine.scenario.engine import (
    create_scenario_baseline,
    simulate_scenario,
    validate_scenario_result,
)
from a2_engine.scenario.identity import scenario_hash
from a2_engine.scenario.models import (
    ScenarioBaseline,
    ScenarioComputation,
    ScenarioFeasibilityInputs,
    ScenarioInputs,
    ScenarioOperation,
    ScenarioResult,
    UrbanScenarioResult,
)
from a2_engine.urban.analysis.models import UrbanAnalysis

from ml1_demo import oracle
from ml1_demo.cargo import delivery_inputs, delivery_operation, urban_cargo_input
from ml1_demo.intercity import passenger_inputs, passenger_operation
from ml1_demo.model import CITIES, PAIRS, SEED, VERSION, City, config_hash
from ml1_demo.oracle import Stage, UrbanMode
from ml1_demo.urban import analyze_car, urban_inputs, urban_operation

Row = dict[str, Any]
TRANSIT_OPERATIONS: tuple[Literal["coordinate_transfer", "additional_departure"], ...] = (
    "coordinate_transfer",
    "additional_departure",
)


@dataclass(frozen=True)
class DemoCase:
    id: str
    origin: City
    destination: City | None
    inputs: ScenarioInputs


def load_cases() -> tuple[DemoCase, ...]:
    return (
        *(DemoCase(f"urban:{c.id}", c, None, urban_inputs(c)) for c in CITIES),
        *(
            DemoCase(
                f"urban-cargo:{c.id}",
                c,
                None,
                ScenarioInputs(
                    urban_cargo=(urban_cargo_input(c),),
                ),
            )
            for c in CITIES
        ),
        *(DemoCase(f"passenger:{a.id}:{b.id}", a, b, passenger_inputs(a, b)) for a, b in PAIRS),
        *(DemoCase(f"delivery:{a.id}:{b.id}", a, b, delivery_inputs(a, b)) for a, b in PAIRS),
    )


def prepare(cases: tuple[DemoCase, ...]) -> tuple[ScenarioBaseline, ...]:
    return tuple(create_scenario_baseline(case.inputs) for case in cases)


def work_states(run: ScenarioComputation) -> dict[str, int]:
    return {
        "urban_timetable": sum(s.search.expanded_states for job in run.urban for s in job.searches),
        "scheduled_passenger": sum(a.search.expanded_states for _, a in run.intercity),
        "scheduled_frequency": sum(a.frequency_search.expanded_states for _, a in run.intercity),
        "delivery": sum(a.expanded_states for _, a in run.cargo),
    }


def urban_row(analysis: UrbanAnalysis, city: City, mode: UrbanMode, stage: Stage = "before") -> Row:
    group = next(m for m in analysis.modes if m.passenger_mode == mode)
    return {
        "h_s": analysis.h_seconds.value,
        "status": group.index.status,
        "eligible": group.ranking_eligible,
        "index": group.index.value,
        "expected_index": oracle.urban_index(city, mode, stage),
        "population": [p.point.population for p in group.points],
        "points": {
            p.point.point_id: {
                "index": p.index.value,
                "expected_index": oracle.point_index(city, mode, int(p.point.point_id[1:]), stage),
                "categories": {
                    c.category: {
                        "s": c.best.journey.total_seconds if c.best else None,
                        "index": c.index.value,
                        "minimum_proven": c.minimum_proven,
                        "object": c.best.destination.destination_id if c.best else None,
                    }
                    for c in p.categories
                },
            }
            for p in group.points
        },
    }


def passenger_row(analysis: IntercityPassengerAnalysis) -> Row:
    winner = analysis.public.best_known
    assert winner is not None
    e = winner.evaluation
    return {
        "initial_wait": winner.initial_wait_seconds,
        "movement": winner.riding_seconds,
        "transfer_wait": winner.transfer_wait_seconds,
        "path": winner.path_seconds,
        "total": winner.total_seconds,
        "reference": e.reference.seconds,
        "path_index": e.path_index.value,
        "waiting_index": e.waiting_index.value,
        "index": e.index.value,
        "status": e.index.status,
        "eligible": analysis.full_ranking_eligible,
        "first_departure": winner.route.path.boardings[0][2].isoformat(),
        "transfers": winner.transfers,
    }


def urban_cargo_row(analysis: UrbanCargoAnalysis, city: City) -> Row:
    group = analysis.by_type[0]
    return {
        "h_s": analysis.h_seconds.value,
        "status": group.index.status,
        "eligible": group.full_ranking_eligible,
        "index": group.index.value,
        "expected_index": oracle.urban_cargo_index(city),
        "sites": [
            {
                "site": s.site_id,
                "node": s.selected_node_id,
                "network_s": s.best_known_route.network_seconds,
                "origin_access_s": s.best_known_route.origin_access_seconds,
                "destination_access_s": s.best_known_route.destination_access_seconds,
                "total_s": s.best_known_route.seconds,
                "compatibility": s.best_known_route.compatibility.state,
                "index": s.evaluation.index.value,
            }
            for s in group.sites
            if s.best_known_route is not None
        ],
    }


def delivery_row(analysis: IntercityCargoAnalysis) -> Row:
    best = analysis.overall.best_known
    assert best is not None
    return {
        **{
            k: sum(i.seconds for i in best.intervals if i.kind == k)
            for k in ("initial_wait", "movement", "handling", "transfer_wait")
        },
        "transshipment": next(i.seconds for i in best.intervals if i.action_id == "transfer"),
        "total": best.evaluation.total_seconds.value,
        "urgency": analysis.profile.urgency,
        "index": best.evaluation.index.value,
        "status": best.evaluation.index.status,
        "eligible": analysis.full_ranking_eligible,
        "actions": best.action_ids,
        "intervals": [
            {"id": i.action_id, "kind": i.kind, "start": i.start_at.isoformat(), "s": i.seconds}
            for i in best.intervals
        ],
    }


def baseline_rows(case: DemoCase, baseline: ScenarioBaseline) -> list[Row]:
    before = baseline.before
    result: list[Row] = []
    common = {
        "id": case.id,
        "input_sha256": baseline.input_hash,
        "expanded_states": work_states(before),
    }
    if before.urban:
        analysis = before.urban[0].analysis
        for mode in ("walk", "public_transport"):
            result.append({**common, "mode": mode, **urban_row(analysis, case.origin, mode)})
        car = analyze_car(case.inputs.urban[0], case.origin)
        result.append(
            {
                **common,
                "mode": "private_car",
                "road_responses": len(car.routes),
                "input_sha256": scenario_hash((car.plan, car.routes)),
                **urban_row(car.analysis, case.origin, "private_car"),
            }
        )
    elif before.intercity:
        assert case.destination is not None
        result.append(
            {
                **common,
                **passenger_row(before.intercity[0][1]),
                "expected": asdict(oracle.passenger(case.origin, case.destination)),
            }
        )
    elif before.cargo:
        assert case.destination is not None
        result.append(
            {
                **common,
                **delivery_row(before.cargo[0][1]),
                "expected": asdict(oracle.delivery(case.origin, case.destination)),
            }
        )
    else:
        result.append({**common, **urban_cargo_row(before.urban_cargo[0][1], case.origin)})
    return result


def operations(
    baseline: ScenarioBaseline,
) -> tuple[tuple[ScenarioOperation, ScenarioFeasibilityInputs | None], ...]:
    if baseline.inputs.urban:
        return tuple(urban_operation(baseline, template) for template in TRANSIT_OPERATIONS)
    if baseline.inputs.intercity:
        return ((passenger_operation(baseline), None),)
    if baseline.inputs.cargo:
        return ((delivery_operation(baseline), None),)
    return ()


def run_comparison(
    baseline: ScenarioBaseline,
    operation: ScenarioOperation,
    proof: ScenarioFeasibilityInputs | None,
    *,
    validate: bool = True,
) -> ScenarioResult:
    result = simulate_scenario(baseline, operation, feasibility=proof)
    if validate:
        validate_scenario_result(baseline, operation, result, feasibility=proof)
    return result


def comparison_row(case: DemoCase, result: ScenarioResult) -> Row:
    before, after = result.before, result.after
    if before.urban:
        stage: Stage = (
            "coordinate" if result.operation.template_id == "coordinate_transfer" else "additional"
        )
        first = urban_row(before.urban[0].analysis, case.origin, "public_transport")
        second = urban_row(after.urban[0].analysis, case.origin, "public_transport", stage)
    elif before.intercity:
        assert case.destination is not None
        first, second = passenger_row(before.intercity[0][1]), passenger_row(after.intercity[0][1])
        first["expected"] = asdict(oracle.passenger(case.origin, case.destination))
        second["expected"] = asdict(oracle.passenger(case.origin, case.destination, after=True))
    else:
        assert case.destination is not None
        first, second = delivery_row(before.cargo[0][1]), delivery_row(after.cargo[0][1])
        first["expected"] = asdict(oracle.delivery(case.origin, case.destination))
        second["expected"] = asdict(oracle.delivery(case.origin, case.destination, after=True))
    return {
        "id": case.id,
        "operation": result.operation.template_id,
        "shift_s": result.operation.shift_seconds,
        "baseline_id": result.baseline_id,
        "overlay_id": result.overlay.overlay_id,
        "scenario_id": result.scenario_id,
        "feasibility": result.feasibility.state,
        "complete": result.complete,
        "before": first,
        "after": second,
        "delta_index": second["index"] - first["index"],
        "effects": dict(Counter(e.outcome for e in result.effects)),
        "nonzero_effects": [
            {
                "key": e.key,
                "outcome": e.outcome,
                "delta": e.delta.value,
                "reason": e.delta.reason_code,
            }
            for e in result.effects
            if e.outcome != "unchanged"
        ],
        "changed_events": [
            {
                "network": e.network_id,
                "run": e.execution_id,
                "stop_or_action": e.event_id,
                "before": e.before_departure.isoformat() if e.before_departure else None,
                "after": e.after_departure.isoformat(),
            }
            for e in result.overlay.changed_events
        ],
        "impact": asdict(result.impact),
        "expanded_states": {"before": work_states(before), "after": work_states(after)},
    }


def run_matrix() -> Row:
    cases = load_cases()
    baseline_set = prepare(cases)
    baselines, comparisons = [], []
    for case, baseline in zip(cases, baseline_set, strict=True):
        baselines.extend(baseline_rows(case, baseline))
        edits = operations(baseline)
        if not edits:
            comparisons.append(
                {
                    "id": case.id,
                    "operation": "reduce_handling_wait",
                    "status": "blocked",
                    "reason": "SCENARIO_OPERATION_NOT_ALLOWLISTED",
                    "baseline_id": baseline.baseline_id,
                }
            )
        for operation, proof in edits:
            result = run_comparison(baseline, operation, proof)
            comparisons.append(comparison_row(case, result))
    return {
        "scope": "synthetic",
        "version": VERSION,
        "seed": SEED,
        "config_sha256": config_hash(),
        "baseline": baselines,
        "comparisons": comparisons,
    }


def district_results(result: UrbanScenarioResult, mode: UrbanMode) -> dict[str, float | None]:
    assert result.districts is not None
    group = next(g for g in result.districts.modes if g.passenger_mode == mode)
    return {r.district_id: r.index.value for r in group.overall.districts}
