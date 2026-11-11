"""All graphs, inventories, elapsed values, and evidence here are SYNTHETIC ONLY."""

import random
from dataclasses import replace
from fractions import Fraction
from typing import Any

import pytest
from a2_engine.urban.walk.finite_oracle import (
    FiniteWalkArc,
    FiniteWalkConnector,
    FiniteWalkGraph,
    FiniteWalkInventory,
    finite_walk_graph_digest,
    solve_finite_walk_graph,
    verify_finite_walk_candidate,
)


def inventory(graph: FiniteWalkGraph) -> FiniteWalkInventory:
    """Self-issued TEST inventory; explicitly NOT authoritative real-source evidence."""
    return FiniteWalkInventory(
        graph.scope_id,
        frozenset(graph.state_ids),
        frozenset(a.arc_id for a in graph.arcs),
        frozenset(c.connector_id for c in graph.initial_connectors),
        frozenset(c.connector_id for c in graph.final_connectors),
        finite_walk_graph_digest(graph),
        ("synthetic-exhaustive-state-inventory",),
    )


@pytest.fixture
def graph() -> FiniteWalkGraph:
    return FiniteWalkGraph(
        "synthetic-scope-v1",
        "physical:origin",
        "physical:destination",
        ("s", "a", "b", "t"),
        (
            FiniteWalkArc("alley-in", "s", "a", Fraction(4), Fraction(1)),
            FiniteWalkArc("alley-out", "a", "t", Fraction(3), Fraction(0)),
            FiniteWalkArc("street-in", "s", "b", Fraction(5), Fraction(1)),
            FiniteWalkArc("street-out", "b", "t", Fraction(6), Fraction(0)),
        ),
        (FiniteWalkConnector("initial", "physical:origin", "s", Fraction("9.7")),),
        (FiniteWalkConnector("final", "physical:destination", "t", Fraction("18.5")),),
    )


def verify(graph: FiniteWalkGraph, **changes: Any) -> Any:
    arguments: dict[str, Any] = {
        "initial_connector_id": "initial",
        "arc_ids": ("alley-in", "alley-out"),
        "final_connector_id": "final",
        "reported_total_seconds": Fraction("36.2"),
    }
    return verify_finite_walk_candidate(graph, inventory(graph), **(arguments | changes))


def test_elapsed_objective_and_both_physical_connectors(graph: FiniteWalkGraph) -> None:
    result = verify(graph)
    assert result.total_seconds == Fraction("36.2")
    assert (result.initial_seconds, result.network_seconds, result.final_seconds) == (
        Fraction("9.7"),
        Fraction(8),
        Fraction("18.5"),
    )
    assert result.arc_ids == ("alley-in", "alley-out")
    assert result.settled_state_ids == frozenset(graph.state_ids)
    # A cost multiplier makes the slower street win generalized cost: 12 < 5*8.
    assert 12 < 5 * 8
    with pytest.raises(ValueError, match="IS_NOT_ELAPSED_MINIMUM"):
        verify(graph, arc_ids=("street-in", "street-out"), reported_total_seconds=Fraction("40.2"))


@pytest.mark.parametrize("reported", ["8", "26.5", "17.7", "45.9", "54.7", "35.2", "36.200001"])
def test_omission_double_count_transition_omission_and_rounding_fail(
    graph: FiniteWalkGraph, reported: str
) -> None:
    with pytest.raises(ValueError, match="ELAPSED_TOTAL_MISMATCH"):
        verify(graph, reported_total_seconds=Fraction(reported))


def test_synthetic_decimal_weights_retain_exact_total(graph: FiniteWalkGraph) -> None:
    # Numeric regression only; the pilot's equality of numbers is not route evidence.
    graph = replace(
        graph, arcs=(FiniteWalkArc("network", "s", "t", Fraction("519.204"), Fraction(0)),)
    )
    result = solve_finite_walk_graph(graph, inventory(graph))
    assert result.total_seconds == Fraction("547.404")


@pytest.mark.parametrize("field", ["state_ids", "arcs", "initial_connectors", "final_connectors"])
def test_truncated_graph_and_incomplete_cut_fail_against_retained_inventory(
    graph: FiniteWalkGraph, field: str
) -> None:
    retained = inventory(graph)
    truncated = replace(graph, **{field: getattr(graph, field)[1:]})
    with pytest.raises(ValueError, match="INVENTORY_MISMATCH"):
        solve_finite_walk_graph(truncated, retained)


def test_self_issued_shortened_inventory_is_explicitly_outside_real_proof_boundary(
    graph: FiniteWalkGraph,
) -> None:
    # A checksum cannot certify that an exporter omitted no real alternatives.
    shortened = replace(graph, arcs=graph.arcs[2:])
    result = solve_finite_walk_graph(shortened, inventory(shortened))
    assert result.total_seconds == Fraction("40.2")
    assert solve_finite_walk_graph(graph, inventory(graph)).total_seconds == Fraction("36.2")
    assert not hasattr(result, "minimum_proven")


def test_inventory_checks_content_not_just_arc_roster(graph: FiniteWalkGraph) -> None:
    retained = inventory(graph)
    changed = replace(
        graph, arcs=(replace(graph.arcs[0], edge_seconds=Fraction(40)), *graph.arcs[1:])
    )
    with pytest.raises(ValueError, match="CONTENT_MISMATCH"):
        solve_finite_walk_graph(changed, retained)
    with pytest.raises(ValueError, match="SCOPE_MISMATCH"):
        solve_finite_walk_graph(replace(graph, scope_id="foreign"), retained)


@pytest.mark.parametrize("field", ["state_ids", "arcs", "initial_connectors", "final_connectors"])
def test_duplicate_ids_are_not_hidden_by_roster_sets(graph: FiniteWalkGraph, field: str) -> None:
    entries = getattr(graph, field)
    changes: dict[str, Any] = {field: (*entries, entries[0])}
    duplicated = replace(graph, **changes)
    with pytest.raises(ValueError, match="DUPLICATE_FINITE_GRAPH_IDENTIFIER"):
        solve_finite_walk_graph(duplicated, inventory(duplicated))


def test_arc_to_missing_state_fails_even_with_self_issued_inventory(graph: FiniteWalkGraph) -> None:
    graph = replace(graph, arcs=(replace(graph.arcs[0], to_state="missing"), *graph.arcs[1:]))
    with pytest.raises(ValueError, match="STATE_MISSING"):
        solve_finite_walk_graph(graph, inventory(graph))


@pytest.mark.parametrize("field", ["initial_connectors", "final_connectors"])
def test_empty_endpoint_alternatives_fail_closed(graph: FiniteWalkGraph, field: str) -> None:
    changes: dict[str, Any] = {field: ()}
    graph = replace(graph, **changes)
    with pytest.raises(ValueError, match="ENDPOINTS_INCOMPLETE"):
        solve_finite_walk_graph(graph, inventory(graph))


@pytest.mark.parametrize(
    "changes",
    [
        {"physical_node_id": "foreign-physical"},
        {"state_id": "missing-state"},
        {"coordinate_precision_digits": 6},
    ],
)
def test_unmatched_and_rounded_endpoint_cannot_prove_exact_minimum(
    graph: FiniteWalkGraph, changes: dict[str, Any]
) -> None:
    graph = replace(graph, initial_connectors=(replace(graph.initial_connectors[0], **changes),))
    with pytest.raises(ValueError, match="ENDPOINT_MISMATCH|EXACT_FINITE_GRAPH_ENDPOINT_REQUIRED"):
        solve_finite_walk_graph(graph, inventory(graph))


@pytest.mark.parametrize(
    "bad_seconds", [-1, 0, 0.1, True, float("nan"), float("inf"), Fraction(-1)]
)
def test_weights_require_exact_nonnegative_rational_seconds(bad_seconds: Any) -> None:
    for factory in (
        lambda: FiniteWalkArc("a", "s", "t", bad_seconds, Fraction(0)),
        lambda: FiniteWalkArc("a", "s", "t", Fraction(0), bad_seconds),
        lambda: FiniteWalkConnector("c", "physical", "s", bad_seconds),
    ):
        with pytest.raises(ValueError, match="EXACT_NONNEGATIVE_FRACTION_SECONDS_REQUIRED"):
            factory()


def test_all_endpoint_alternatives_participate_in_total_minimum(graph: FiniteWalkGraph) -> None:
    graph = replace(
        graph,
        initial_connectors=(
            *graph.initial_connectors,
            FiniteWalkConnector("initial-b", "physical:origin", "b", Fraction(1)),
        ),
        final_connectors=(
            *graph.final_connectors,
            FiniteWalkConnector("final-a", "physical:destination", "a", Fraction(2)),
        ),
    )
    result = solve_finite_walk_graph(graph, inventory(graph))
    assert result.total_seconds == Fraction("16.7")  # 9.7 + (4+1) + 2.
    assert result.initial_connector_id == "initial" and result.final_connector_id == "final-a"


def test_directed_path_and_candidate_final_state_must_match(graph: FiniteWalkGraph) -> None:
    with pytest.raises(ValueError, match="DIRECTED_PATH_MISMATCH"):
        verify(graph, arc_ids=("alley-out", "alley-in"))
    with pytest.raises(ValueError, match="FINAL_ENDPOINT_MISMATCH"):
        verify(graph, arc_ids=("alley-in",))
    with pytest.raises(ValueError, match="CONNECTOR_UNKNOWN"):
        verify(graph, initial_connector_id="missing")


def test_same_network_state_still_counts_both_physical_connectors(graph: FiniteWalkGraph) -> None:
    graph = replace(graph, final_connectors=(replace(graph.final_connectors[0], state_id="s"),))
    result = solve_finite_walk_graph(graph, inventory(graph))
    assert result.total_seconds == Fraction("28.2") and result.network_seconds == 0
    assert result.arc_ids == ()


def test_same_physical_endpoint_admits_zero_stay_path(graph: FiniteWalkGraph) -> None:
    graph = replace(
        graph,
        destination_physical_node_id=graph.origin_physical_node_id,
        final_connectors=(
            replace(graph.final_connectors[0], physical_node_id=graph.origin_physical_node_id),
        ),
    )
    result = solve_finite_walk_graph(graph, inventory(graph))
    assert result.total_seconds == 0 and result.arc_ids == ()
    assert result.initial_connector_id is None and result.final_connector_id is None


@pytest.fixture
def physical_stay_graph() -> FiniteWalkGraph:
    return FiniteWalkGraph(
        "synthetic-stay-scope",
        "physical:same",
        "physical:same",
        ("s",),
        (),
        (FiniteWalkConnector("initial", "physical:same", "s", Fraction(1)),),
        (FiniteWalkConnector("final", "physical:same", "s", Fraction(1)),),
    )


def test_solver_zero_stay_round_trips_through_candidate_verifier(
    physical_stay_graph: FiniteWalkGraph,
) -> None:
    retained = inventory(physical_stay_graph)
    result = solve_finite_walk_graph(physical_stay_graph, retained)
    assert result.total_seconds == 0
    assert (
        verify_finite_walk_candidate(
            physical_stay_graph,
            retained,
            initial_connector_id=result.initial_connector_id,
            arc_ids=result.arc_ids,
            final_connector_id=result.final_connector_id,
            reported_total_seconds=Fraction(0),
        )
        == result
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"initial_connector_id": "initial"},
        {"final_connector_id": "final"},
        {"reported_total_seconds": Fraction(1)},
        {"arc_ids": ("zero-loop",)},
    ],
)
def test_empty_stay_rejects_one_connector_nonzero_time_or_network_arcs(
    physical_stay_graph: FiniteWalkGraph, changes: dict[str, Any]
) -> None:
    graph = replace(
        physical_stay_graph,
        arcs=(FiniteWalkArc("zero-loop", "s", "s", Fraction(0), Fraction(0)),),
    )
    arguments: dict[str, Any] = {
        "initial_connector_id": None,
        "arc_ids": (),
        "final_connector_id": None,
        "reported_total_seconds": Fraction(0),
    }
    with pytest.raises(ValueError, match="INVALID_EMPTY_PHYSICAL_STAY_CANDIDATE"):
        verify_finite_walk_candidate(graph, inventory(graph), **(arguments | changes))


@pytest.mark.parametrize("initial_id,final_id", [(None, None), (None, "final"), ("initial", None)])
def test_distinct_physical_endpoints_never_accept_omitted_connectors(
    physical_stay_graph: FiniteWalkGraph, initial_id: str | None, final_id: str | None
) -> None:
    graph = replace(
        physical_stay_graph,
        destination_physical_node_id="physical:different",
        final_connectors=(
            replace(physical_stay_graph.final_connectors[0], physical_node_id="physical:different"),
        ),
    )
    with pytest.raises(ValueError, match="INVALID_EMPTY_PHYSICAL_STAY_CANDIDATE"):
        verify_finite_walk_candidate(
            graph,
            inventory(graph),
            initial_connector_id=initial_id,
            arc_ids=(),
            final_connector_id=final_id,
            reported_total_seconds=Fraction(0),
        )


@pytest.mark.parametrize(
    "reported,reason",
    [(Fraction(0), "ELAPSED_TOTAL_MISMATCH"), (Fraction(2), "IS_NOT_ELAPSED_MINIMUM")],
)
def test_positive_connector_detour_is_not_the_zero_physical_stay(
    physical_stay_graph: FiniteWalkGraph, reported: Fraction, reason: str
) -> None:
    with pytest.raises(ValueError, match=reason):
        verify_finite_walk_candidate(
            physical_stay_graph,
            inventory(physical_stay_graph),
            initial_connector_id="initial",
            arc_ids=(),
            final_connector_id="final",
            reported_total_seconds=reported,
        )


def test_empty_physical_stay_still_requires_verified_input_inventory(
    physical_stay_graph: FiniteWalkGraph,
) -> None:
    retained = inventory(physical_stay_graph)
    with pytest.raises(ValueError, match="INVENTORY_MISMATCH"):
        verify_finite_walk_candidate(
            replace(physical_stay_graph, final_connectors=()),
            retained,
            initial_connector_id=None,
            arc_ids=(),
            final_connector_id=None,
            reported_total_seconds=Fraction(0),
        )


def test_zero_cycles_and_distinct_tied_paths(graph: FiniteWalkGraph) -> None:
    graph = replace(
        graph,
        arcs=tuple(
            replace(a, edge_seconds=Fraction(0), transition_seconds=Fraction(0)) for a in graph.arcs
        )
        + (FiniteWalkArc("cycle", "a", "s", Fraction(0), Fraction(0)),),
        initial_connectors=(replace(graph.initial_connectors[0], seconds=Fraction(0)),),
        final_connectors=(replace(graph.final_connectors[0], seconds=Fraction(0)),),
    )
    result = verify(graph, reported_total_seconds=Fraction(0))
    assert result.total_seconds == 0 and len(result.arc_ids) == 2
    verify(graph, arc_ids=("street-in", "street-out"), reported_total_seconds=Fraction(0))
    verify(
        graph,
        arc_ids=("alley-in", "cycle", "street-in", "street-out"),
        reported_total_seconds=Fraction(0),
    )


def test_directed_unreachable_is_only_an_inventory_result(graph: FiniteWalkGraph) -> None:
    graph = replace(
        graph,
        arcs=tuple(replace(a, from_state=a.to_state, to_state=a.from_state) for a in graph.arcs),
    )
    result = solve_finite_walk_graph(graph, inventory(graph))
    assert result.total_seconds is None and result.initial_seconds is None
    assert result.settled_state_ids == frozenset({"s"})
    assert not hasattr(result, "confirmed_unreachable")


def test_turn_history_states_cannot_be_collapsed_to_one_junction(graph: FiniteWalkGraph) -> None:
    graph = replace(
        graph,
        state_ids=("s", "junction:from-a", "b", "junction:from-b", "t"),
        arcs=(
            FiniteWalkArc("a", "s", "junction:from-a", Fraction(1), Fraction(0)),
            FiniteWalkArc("a-turn", "junction:from-a", "t", Fraction(1), Fraction(100)),
            FiniteWalkArc("b", "s", "b", Fraction(2), Fraction(0)),
            FiniteWalkArc("b-in", "b", "junction:from-b", Fraction(1), Fraction(0)),
            FiniteWalkArc("b-turn", "junction:from-b", "t", Fraction(1), Fraction(0)),
        ),
    )
    result = solve_finite_walk_graph(graph, inventory(graph))
    assert result.network_seconds == 4 and result.arc_ids == ("b", "b-in", "b-turn")


def exhaustive_simple_path_minimum(graph: FiniteWalkGraph) -> Fraction | None:
    """Independent enumeration, no heap, distance labels, or oracle implementation reuse."""
    totals: list[Fraction] = []

    def visit(state: str, elapsed: Fraction, visited: frozenset[str]) -> None:
        totals.extend(elapsed + c.seconds for c in graph.final_connectors if c.state_id == state)
        for arc in graph.arcs:
            if arc.from_state == state and arc.to_state not in visited:
                visit(
                    arc.to_state,
                    elapsed + arc.edge_seconds + arc.transition_seconds,
                    visited | {arc.to_state},
                )

    for connector in graph.initial_connectors:
        visit(connector.state_id, connector.seconds, frozenset({connector.state_id}))
    return min(totals) if totals else None


def test_dijkstra_against_independent_exhaustive_paths_and_input_permutations(
    graph: FiniteWalkGraph,
) -> None:
    rng = random.Random(20261110)
    for _ in range(75):
        states = tuple(str(i) for i in range(6))
        candidate = replace(
            graph,
            state_ids=states,
            arcs=tuple(
                FiniteWalkArc(
                    f"{a}->{b}", a, b, Fraction(rng.randrange(8), 7), Fraction(rng.randrange(3), 11)
                )
                for a in states
                for b in states
                if rng.randrange(4) == 0
            ),
            initial_connectors=tuple(
                FiniteWalkConnector(
                    f"initial-{s}", graph.origin_physical_node_id, s, Fraction(rng.randrange(3), 13)
                )
                for s in ("0", "1")
            ),
            final_connectors=tuple(
                FiniteWalkConnector(
                    f"final-{s}",
                    graph.destination_physical_node_id,
                    s,
                    Fraction(rng.randrange(3), 17),
                )
                for s in ("4", "5")
            ),
        )
        retained = inventory(candidate)
        result = solve_finite_walk_graph(candidate, retained)
        assert result.total_seconds == exhaustive_simple_path_minimum(candidate)
        permuted = replace(
            candidate,
            state_ids=tuple(reversed(candidate.state_ids)),
            arcs=tuple(reversed(candidate.arcs)),
            initial_connectors=tuple(reversed(candidate.initial_connectors)),
            final_connectors=tuple(reversed(candidate.final_connectors)),
        )
        assert solve_finite_walk_graph(permuted, retained) == result
