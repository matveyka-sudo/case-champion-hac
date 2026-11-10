"""Independent closed-ball and turn-state counterexamples; all graphs synthetic."""

import hashlib
import json
import random
import struct
from dataclasses import replace
from fractions import Fraction

import pytest
from a2_engine.urban.walk.source_bound import (
    NativeEdge,
    NativeEndpoint,
    NativeWalkCandidate,
    NativeWalkExport,
    binary_fraction,
)
from a2_engine.urban.walk.turn_certificate import (
    NativeTurnPlan,
    NativeTurnWeights,
    check_native_turn_minimum,
    load_native_turn_weights,
    make_native_turn_incumbent,
    plan_native_turn_pairs,
    solve_native_turn_plan,
)

SCOPE = "a" * 64


def bits(value: Fraction | float | int) -> int:
    return int.from_bytes(struct.pack("<f", float(value)), "little")


def replay(candidate: NativeWalkCandidate, turns: tuple[Fraction, ...]) -> str:
    rows = [
        {
            "kind": "replay",
            "id": edge,
            "fraction_bits": fraction,
            "edge_seconds_bits": bits(seconds),
            "turn_seconds_bits": bits(turn),
        }
        for (edge, fraction), seconds, turn in zip(
            candidate.traversals, candidate.edge_seconds, turns, strict=True
        )
    ]
    return "\n".join(
        json.dumps(row)
        for row in [*rows, {"kind": "replay_footer", "feasible": True, "edges": len(rows)}]
    )


def fixture(
    *,
    terminal_edge_seconds: int = 2,
    source_seconds: int = 2,
    source_percent: Fraction = Fraction(1, 2),
    hierarchy: bool = False,
) -> tuple[NativeWalkExport, NativeWalkCandidate, str]:
    final_percent = Fraction(float(1 / terminal_edge_seconds))
    origin_cost = binary_fraction(
        bits(Fraction(source_seconds) * binary_fraction(bits(1 - source_percent)))
    )
    edges = (
        NativeEdge(10, 0, 1, Fraction(source_seconds), True),
        NativeEdge(20, 5 if hierarchy else 1, 2, Fraction(2), True),
        NativeEdge(21, 2, 3, Fraction(2), True),
        NativeEdge(30, 3, 4, Fraction(terminal_edge_seconds), True),
    )
    graph = NativeWalkExport(
        "b" * 64,
        frozenset(range(6)),
        edges,
        ((1, 5),) if hierarchy else (),
        (
            NativeEndpoint(0, 0, 10, source_percent, 0, 0, origin_cost),
            NativeEndpoint(1, 0, 30, final_percent, 0, 0, Fraction(1)),
        ),
        frozenset({0}),
    )
    candidate = NativeWalkCandidate(
        0,
        0,
        ((10, bits(1 - source_percent)), (20, bits(1)), (21, bits(1)), (30, bits(final_percent))),
        origin_cost + 5,
        (origin_cost, Fraction(2), Fraction(2), Fraction(1)),
        (Fraction(),) * 4,
    )
    turns = (
        Fraction(),
        Fraction(1, 4) if source_percent < 1 else Fraction(),
        Fraction(1, 2),
        Fraction(1, 4),
    )
    return graph, candidate, replay(candidate, turns)


def weights(
    plan: NativeTurnPlan, overrides: dict[tuple[int, int], Fraction] | None = None
) -> NativeTurnWeights:
    values = {(10, 20): Fraction(1, 4), (20, 21): Fraction(1, 2), (21, 30): Fraction(1, 4)}
    values.update(overrides or {})
    rows = [
        {
            "kind": "turn_pairs_header",
            "scope_id": plan.incumbent.source_scope_id,
            "plan_sha256": plan.fingerprint,
        },
        *[
            {
                "kind": "turn_pair",
                "from": a,
                "to": b,
                "seconds_bits": bits(values.get((a, b), Fraction())),
            }
            for a, b in plan.pairs
        ],
        {"kind": "turn_pairs_footer", "pairs": len(plan.pairs)},
    ]
    text = "\n".join(json.dumps(row) for row in rows)
    return load_native_turn_weights(
        text, plan, expected_sha256=hashlib.sha256(text.encode()).hexdigest()
    )


def plan_for(
    graph: NativeWalkExport, candidate: NativeWalkCandidate, observed: str
) -> NativeTurnPlan:
    return plan_native_turn_pairs(
        graph, SCOPE, make_native_turn_incumbent(graph, SCOPE, candidate, observed)
    )


def test_exact_boundary_is_inclusive_and_turn_optimum_replays() -> None:
    graph, candidate, observed = fixture()
    plan = plan_for(graph, candidate, observed)
    assert plan.incumbent.upper_seconds == 7
    assert 4 in plan.ball_nodes and 30 in plan.state_edges  # d(4) == U exactly
    optimum = solve_native_turn_plan(graph, plan, weights(plan))
    assert optimum.lower_seconds == 7
    result = check_native_turn_minimum(optimum, replay(optimum, optimum.lower_turn_seconds))
    assert result.network_minimum_proven and not result.physical_minimum_proven
    with pytest.raises(ValueError, match="CLOSED_BALL_PLAN_CHANGED"):
        solve_native_turn_plan(
            graph, replace(plan, state_edges=plan.state_edges - {30}), weights(plan)
        )


def test_terminal_partial_is_absorbing_even_when_full_end_is_outside_ball() -> None:
    graph, candidate, observed = fixture(terminal_edge_seconds=100)
    plan = plan_for(graph, candidate, observed)
    assert 4 not in plan.ball_nodes and 30 not in plan.state_edges
    assert (21, 30) in plan.pairs
    assert solve_native_turn_plan(graph, plan, weights(plan)).lower_seconds == 7


def test_positive_fraction_with_zero_elapsed_keeps_incoming_turn_state() -> None:
    graph, candidate, _ = fixture(source_seconds=0)
    actual = (Fraction(), Fraction(5), Fraction(1, 2), Fraction(1, 4))
    plan = plan_for(graph, candidate, replay(candidate, actual))
    optimum = solve_native_turn_plan(graph, plan, weights(plan, {(10, 20): Fraction(5)}))
    assert optimum.lower_seconds == Fraction(43, 4)
    assert optimum.lower_turn_seconds[1] == 5


def test_zero_source_fragment_has_no_fictitious_predecessor_turn() -> None:
    graph, candidate, observed = fixture(source_percent=Fraction(1))
    plan = plan_for(graph, candidate, observed)
    optimum = solve_native_turn_plan(graph, plan, weights(plan, {(10, 20): Fraction(100)}))
    assert optimum.lower_seconds == Fraction(23, 4)
    assert optimum.lower_turn_seconds[:2] == (Fraction(), Fraction())


def test_direct_same_edge_survives_an_empty_full_node_ball() -> None:
    graph, _, _ = fixture()
    graph = replace(
        graph,
        endpoints=(
            NativeEndpoint(0, 0, 10, Fraction(1, 4), 0, 0, Fraction(3, 2)),
            NativeEndpoint(1, 0, 10, Fraction(3, 4), 0, 0, Fraction(3, 2)),
        ),
    )
    candidate = NativeWalkCandidate(
        0, 0, ((10, bits(0.5)),), Fraction(1), (Fraction(1),), (Fraction(),)
    )
    plan = plan_for(graph, candidate, replay(candidate, (Fraction(),)))
    assert not plan.ball_nodes and not plan.pairs
    assert solve_native_turn_plan(graph, plan, weights(plan)).lower_seconds == 1


def test_directed_hierarchy_aliases_are_included_without_reversing_them() -> None:
    graph, candidate, observed = fixture(hierarchy=True)
    graph = replace(
        graph,
        nodes=graph.nodes | {6},
        transitions=(*graph.transitions, (6, 1)),
        edges=(*graph.edges, NativeEdge(22, 6, 3, Fraction(1), True)),
    )
    plan = plan_for(graph, candidate, observed)
    assert (10, 20) in plan.pairs and (10, 22) not in plan.pairs
    assert solve_native_turn_plan(graph, plan, weights(plan)).lower_seconds == 7


def test_zero_cost_positive_fraction_cycle_changes_history_and_can_improve() -> None:
    graph, candidate, _ = fixture()
    graph = replace(
        graph,
        edges=(
            *graph.edges,
            NativeEdge(25, 1, 5, Fraction(), True),
            NativeEdge(26, 5, 1, Fraction(), True),
        ),
    )
    observed = replay(candidate, (Fraction(), Fraction(5), Fraction(), Fraction()))
    plan = plan_for(graph, candidate, observed)
    overrides = {pair: Fraction() for pair in plan.pairs}
    overrides[10, 20] = Fraction(5)
    optimum = solve_native_turn_plan(graph, plan, weights(plan, overrides))
    assert optimum.lower_seconds == 6
    assert [edge for edge, _ in optimum.traversals] == [10, 25, 26, 20, 21, 30]


def test_foreign_scope_or_failed_or_modified_incumbent_cannot_authorize_cut() -> None:
    graph, candidate, observed = fixture()
    incumbent = make_native_turn_incumbent(graph, SCOPE, candidate, observed)
    for changed_graph, scope, changed in (
        (graph, "c" * 64, incumbent),
        (replace(graph, content_sha256="d" * 64), SCOPE, incumbent),
        (graph, SCOPE, replace(incumbent, upper_seconds=Fraction(6))),
    ):
        with pytest.raises(ValueError):
            plan_native_turn_pairs(changed_graph, scope, changed)
    with pytest.raises(ValueError):
        make_native_turn_incumbent(
            graph, SCOPE, candidate, observed.replace('"feasible": true', '"feasible": false')
        )


def test_pair_roster_and_source_plan_binding_are_exact() -> None:
    graph, candidate, observed = fixture()
    plan = plan_for(graph, candidate, observed)
    valid = weights(plan)
    for attacked in (
        replace(valid, values=valid.values[:-1]),
        replace(valid, source_scope_id="c" * 64),
        replace(valid, plan_sha256="d" * 64),
        replace(valid, values=(*valid.values, valid.values[0])),
    ):
        with pytest.raises(ValueError):
            solve_native_turn_plan(graph, plan, attacked)


def test_turn_state_minimum_matches_independent_simple_state_path_enumeration() -> None:
    for seed in range(25):
        rng = random.Random(seed)
        network = {
            (1, 2): rng.randrange(1, 4),
            (2, 3): rng.randrange(1, 4),
            (3, 4): rng.randrange(1, 4),
        }
        for start in range(1, 5):
            for end in range(1, 5):
                if start != end and rng.random() < 0.2:
                    network[start, end] = rng.randrange(3)
        internal = tuple(
            NativeEdge(100 + i, a, b, Fraction(seconds), True)
            for i, ((a, b), seconds) in enumerate(sorted(network.items()))
        )
        edges = (
            NativeEdge(10, 0, 1, Fraction(1), True),
            *internal,
            NativeEdge(90, 4, 5, Fraction(2), True),
        )
        graph = NativeWalkExport(
            hashlib.sha256(str(seed).encode()).hexdigest(),
            frozenset(range(6)),
            edges,
            (),
            (
                NativeEndpoint(0, 0, 10, Fraction(1, 2), 0, 0, Fraction(1, 2)),
                NativeEndpoint(1, 0, 90, Fraction(1, 2), 0, 0, Fraction(1)),
            ),
            frozenset({0}),
        )
        turn_map = {
            (a.edge_id, b.edge_id): Fraction(rng.randrange(4))
            for a in edges
            for b in edges
            if a.end == b.start
        }
        chain = [
            next(e for e in internal if (e.start, e.end) == pair)
            for pair in ((1, 2), (2, 3), (3, 4))
        ]
        ids = [10, *(e.edge_id for e in chain), 90]
        running = (Fraction(1, 2), *(e.seconds for e in chain), Fraction(1))
        candidate = NativeWalkCandidate(
            0,
            0,
            tuple((edge, bits(Fraction(1, 2) if i in (0, 4) else 1)) for i, edge in enumerate(ids)),
            sum(running, Fraction()),
            tuple(running),
            (Fraction(),) * 5,
        )
        actual = (Fraction(), *(turn_map[a, b] for a, b in zip(ids, ids[1:])))
        plan = plan_for(graph, candidate, replay(candidate, tuple(actual)))
        optimum = solve_native_turn_plan(graph, plan, weights(plan, turn_map))
        totals: list[Fraction] = []
        edge_map = {e.edge_id: e for e in edges}

        def enumerate_states(previous: int, elapsed: Fraction, visited: frozenset[int]) -> None:
            for edge in edges:
                if edge.start != edge_map[previous].end:
                    continue
                if edge.edge_id == 90:
                    totals.append(elapsed + turn_map[previous, 90] + 1)
                elif edge.edge_id not in visited:
                    enumerate_states(
                        edge.edge_id,
                        elapsed + turn_map[previous, edge.edge_id] + edge.seconds,
                        visited | {edge.edge_id},
                    )

        enumerate_states(10, Fraction(1, 2), frozenset({10}))
        assert optimum.lower_seconds == min(totals), seed
