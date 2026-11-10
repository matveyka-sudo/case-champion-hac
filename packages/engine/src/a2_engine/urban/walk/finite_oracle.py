"""Exact reference oracle for a supplied finite, directed, elapsed-time state graph.

This verifies inventory consistency, not the inventory's authority over real roads.
It deliberately has no route-envelope adapter or production ``minimum_proven`` flag.
The source owner must establish state/turn/connector exhaustiveness independently.
"""

import hashlib
import json
from dataclasses import dataclass
from fractions import Fraction
from heapq import heappop, heappush

from a2_engine.urban.models import identifier, references


def _seconds(value: Fraction) -> Fraction:
    # No float, tolerance, hidden unit conversion, or intermediate rounding.
    if not isinstance(value, Fraction) or value < 0:
        raise ValueError("EXACT_NONNEGATIVE_FRACTION_SECONDS_REQUIRED")
    return value


@dataclass(frozen=True)
class FiniteWalkArc:
    arc_id: str
    from_state: str
    to_state: str
    edge_seconds: Fraction
    transition_seconds: Fraction

    def __post_init__(self) -> None:
        for name in ("arc_id", "from_state", "to_state"):
            identifier(getattr(self, name), name)
        _seconds(self.edge_seconds)
        _seconds(self.transition_seconds)

    @property
    def seconds(self) -> Fraction:
        return self.edge_seconds + self.transition_seconds


@dataclass(frozen=True)
class FiniteWalkConnector:
    connector_id: str
    physical_node_id: str
    state_id: str
    seconds: Fraction
    coordinate_precision_digits: int | None = None

    def __post_init__(self) -> None:
        for name in ("connector_id", "physical_node_id", "state_id"):
            identifier(getattr(self, name), name)
        _seconds(self.seconds)


@dataclass(frozen=True)
class FiniteWalkGraph:
    # Resolves to pinned graph/build/profile/state model and physical access scope.
    scope_id: str
    origin_physical_node_id: str
    destination_physical_node_id: str
    state_ids: tuple[str, ...]
    arcs: tuple[FiniteWalkArc, ...]
    initial_connectors: tuple[FiniteWalkConnector, ...]
    final_connectors: tuple[FiniteWalkConnector, ...]


@dataclass(frozen=True)
class FiniteWalkInventory:
    """Separately retained exhaustive inventory; never inferred from an export flag.

    A self-generated or shortened inventory can pass structural verification. Its
    authority and semantics are explicit external proof obligations, not checksums.
    Partial-cut certificates are intentionally unsupported by this reference oracle.
    """

    scope_id: str
    state_ids: frozenset[str]
    arc_ids: frozenset[str]
    initial_connector_ids: frozenset[str]
    final_connector_ids: frozenset[str]
    content_sha256: str
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class FiniteWalkMinimum:
    # None means unreachable only in the supplied inventory, never global no-route.
    total_seconds: Fraction | None
    initial_seconds: Fraction | None
    network_seconds: Fraction | None
    final_seconds: Fraction | None
    initial_connector_id: str | None
    arc_ids: tuple[str, ...]
    final_connector_id: str | None
    settled_state_ids: frozenset[str]
    inventory_sha256: str


def finite_walk_graph_digest(graph: FiniteWalkGraph) -> str:
    """Order-independent digest including exact rational weights and endpoint scope."""
    payload = [
        graph.scope_id,
        graph.origin_physical_node_id,
        graph.destination_physical_node_id,
        sorted(graph.state_ids),
        sorted(
            [a.arc_id, a.from_state, a.to_state, str(a.edge_seconds), str(a.transition_seconds)]
            for a in graph.arcs
        ),
        *[
            sorted(
                [
                    c.connector_id,
                    c.physical_node_id,
                    c.state_id,
                    str(c.seconds),
                    c.coordinate_precision_digits,
                ]
                for c in connectors
            )
            for connectors in (graph.initial_connectors, graph.final_connectors)
        ],
    ]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()


def _verify_inventory(graph: FiniteWalkGraph, inventory: FiniteWalkInventory) -> None:
    for value in (
        graph.scope_id,
        graph.origin_physical_node_id,
        graph.destination_physical_node_id,
    ):
        identifier(value, "finite_walk_scope")
    if graph.scope_id != inventory.scope_id:
        raise ValueError("FINITE_GRAPH_SCOPE_MISMATCH")
    references(inventory.evidence_refs, required=True)
    for actual, expected in (
        (graph.state_ids, inventory.state_ids),
        (tuple(a.arc_id for a in graph.arcs), inventory.arc_ids),
        (tuple(c.connector_id for c in graph.initial_connectors), inventory.initial_connector_ids),
        (tuple(c.connector_id for c in graph.final_connectors), inventory.final_connector_ids),
    ):
        for value in actual:
            identifier(value, "finite_graph_identifier")
        if len(set(actual)) != len(actual):
            raise ValueError("DUPLICATE_FINITE_GRAPH_IDENTIFIER")
        if frozenset(actual) != expected:
            raise ValueError("FINITE_GRAPH_INVENTORY_MISMATCH")
    states = set(graph.state_ids)
    if any(a.from_state not in states or a.to_state not in states for a in graph.arcs):
        raise ValueError("FINITE_GRAPH_STATE_MISSING")
    if not graph.initial_connectors or not graph.final_connectors:
        raise ValueError("FINITE_GRAPH_ENDPOINTS_INCOMPLETE")
    for connectors, physical_id in (
        (graph.initial_connectors, graph.origin_physical_node_id),
        (graph.final_connectors, graph.destination_physical_node_id),
    ):
        for connector in connectors:
            if connector.state_id not in states or connector.physical_node_id != physical_id:
                raise ValueError("FINITE_GRAPH_ENDPOINT_MISMATCH")
            if connector.coordinate_precision_digits is not None:
                raise ValueError("EXACT_FINITE_GRAPH_ENDPOINT_REQUIRED")
    if finite_walk_graph_digest(graph) != inventory.content_sha256:
        raise ValueError("FINITE_GRAPH_CONTENT_MISMATCH")


def solve_finite_walk_graph(
    graph: FiniteWalkGraph, inventory: FiniteWalkInventory
) -> FiniteWalkMinimum:
    """Exhaustive exact Dijkstra, with virtual physical source/sink connector arcs.

    State IDs must encode all history affecting allowed turns or elapsed costs.
    Exhausting a node graph that discarded that history does not establish this.
    All source-model elapsed weights must be static and nonnegative in seconds.
    """
    _verify_inventory(graph, inventory)
    zero = Fraction(0)
    if graph.origin_physical_node_id == graph.destination_physical_node_id:
        return FiniteWalkMinimum(
            zero, zero, zero, zero, None, (), None, frozenset(), inventory.content_sha256
        )
    outgoing: dict[str, list[FiniteWalkArc]] = {state: [] for state in graph.state_ids}
    for arc in sorted(graph.arcs, key=lambda a: a.arc_id):
        outgoing[arc.from_state].append(arc)
    distances: dict[str, Fraction] = {}
    initial_ids: dict[str, str] = {}
    previous: dict[str, tuple[str, str]] = {}
    queue: list[tuple[Fraction, str]] = []
    for connector in sorted(graph.initial_connectors, key=lambda c: c.connector_id):
        if connector.state_id not in distances or connector.seconds < distances[connector.state_id]:
            distances[connector.state_id] = connector.seconds
            initial_ids[connector.state_id] = connector.connector_id
            heappush(queue, (connector.seconds, connector.state_id))
    settled: set[str] = set()
    while queue:
        elapsed, state = heappop(queue)
        if state in settled or elapsed != distances[state]:
            continue
        settled.add(state)
        for arc in outgoing[state]:
            candidate = elapsed + arc.seconds
            if arc.to_state not in distances or candidate < distances[arc.to_state]:
                distances[arc.to_state] = candidate
                initial_ids[arc.to_state] = initial_ids[state]
                previous[arc.to_state] = (state, arc.arc_id)
                heappush(queue, (candidate, arc.to_state))
    endings = [
        (distances[c.state_id] + c.seconds, c.connector_id, c)
        for c in graph.final_connectors
        if c.state_id in distances
    ]
    if not endings:
        return FiniteWalkMinimum(
            None, None, None, None, None, (), None, frozenset(settled), inventory.content_sha256
        )
    total, _, final = min(endings)
    state = final.state_id
    initial_id = initial_ids[state]
    path = []
    while state in previous:
        state, arc_id = previous[state]
        path.append(arc_id)
    initial = next(c for c in graph.initial_connectors if c.connector_id == initial_id)
    return FiniteWalkMinimum(
        total,
        initial.seconds,
        total - initial.seconds - final.seconds,
        final.seconds,
        initial_id,
        tuple(reversed(path)),
        final.connector_id,
        frozenset(settled),
        inventory.content_sha256,
    )


def verify_finite_walk_candidate(
    graph: FiniteWalkGraph,
    inventory: FiniteWalkInventory,
    *,
    initial_connector_id: str,
    arc_ids: tuple[str, ...],
    final_connector_id: str,
    reported_total_seconds: Fraction,
) -> FiniteWalkMinimum:
    """Check a candidate's directed continuity, physical total, and elapsed optimum.

    A tied path is valid. Generalized cost, distance, connector omissions, and
    connector double counting cannot substitute for this exact elapsed total.
    The returned reference result is still conditional on the supplied inventory.
    """
    _seconds(reported_total_seconds)
    result = solve_finite_walk_graph(graph, inventory)
    initials = {c.connector_id: c for c in graph.initial_connectors}
    finals = {c.connector_id: c for c in graph.final_connectors}
    arcs = {a.arc_id: a for a in graph.arcs}
    if initial_connector_id not in initials or final_connector_id not in finals:
        raise ValueError("CANDIDATE_CONNECTOR_UNKNOWN")
    initial, final = initials[initial_connector_id], finals[final_connector_id]
    state, total = initial.state_id, initial.seconds
    for arc_id in arc_ids:
        if arc_id not in arcs or arcs[arc_id].from_state != state:
            raise ValueError("CANDIDATE_DIRECTED_PATH_MISMATCH")
        arc = arcs[arc_id]
        state, total = arc.to_state, total + arc.seconds
    if final.state_id != state:
        raise ValueError("CANDIDATE_FINAL_ENDPOINT_MISMATCH")
    total += final.seconds
    if total != reported_total_seconds:
        raise ValueError("CANDIDATE_ELAPSED_TOTAL_MISMATCH")
    if total != result.total_seconds:
        raise ValueError("CANDIDATE_IS_NOT_ELAPSED_MINIMUM")
    return result
