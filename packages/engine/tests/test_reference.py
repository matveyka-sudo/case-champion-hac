"""Reference controls, invalid input boundaries and independent simple-path oracle."""

import importlib.util
import os
import random
import subprocess
import sys
from dataclasses import FrozenInstanceError, replace
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal, cast

import pytest
from a2_engine import (
    PassengerChain,
    ReferenceCalibration,
    ReferenceComparison,
    ReferenceEdge,
    ReferenceGraph,
    ReferenceOperation,
    ReferenceScope,
    ReferenceState,
    RoadBuild,
    build_reference,
    calibrate_reference_graph,
    intercity_car_index,
    intercity_passenger_index,
    load_passenger_config,
    reference_time_from_result,
)

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/synthetic_reference.py"
_spec = importlib.util.spec_from_file_location("synthetic_reference", EXAMPLE)
assert _spec is not None and _spec.loader is not None
_demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_demo)


def inputs() -> tuple[ReferenceGraph, ReferenceScope]:
    graph, scope = _demo.demo_inputs()
    assert isinstance(graph, ReferenceGraph) and isinstance(scope, ReferenceScope)
    return graph, scope


def edge(graph: ReferenceGraph, name: str, start: str, end: str, *seconds: float) -> ReferenceEdge:
    return ReferenceEdge(
        name,
        start,
        end,
        tuple(
            ReferenceCalibration(
                f"{name}:sample:{i}",
                name,
                start,
                end,
                graph.network,
                seconds=s,
                semantics="running_time",
                evidence_refs=(f"ev:{name}:{i}",),
            )
            for i, s in enumerate(seconds)
        ),
        (f"ev:{name}",),
        compatible=True,
    )


def test_mixed_search_into_unchanged_formulas() -> None:
    result, passenger, car = _demo.run_demo()
    graph, scope = inputs()
    assert result == build_reference(graph, scope)
    assert result.seconds == 3600
    assert result.path.edge_ids == ("rail", "air")
    assert result.path.operation_ids == ("transfer",)
    assert result.path.state_ids == ("A:rail", "X:rail", "X:air", "B:air")
    assert result.path.movement_seconds == 3300 and result.path.operation_seconds == 300
    assert "ev:transfer" in result.path.evidence_refs
    assert {"ev:network", "ev:coverage", "ev:direct", "ev:rail", "ev:air"} <= set(
        result.evidence_refs
    )
    assert result.search_status == "minimum_proven" and result.full_ranking_eligible
    assert passenger.path_index.value == passenger.waiting_index.value == car.path_index.value == 50
    assert passenger.index.value == pytest.approx(50)
    assert graph == inputs()[0]  # No mutation, including accepted calibration ordering.
    with pytest.raises(FrozenInstanceError):
        setattr(result, "seconds", 2)


def test_independent_edge_calibration_not_historical_whole_journey() -> None:
    graph, scope = inputs()
    edges = (
        edge(graph, "rail", "A:rail", "X:rail", 1000, 4000),
        edge(graph, "air", "X:air", "B:air", 4000, 1000),
    )
    result = build_reference(replace(graph, edges=edges), scope)
    assert result.seconds == 2300 and result.path is not None
    assert tuple(a.calibration_id for a in result.path.arcs) == (
        "rail:sample:0",
        None,
        "air:sample:1",
    )


@pytest.mark.parametrize("kind", ["unknown_cost", "unknown_compatibility", "forbidden"])
@pytest.mark.parametrize("direct", [True, False])
def test_transfer_unknown_distinct_from_confirmed_forbidden(kind: str, direct: bool) -> None:
    graph, scope = inputs()
    op = (
        replace(graph.operations[0], seconds=None)
        if kind == "unknown_cost"
        else replace(graph.operations[0], compatible=False if kind == "forbidden" else None)
    )
    graph = replace(
        graph,
        operations=(op,),
        edges=tuple(e for e in graph.edges if direct or e.edge_id != "direct"),
    )
    result = build_reference(graph, scope)
    assert result.seconds == (5000 if direct else None)
    assert result.completeness == ("full" if kind == "forbidden" else "partial")
    assert result.full_ranking_eligible == (kind == "forbidden" and direct)
    assert "transfer" in result.calibrated_graph.excluded_arc_ids
    if not direct:
        assert result.search_status == (
            "no_path_proven" if kind == "forbidden" else "known_subnetwork_no_path"
        )
    scalar = reference_time_from_result(
        result, ReferenceComparison(scope, "A", "B"), config=load_passenger_config()
    )
    passenger = intercity_passenger_index(
        PassengerChain("p", 7200, 43200), scalar, config=load_passenger_config()
    )
    assert passenger.waiting_index.value == 50
    assert passenger.chain.path_seconds == 7200
    if direct and kind != "forbidden":
        assert passenger.path_index.value == pytest.approx(100 * 5000 / 7200)
        assert passenger.completeness == "partial"
        assert "PARTIAL_REFERENCE" in passenger.diagnostics
    elif not direct:
        assert scalar.seconds is None and passenger.path_index.value is None
        assert passenger.path_index.reason_code == "REFERENCE_UNAVAILABLE"


def test_slower_arrival_state_keeps_compatible_continuation() -> None:
    graph, scope = inputs()
    states = graph.states + (
        ReferenceState("X:fast", "X", "arrival", "rail", "incompatible-platform"),
    )
    fast = edge(graph, "fast", "A:rail", "X:fast", 1)
    result = build_reference(replace(graph, states=states, edges=graph.edges + (fast,)), scope)
    assert result.seconds == 3600
    assert result.path is not None and "X:fast" not in result.path.state_ids
    # Giving this state a confirmed transition changes the answer; no transition is inferred.
    op = replace(graph.operations[0], operation_id="fast-transfer", from_state_id="X:fast")
    assert (
        build_reference(
            replace(
                graph,
                states=states,
                edges=graph.edges + (fast,),
                operations=graph.operations + (op,),
            ),
            scope,
        ).seconds
        == 1801
    )


def test_boundary_operations_excluded_internal_same_kind_retained() -> None:
    graph, scope = inputs()
    states = graph.states + (
        ReferenceState("A:before", "A", "arrival", "rail", "pre-departure"),
        ReferenceState("B:after", "B", "departure", "air", "post-arrival"),
    )
    op = graph.operations[0]
    before = replace(
        op, operation_id="before", from_state_id="A:before", to_state_id="A:rail", seconds=800
    )
    after = replace(
        op, operation_id="after", from_state_id="B:air", to_state_id="B:after", seconds=800
    )
    result = build_reference(replace(graph, states=states, operations=(before, op, after)), scope)
    assert result.seconds == 3600
    assert result.path is not None and result.path.operation_ids == ("transfer",)


def pair_inputs() -> tuple[ReferenceGraph, ReferenceScope]:
    graph, scope = inputs()
    states = tuple(
        ReferenceState(
            f"{node}:state",
            node,
            cast(Literal["departure", "arrival"], phase),
            "air",
            f"platform:{node}",
        )
        for node, phase in (
            ("A1", "departure"),
            ("A2", "departure"),
            ("B1", "arrival"),
            ("B2", "arrival"),
        )
    )
    edges = (
        edge(graph, "pair1", "A1:state", "B1:state", 600),
        edge(graph, "pair2", "A2:state", "B2:state", 900),
    )
    graph = replace(graph, states=states, edges=edges, operations=())
    return graph, replace(scope, origin_node_ids=("A1", "A2"), destination_node_ids=("B1", "B2"))


def test_common_scope_best_node_distinct_from_exact_pair() -> None:
    graph, common = pair_inputs()
    config = load_passenger_config()
    result = build_reference(graph, common)
    assert result.seconds == 600 and result.path is not None
    assert (result.path.origin_node_id, result.path.destination_node_id) == ("A1", "B1")
    scalar = reference_time_from_result(
        result, ReferenceComparison(common, "A2", "B2"), config=config
    )
    assert scalar.applicability_confirmed and scalar.seconds == 600
    exact = replace(
        common,
        comparison_kind="exact_node_pair",
        origin_node_ids=("A2",),
        destination_node_ids=("B2",),
    )
    exact_result = build_reference(graph, exact)
    assert exact_result.seconds == 900
    assert not reference_time_from_result(
        result, ReferenceComparison(exact, "A2", "B2"), config=config
    ).applicability_confirmed
    assert not reference_time_from_result(
        exact_result, ReferenceComparison(common, "A2", "B2"), config=config
    ).applicability_confirmed


@pytest.mark.parametrize(
    "field,value",
    [
        ("graph_version", "other-graph"),
        ("snapshot_id", "other-snapshot"),
        ("applicability_id", "other-season"),
        ("compatibility_version", "other-rules"),
        ("acceptance_id", "other-acceptance"),
    ],
)
def test_adapter_rejects_other_network_context(field: str, value: str) -> None:
    graph, scope = inputs()
    result = build_reference(graph, scope)
    other = replace(scope, network=replace(scope.network, **cast(dict[str, Any], {field: value})))
    scalar = reference_time_from_result(
        result, ReferenceComparison(other, "A", "B"), config=load_passenger_config()
    )
    assert not scalar.applicability_confirmed
    assert (
        intercity_car_index(
            "c", 7200, scalar, config=load_passenger_config()
        ).path_index.reason_code
        == "REFERENCE_NOT_APPLICABLE"
    )


@pytest.mark.parametrize(
    "change",
    ["reverse", "boundaries", "kind", "modes", "outside_node", "config_profile", "config_method"],
)
def test_adapter_checks_remaining_analysis_context(change: str) -> None:
    graph, scope = pair_inputs()
    result = build_reference(graph, scope)
    config = load_passenger_config()
    other = scope
    actual_a = "A2"
    if change == "reverse":
        other = replace(
            scope,
            origin_territory_id="city-B",
            destination_territory_id="city-A",
            origin_node_ids=scope.destination_node_ids,
            destination_node_ids=scope.origin_node_ids,
        )
    elif change == "boundaries":
        other = replace(scope, origin_node_ids=("A1",))
    elif change == "kind":
        other = replace(
            scope,
            comparison_kind="exact_node_pair",
            origin_node_ids=("A2",),
            destination_node_ids=("B2",),
        )
    elif change == "modes":
        other = replace(scope, allowed_modes=("air",))
    elif change == "outside_node":
        actual_a = "outside"
    elif change == "config_profile":
        config = replace(config, reference_profile="other")
    else:
        config = replace(config, methodology_version="other")
    assert not reference_time_from_result(
        result, ReferenceComparison(other, actual_a, "B2"), config=config
    ).applicability_confirmed


@pytest.mark.parametrize("traffic", ["static", "typical", "live", "historical"])
def test_actual_traffic_does_not_change_static_A(traffic: Any) -> None:
    graph, scope = inputs()
    result = build_reference(graph, scope)
    scalar = reference_time_from_result(
        result, ReferenceComparison(scope, "A", "B", traffic), config=load_passenger_config()
    )
    assert scalar.applicability_confirmed and scalar.seconds == 3600
    assert result.reference_id == build_reference(graph, scope).reference_id


def test_reverse_not_inferred_and_no_path_coverage_distinguished() -> None:
    graph, scope = inputs()
    reverse = replace(
        scope,
        origin_territory_id="city-B",
        destination_territory_id="city-A",
        origin_node_ids=("B",),
        destination_node_ids=("A",),
    )
    full = build_reference(graph, reverse)
    partial = build_reference(replace(graph, coverage="partial"), reverse)
    assert full.seconds is partial.seconds is None
    assert full.search_status == "no_path_proven"
    assert partial.search_status == "known_subnetwork_no_path"
    assert "REFERENCE_NO_FEASIBLE_PATH" in full.reasons
    assert "REFERENCE_COVERAGE_INCOMPLETE" in partial.reasons


def test_missing_and_unconfirmed_faster_alternatives_preserve_partial_number() -> None:
    graph, scope = inputs()
    direct = next(e for e in graph.edges if e.edge_id == "direct")
    for replacement in (
        replace(direct, calibrations=()),
        replace(
            direct, calibrations=(replace(direct.calibrations[0], seconds=1, confirmed=False),)
        ),
        replace(direct, coverage="partial"),
    ):
        result = build_reference(
            replace(
                graph, edges=tuple(replacement if e.edge_id == "direct" else e for e in graph.edges)
            ),
            scope,
        )
        assert result.seconds == 3600 and result.completeness == "partial"
        assert not result.full_ranking_eligible
        assert any("direct" in reason for reason in result.reasons)


def test_known_air_time_without_geometry_and_informational_geodesic_length() -> None:
    graph, scope = inputs()
    edges = tuple(
        replace(e, length_m=150000, length_basis="geodesic") if e.edge_id == "air" else e
        for e in graph.edges
    )
    result = build_reference(replace(graph, edges=edges), scope)
    assert result.seconds == 3600 and result.path is not None
    air = next(a for a in result.path.arcs if a.arc_id == "air")
    assert air.effective_speed_mps == 100
    assert next(e for e in edges if e.edge_id == "air").geometry_ref is None


def test_zero_cycles_parallel_equal_routes_and_input_permutations() -> None:
    graph, scope = inputs()
    loop_state = ReferenceState("X:air:return", "X", "arrival", "air", "loop-platform")
    loop = edge(graph, "0-loop", "X:air", "X:air:return", 0)
    loop_operation = ReferenceOperation(
        "0-loop-op",
        "X:air:return",
        "X:air",
        graph.network,
        0,
        "transfer",
        ("ev:zero-loop",),
        compatible=True,
    )
    direct = next(e for e in graph.edges if e.edge_id == "direct")
    parallel = edge(graph, "0-parallel", direct.from_state_id, direct.to_state_id, 3300)
    graph = replace(
        graph,
        states=graph.states + (loop_state,),
        edges=graph.edges + (loop, parallel),
        operations=(replace(graph.operations[0], seconds=0), loop_operation),
    )
    result = build_reference(graph, scope)
    assert result.seconds == 3300 and result.path is not None
    # Equal cost: fewer hops beats any lexicographically attractive zero loop.
    assert result.path.edge_ids == ("0-parallel",)
    assert len(result.path.arcs) == 1
    without_direct = replace(
        graph, edges=tuple(e for e in graph.edges if e.edge_id not in ("direct", "0-parallel"))
    )
    path = build_reference(without_direct, scope).path
    assert path is not None and path.edge_ids == ("rail", "air")
    rng = random.Random(42)
    for _ in range(12):
        states, edges = list(graph.states), list(graph.edges)
        rng.shuffle(states)
        rng.shuffle(edges)
        permuted = replace(graph, states=tuple(states), edges=tuple(edges))
        assert (
            build_reference(
                permuted, replace(scope, allowed_modes=tuple(reversed(scope.allowed_modes)))
            )
            == result
        )


def test_equal_parallel_arcs_choose_stable_lexicographic_id() -> None:
    graph, scope = inputs()
    a = edge(graph, "aa", "A:air", "B:air", 1)
    z = edge(graph, "zz", "A:air", "B:air", 1)
    result = build_reference(replace(graph, edges=(z, a)), scope)
    assert result.path is not None and result.path.edge_ids == ("aa",)


def test_stable_identity_across_fresh_process_hash_seeds(tmp_path: Path) -> None:
    script = """import runpy,sys
m=runpy.run_path(sys.argv[1]); g,s=m['demo_inputs']()
from a2_engine import build_reference
print(build_reference(g,s).reference_id)"""
    outputs = set()
    source = str(EXAMPLE.parents[1] / "src")
    for seed in ("0", "1", "27", "random"):
        process = subprocess.run(
            [sys.executable, "-c", script, str(EXAMPLE)],
            cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": source, "PYTHONHASHSEED": seed},
            check=True,
            capture_output=True,
            text=True,
        )
        outputs.add(process.stdout.strip())
    assert len(outputs) == 1


@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), -float("inf"), -1, True, "4", 10**500]
)
def test_invalid_numbers_rejected_before_search(value: Any) -> None:
    graph, _ = inputs()
    with pytest.raises(ValueError):
        replace(graph.edges[0].calibrations[0], seconds=value)
    with pytest.raises(ValueError):
        replace(graph.operations[0], seconds=value)
    with pytest.raises(ValueError):
        replace(graph.edges[0], length_m=value)


@pytest.mark.parametrize(
    "change",
    [
        "units",
        "semantics",
        "version",
        "empty_evidence",
        "blank_id",
        "duplicate_id",
        "conflicting_id",
        "dangling_edge",
        "dangling_operation",
        "reverse_sample",
        "snapshot",
        "operation_scope",
        "phase",
        "mode",
        "overlap",
        "empty_boundary",
        "same_territory",
        "invalid_compatibility",
        "geometry",
        "length_basis",
    ],
)
def test_invalid_inputs_rejected(change: str) -> None:
    graph, scope = inputs()
    e = graph.edges[0]
    c = e.calibrations[0]
    with pytest.raises(ValueError):
        if change == "units":
            replace(c, units=cast(Any, "minutes"))
        elif change == "semantics":
            replace(c, semantics=cast(Any, "preference_cost"))
        elif change == "version":
            replace(graph.network, profile_version="old")
        elif change == "empty_evidence":
            replace(c, evidence_refs=())
        elif change == "blank_id":
            replace(c, calibration_id=" ")
        elif change == "duplicate_id":
            replace(graph, edges=(e, e))
        elif change == "conflicting_id":
            replace(graph, edges=(e, replace(e, calibrations=())))
        elif change == "dangling_edge":
            build_reference(replace(graph, edges=(replace(e, to_state_id="missing"),)), scope)
        elif change == "dangling_operation":
            build_reference(
                replace(graph, operations=(replace(graph.operations[0], to_state_id="missing"),)),
                scope,
            )
        elif change == "reverse_sample":
            build_reference(
                replace(
                    graph,
                    edges=(
                        replace(
                            e,
                            calibrations=(
                                replace(
                                    c, from_state_id=c.to_state_id, to_state_id=c.from_state_id
                                ),
                            ),
                        ),
                    ),
                ),
                scope,
            )
        elif change == "snapshot":
            build_reference(
                replace(
                    graph,
                    edges=(
                        replace(
                            e,
                            calibrations=(
                                replace(c, network=replace(graph.network, snapshot_id="other")),
                            ),
                        ),
                    ),
                ),
                scope,
            )
        elif change == "operation_scope":
            build_reference(
                replace(
                    graph,
                    operations=(
                        replace(
                            graph.operations[0],
                            network=replace(graph.network, applicability_id="other"),
                        ),
                    ),
                ),
                scope,
            )
        elif change == "phase":
            build_reference(replace(graph, edges=(replace(e, from_state_id="X:rail"),)), scope)
        elif change == "mode":
            build_reference(replace(graph, edges=(replace(e, from_state_id="A:rail"),)), scope)
        elif change == "overlap":
            replace(scope, destination_node_ids=("A",))
        elif change == "empty_boundary":
            replace(scope, origin_node_ids=())
        elif change == "same_territory":
            replace(scope, destination_territory_id=scope.origin_territory_id)
        elif change == "invalid_compatibility":
            replace(e, compatible=cast(Any, 1))
        elif change == "geometry":
            replace(e, geometry_ref=" ")
        else:
            replace(e, length_m=10)


def road_inputs() -> tuple[ReferenceGraph, ReferenceScope]:
    graph, scope = inputs()
    build = RoadBuild(
        "road-snapshot",
        "source-road",
        "valhalla-build",
        "road-graph",
        "auto-freeflow-v1",
        "auto",
        '{"speed_types":["freeflow"]}',
        ("ev:road-build",),
    )
    network = replace(graph.network, road_build=build)
    states = (
        ReferenceState("road:A", "A", "departure", "road", "car-present"),
        ReferenceState("road:B", "B", "arrival", "road", "car-present"),
    )
    calibration = ReferenceCalibration(
        "road:sample",
        "road",
        "road:A",
        "road:B",
        network,
        9000,
        "static_freeflow_elapsed",
        ("ev:physical-seconds",),
        road_build=build,
    )
    e = ReferenceEdge(
        "road",
        "road:A",
        "road:B",
        (calibration,),
        ("ev:bounded-route",),
        boundary_semantics="logical_connection",
        compatible=True,
    )
    return replace(graph, network=network, states=states, edges=(e,), operations=()), replace(
        scope, network=network, allowed_modes=("road",)
    )


def test_fixed_road_profile_and_inconsistency_preserve_A() -> None:
    graph, scope = road_inputs()
    result = build_reference(graph, scope)
    scalar = reference_time_from_result(
        result, ReferenceComparison(scope, "A", "B", "live"), config=load_passenger_config()
    )
    car = intercity_car_index("car", 7200, scalar, config=load_passenger_config())
    assert result.seconds == scalar.seconds == 9000
    assert car.path_index.value == 100
    assert "REFERENCE_INCONSISTENCY" in car.diagnostics
    assert result.path is not None and "ev:road-build" in result.path.evidence_refs


@pytest.mark.parametrize(
    "change",
    ["missing_build", "preference", "other_build", "conflicting_costs", "road_data_on_rail"],
)
def test_road_semantics_and_build_not_inferred(change: str) -> None:
    graph, scope = road_inputs()
    e = graph.edges[0]
    c = e.calibrations[0]
    if change == "missing_build":
        c = replace(c, road_build=None)
    elif change == "preference":
        c = replace(c, semantics="running_time")
    elif change == "other_build":
        assert c.road_build is not None
        c = replace(c, road_build=replace(c.road_build, build_id="other"))
    elif change == "conflicting_costs":
        e = replace(e, calibrations=(c, replace(c, calibration_id="different", seconds=100)))
    else:
        states = tuple(replace(s, mode="rail") for s in graph.states)
        graph = replace(graph, states=states)
    if change != "conflicting_costs":
        e = replace(e, calibrations=(c,))
    with pytest.raises(ValueError):
        build_reference(replace(graph, edges=(e,)), scope)


def test_sum_overflow_not_enqueued_as_infinity() -> None:
    graph, scope = inputs()
    huge = sys.float_info.max
    edges = (
        edge(graph, "rail", "A:rail", "X:rail", huge),
        edge(graph, "air", "X:air", "B:air", huge),
    )
    with pytest.raises(ValueError, match="SUM_OVERFLOW"):
        build_reference(replace(graph, edges=edges), scope)


def test_no_intermediate_float_rounding_changes_the_minimum() -> None:
    graph, scope = inputs()
    # Both totals round to 1e16 in float arithmetic; the direct edge is exactly smaller.
    edges = (
        edge(graph, "a-rail", "A:rail", "X:rail", 1e16),
        edge(graph, "a-air", "X:air", "B:air", 1),
        edge(graph, "z-direct", "A:air", "B:air", 1e16),
    )
    result = build_reference(
        replace(graph, edges=edges, operations=(replace(graph.operations[0], seconds=0),)), scope
    )
    assert result.path is not None and result.path.edge_ids == ("z-direct",)


def test_against_independent_exhaustive_state_path_oracle() -> None:
    rng = random.Random(479)
    for case in range(45):
        graph, scope = inputs()
        states = tuple(
            ReferenceState(f"{node}:{phase}", node, phase, "rail", f"ctx:{node}:{phase}")
            for node in ("A", "X", "Y", "B")
            for phase in ("departure", "arrival")
        )
        edges = tuple(
            edge(
                graph,
                f"e:{a}:{b}:{case}",
                f"{a}:departure",
                f"{b}:arrival",
                rng.randrange(0, 12),
                rng.randrange(0, 12),
            )
            for a in ("A", "X", "Y")
            for b in ("X", "Y", "B")
            if a != b and rng.random() < 0.8
        )
        operations = tuple(
            ReferenceOperation(
                f"op:{node}",
                f"{node}:arrival",
                f"{node}:departure",
                graph.network,
                rng.randrange(0, 4),
                "transfer",
                ("ev:operation",),
                compatible=True,
            )
            for node in ("X", "Y")
        )
        graph = replace(graph, states=states, edges=edges, operations=operations)
        scope = replace(scope, allowed_modes=("rail",))
        # Oracle enumerates simple state paths from RAW samples/operations, not calibrated arcs,
        # not Dijkstra, and never uses engine's labels/queue/predecessor implementation.
        connections: list[tuple[str, str, str, Fraction]] = [
            (
                e.edge_id,
                e.from_state_id,
                e.to_state_id,
                min(Fraction(c.seconds) for c in e.calibrations),
            )
            for e in edges
        ] + [
            (o.operation_id, o.from_state_id, o.to_state_id, Fraction(o.seconds))
            for o in operations
            if o.seconds is not None
        ]
        solutions: list[tuple[Fraction, int, tuple[str, ...]]] = []

        def enumerate_paths(
            state: str, visited: frozenset[str], ids: tuple[str, ...], cost: Fraction
        ) -> None:
            if state == "B:arrival":
                solutions.append((cost, len(ids), ids))
                return
            for name, start, end, seconds in connections:
                if start == state and end not in visited:
                    enumerate_paths(end, visited | {end}, ids + (name,), cost + seconds)

        enumerate_paths("A:departure", frozenset({"A:departure"}), (), Fraction(0))
        result = build_reference(graph, scope)
        if solutions:
            expected = min(solutions)
            assert result.path is not None
            assert (
                Fraction(result.path.seconds),
                len(result.path.arcs),
                tuple(a.arc_id for a in result.path.arcs),
            ) == expected
        else:
            assert result.seconds is None and result.search_status == "no_path_proven"
        # Calibration itself exposes all selected provenance and the immutable accepted graph.
        assert calibrate_reference_graph(graph, scope).graph == graph


def test_evidence_and_acceptance_are_bound_into_identity_not_fabricated() -> None:
    graph, scope = inputs()
    original = build_reference(graph, scope)
    direct = next(e for e in graph.edges if e.edge_id == "direct")
    sample = direct.calibrations[0]
    changed = replace(direct, calibrations=(replace(sample, evidence_refs=("ev:other-receipt",)),))
    updated = replace(
        graph, edges=tuple(changed if e.edge_id == direct.edge_id else e for e in graph.edges)
    )
    # A change even in unused calibration provenance invalidates the accepted graph identity.
    assert build_reference(updated, scope).reference_id != original.reference_id
    with pytest.raises(ValueError):
        replace(graph.network, evidence_refs=(" ",))
    with pytest.raises(ValueError):
        replace(sample, evidence_refs=("ev:same", "ev:same"))
    with pytest.raises(ValueError):
        build_reference(
            graph, replace(scope, network=replace(scope.network, acceptance_id="other"))
        )


def test_source_collections_are_copied_and_frozen() -> None:
    graph, scope = inputs()
    values = list(scope.origin_node_ids)
    normalized = replace(scope, origin_node_ids=cast(Any, values))
    values.append("outside")
    assert normalized.origin_node_ids == ("A",)
    states = list(graph.states)
    frozen_graph = replace(graph, states=cast(Any, states))
    states.clear()
    assert frozen_graph.states == graph.states
    assert build_reference(frozen_graph, normalized).seconds == 3600


def test_explicit_zero_operation_is_counted_once() -> None:
    graph, scope = inputs()
    result = build_reference(
        replace(graph, operations=(replace(graph.operations[0], seconds=0),)), scope
    )
    assert result.seconds == 3300 and result.path is not None
    assert result.path.operation_ids == ("transfer",)
    assert result.path.operation_seconds == 0
    assert result.completeness == "full"


def test_allowed_modes_do_not_import_unknown_out_of_scope_alternative() -> None:
    graph, scope = inputs()
    direct = next(e for e in graph.edges if e.edge_id == "direct")
    graph = replace(graph, edges=(replace(direct, calibrations=()),))
    # The unknown air edge is explicitly outside this rail-only comparison area.
    result = build_reference(graph, replace(scope, allowed_modes=("rail",)))
    assert result.seconds is None and result.completeness == "full"
    assert result.search_status == "no_path_proven"


def test_absent_transfer_never_connects_equal_physical_nodes() -> None:
    graph, scope = inputs()
    result = build_reference(replace(graph, operations=()), scope)
    assert (
        result.seconds == 5000
    )  # Accepted full scope asserts there are no other permitted operations.
    assert result.path is not None and result.path.operation_ids == ()


@pytest.mark.parametrize("traffic", ["live", "typical", "historical"])
def test_reference_build_rejects_dynamic_road_costs(traffic: Any) -> None:
    graph, _ = road_inputs()
    assert graph.network.road_build is not None
    with pytest.raises(ValueError):
        replace(graph.network.road_build, traffic_mode=traffic)


def test_reference_inconsistency_preserved_in_passenger_formula() -> None:
    graph, scope = road_inputs()
    result = build_reference(graph, scope)
    config = load_passenger_config()
    scalar = reference_time_from_result(result, ReferenceComparison(scope, "A", "B"), config=config)
    passenger = intercity_passenger_index(
        PassengerChain("chain", 7200, 43200), scalar, config=config
    )
    assert passenger.path_index.value == 100
    assert passenger.index.value == pytest.approx(50**0.5 * 10)
    assert "REFERENCE_INCONSISTENCY" in passenger.diagnostics
    assert scalar.seconds == result.seconds == 9000


def test_multi_source_can_improve_a_shared_state_without_conflating_origins() -> None:
    graph, scope = inputs()
    second = ReferenceState("A2:rail", "A2", "departure", "rail", "other-origin")
    faster = edge(graph, "second-origin", "A2:rail", "X:rail", 10)
    graph = replace(graph, states=graph.states + (second,), edges=graph.edges + (faster,))
    scope = replace(scope, origin_node_ids=("A", "A2"))
    result = build_reference(graph, scope)
    assert result.seconds == 1810 and result.path is not None
    assert result.path.origin_node_id == "A2" and result.path.destination_node_id == "B"


def test_exact_sums_choose_correct_equal_hop_operation() -> None:
    graph, scope = inputs()
    edges = (edge(graph, "rail", "A:rail", "X:rail", 1e16), edge(graph, "air", "X:air", "B:air", 1))
    slower = replace(graph.operations[0], operation_id="a-lexically-first", seconds=1)
    faster = replace(graph.operations[0], operation_id="z-truly-faster", seconds=0)
    result = build_reference(replace(graph, edges=edges, operations=(slower, faster)), scope)
    assert result.path is not None
    # IEEE float addition loses the slower operation's extra second at 1e16.
    # Exact labels must select z, even though lexicographic tie-breaking prefers a.
    assert result.path.operation_ids == ("z-truly-faster",)


def test_private_car_carriage_requires_explicit_accepted_operation() -> None:
    road_graph, road_scope = road_inputs()
    rail_states = (
        ReferenceState("rail:A", "A", "departure", "rail", "no-private-car"),
        ReferenceState("rail:B", "B", "arrival", "rail", "no-private-car"),
    )
    # No automatic movement from the road arrival to the rail departure at B.
    onward = ReferenceState("rail:C", "C", "arrival", "rail", "no-private-car")
    depart_b = ReferenceState("rail:B:depart", "B", "departure", "rail", "car-carriage-unconfirmed")
    graph = replace(
        road_graph,
        states=road_graph.states + rail_states + (onward, depart_b),
        edges=road_graph.edges + (edge(road_graph, "onward", "rail:B:depart", "rail:C", 10),),
    )
    scope = replace(
        road_scope,
        destination_territory_id="city-C",
        destination_node_ids=("C",),
        allowed_modes=("road", "rail"),
    )
    assert build_reference(graph, scope).search_status == "no_path_proven"
    operation = ReferenceOperation(
        "carriage",
        "road:B",
        "rail:B:depart",
        graph.network,
        0,
        "accepted_carriage",
        ("ev:carriage",),
        compatible=None,
    )
    unknown = build_reference(replace(graph, operations=(operation,)), scope)
    assert unknown.seconds is None and unknown.completeness == "partial"
    confirmed = build_reference(
        replace(graph, operations=(replace(operation, compatible=True),)), scope
    )
    assert confirmed.seconds == 9010 and confirmed.path is not None
    assert confirmed.path.operation_ids == ("carriage",)


def test_multiple_internal_operations_and_pure_zero_operation_cycle() -> None:
    graph, scope = inputs()
    t1 = ReferenceState("transfer:1", "X", "transfer", "rail", "secure-area-entry")
    t2 = ReferenceState("transfer:2", "X", "transfer", "air", "secure-area")
    operations = tuple(
        ReferenceOperation(
            name,
            start,
            end,
            graph.network,
            seconds,
            "required_operation",
            ("ev:operations",),
            compatible=True,
        )
        for name, start, end, seconds in (
            ("enter", "X:rail", "transfer:1", 100),
            ("inside", "transfer:1", "transfer:2", 0),
            ("0-back", "transfer:2", "transfer:1", 0),
            ("0-self", "transfer:1", "transfer:1", 0),
            ("exit", "transfer:2", "X:air", 200),
        )
    )
    result = build_reference(
        replace(graph, states=graph.states + (t1, t2), operations=operations), scope
    )
    assert result.seconds == 3600 and result.path is not None
    assert result.path.operation_ids == ("enter", "inside", "exit")
    assert result.path.operation_seconds == 300


def test_independent_oracle_for_internal_operation_state_graphs() -> None:
    rng = random.Random(882)
    for _ in range(20):
        graph, scope = inputs()
        transfers = tuple(
            ReferenceState(f"t:{i}", "X", "transfer", "rail", f"context:{i}") for i in range(4)
        )
        source_states = ("X:rail",) + tuple(s.state_id for s in transfers)
        target_states = tuple(s.state_id for s in transfers) + ("X:air",)
        operations = tuple(
            ReferenceOperation(
                f"op:{a}:{b}",
                a,
                b,
                graph.network,
                rng.randrange(3),
                "internal",
                ("ev:ops",),
                compatible=True,
            )
            for a in source_states
            for b in target_states
            if rng.random() < 0.35
        )
        graph = replace(graph, states=graph.states + transfers, operations=operations)
        # Independent exhaustive enumeration of raw, simple operation-state paths.
        totals: list[int] = []

        def enumerate_operations(state: str, seen: frozenset[str], seconds: int) -> None:
            if state == "X:air":
                totals.append(seconds)
                return
            for op in operations:
                if op.from_state_id == state and op.to_state_id not in seen:
                    assert op.seconds is not None
                    enumerate_operations(
                        op.to_state_id, seen | {op.to_state_id}, seconds + int(op.seconds)
                    )

        enumerate_operations("X:rail", frozenset({"X:rail"}), 0)
        expected = min([5000] + [3300 + total for total in totals])
        assert build_reference(graph, scope).seconds == expected


def test_compatibility_must_be_supplied_explicitly() -> None:
    graph, _ = inputs()
    operation: Any = ReferenceOperation
    with pytest.raises(TypeError, match="compatible"):
        operation(
            "missing-confirmation",
            "X:rail",
            "X:air",
            graph.network,
            0,
            "transfer",
            ("ev:transfer",),
        )
    movement: Any = ReferenceEdge
    with pytest.raises(TypeError, match="compatible"):
        movement("missing-confirmation", "A:rail", "X:rail", (), ("ev:edge",))
