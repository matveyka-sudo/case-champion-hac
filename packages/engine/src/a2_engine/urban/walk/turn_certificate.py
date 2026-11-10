"""Source-bound closed-ball relaxation retaining exact immediate turn times.

Every route improving a replayed incumbent has all full-edge endpoints inside
the zero-turn closed ball. Terminal fragments are absorbing alternatives even
when their full edge ends outside that ball. Long history/resources remain
relaxed; equality requires another native feasibility replay of the optimum.
"""

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction
from heapq import heappop, heappush

from a2_engine.urban.walk.source_bound import (
    NativeEdge,
    NativeWalkBounds,
    NativeWalkCandidate,
    NativeWalkExport,
    _float_bits,
    binary_fraction,
    check_native_walk_bounds,
)


@dataclass(frozen=True)
class NativeTurnIncumbent:
    source_scope_id: str
    export_sha256: str
    candidate: NativeWalkCandidate
    replay: str
    replay_sha256: str
    upper_seconds: Fraction


@dataclass(frozen=True)
class NativeTurnPlan:
    incumbent: NativeTurnIncumbent
    ball_nodes: frozenset[int]
    state_edges: frozenset[int]
    pairs: tuple[tuple[int, int], ...]
    cutoff_seconds: Fraction | None = None

    @property
    def ceiling_seconds(self) -> Fraction:
        return self.incumbent.upper_seconds if self.cutoff_seconds is None else self.cutoff_seconds

    @property
    def fingerprint(self) -> str:
        content = [
            self.incumbent.source_scope_id,
            self.incumbent.export_sha256,
            self.incumbent.replay_sha256,
            str(self.incumbent.upper_seconds),
            str(self.ceiling_seconds),
            sorted(self.ball_nodes),
            sorted(self.state_edges),
            self.pairs,
        ]
        return hashlib.sha256(json.dumps(content, separators=(",", ":")).encode()).hexdigest()

    def request_json(self) -> str:
        return json.dumps(
            {
                "scope_id": self.incumbent.source_scope_id,
                "plan_sha256": self.fingerprint,
                "turn_pairs": [{"from": a, "to": b} for a, b in self.pairs],
            },
            separators=(",", ":"),
        )


@dataclass(frozen=True)
class NativeTurnWeights:
    source_scope_id: str
    plan_sha256: str
    content_sha256: str
    values: tuple[tuple[int, int, Fraction], ...]


def _validate_candidate(graph: NativeWalkExport, candidate: NativeWalkCandidate) -> None:
    edges = {edge.edge_id: edge for edge in graph.edges}
    origins = {e.index: e for e in graph.endpoints if e.endpoint == 0}
    finals = {e.index: e for e in graph.endpoints if e.endpoint == 1}
    origin, final = origins[candidate.origin_index], finals[candidate.destination_index]
    rows = candidate.traversals
    if not rows or rows[0][0] != origin.edge_id or rows[-1][0] != final.edge_id:
        raise ValueError("INCUMBENT_ENDPOINT_BINDING_MISMATCH")
    expected: tuple[int, ...]
    if len(rows) == 1:
        if origin.edge_id != final.edge_id or final.percent < origin.percent:
            raise ValueError("INCUMBENT_DIRECT_EDGE_MISMATCH")
        expected = (_float_bits(final.percent - origin.percent),)
    else:
        expected = (
            _float_bits(1 - origin.percent),
            *(_float_bits(Fraction(1)) for _ in rows[1:-1]),
            _float_bits(final.percent),
        )
    if tuple(bits for _, bits in rows) != expected:
        raise ValueError("INCUMBENT_FRAGMENT_BINDING_MISMATCH")
    for (edge_id, fraction_bits), seconds in zip(rows, candidate.edge_seconds, strict=True):
        edge = edges[edge_id]
        if not edge.retained or seconds != binary_fraction(
            _float_bits(edge.seconds * binary_fraction(fraction_bits))
        ):
            raise ValueError("INCUMBENT_SOURCE_WEIGHT_MISMATCH")
    transitions = set(graph.transitions)
    for (previous, _), (current, _) in zip(rows, rows[1:]):
        a, b = edges[previous].end, edges[current].start
        if a != b and (a, b) not in transitions:
            raise ValueError("INCUMBENT_SOURCE_CONTINUITY_MISMATCH")


def make_native_turn_incumbent(
    graph: NativeWalkExport, source_scope_id: str, candidate: NativeWalkCandidate, replay: str
) -> NativeTurnIncumbent:
    """Executor scope binds export/request/config/profile/library/binary hashes."""
    if not re.fullmatch(r"[0-9a-f]{64}", source_scope_id):
        raise ValueError("SHA256_EXECUTION_SCOPE_REQUIRED")
    _validate_candidate(graph, candidate)
    bound = check_native_walk_bounds(
        candidate,
        replay,
        origin_connector_seconds=Fraction(),
        destination_connector_seconds=Fraction(),
    )
    if bound.network_upper_seconds is None:
        raise ValueError("REPLAYED_FEASIBLE_INCUMBENT_REQUIRED")
    return NativeTurnIncumbent(
        source_scope_id,
        graph.content_sha256,
        candidate,
        replay,
        hashlib.sha256(replay.encode()).hexdigest(),
        bound.network_upper_seconds,
    )


def plan_native_turn_pairs(
    graph: NativeWalkExport,
    source_scope_id: str,
    incumbent: NativeTurnIncumbent,
    *,
    ceiling_seconds: Fraction | None = None,
) -> NativeTurnPlan:
    if (
        incumbent.source_scope_id != source_scope_id
        or incumbent.export_sha256 != graph.content_sha256
    ):
        raise ValueError("INCUMBENT_SOURCE_PROFILE_SCOPE_MISMATCH")
    current = make_native_turn_incumbent(
        graph, source_scope_id, incumbent.candidate, incumbent.replay
    )
    if current != incumbent:
        raise ValueError("INCUMBENT_REPLAY_CHANGED")
    ceiling = incumbent.upper_seconds if ceiling_seconds is None else ceiling_seconds
    if not isinstance(ceiling, Fraction) or ceiling < incumbent.upper_seconds:
        raise ValueError("CLOSED_BALL_CEILING_BELOW_REPLAYED_INCUMBENT")
    edges = {e.edge_id: e for e in graph.edges if e.retained}
    outgoing: dict[int, list[tuple[int, Fraction]]] = {}
    aliases: dict[int, list[int]] = {}
    for edge in edges.values():
        outgoing.setdefault(edge.start, []).append((edge.end, edge.seconds))
    for start, end in graph.transitions:
        outgoing.setdefault(start, []).append((end, Fraction()))
        aliases.setdefault(start, []).append(end)
    distance: dict[int, Fraction] = {}
    queue: list[tuple[Fraction, int]] = []
    for origin in graph.endpoints:
        if origin.endpoint == 0 and origin.edge_id in edges and origin.partial_seconds <= ceiling:
            node = edges[origin.edge_id].end
            if origin.partial_seconds < distance.get(node, origin.partial_seconds + 1):
                distance[node] = origin.partial_seconds
                heappush(queue, (origin.partial_seconds, node))
    while queue:
        seconds, node = heappop(queue)
        if seconds != distance[node]:
            continue
        for target, weight in outgoing.get(node, []):
            total = seconds + weight
            if total <= ceiling and total < distance.get(target, total + 1):
                distance[target] = total
                heappush(queue, (total, target))
    ball = frozenset(distance)
    states = frozenset(e.edge_id for e in edges.values() if e.end in ball)
    final_ids = {e.edge_id for e in graph.endpoints if e.endpoint == 1}
    out_edges: dict[int, list[NativeEdge]] = {}
    for edge in edges.values():
        if edge.end in ball or edge.edge_id in final_ids:
            out_edges.setdefault(edge.start, []).append(edge)
    pairs: set[tuple[int, int]] = set()
    for previous in states:
        end = edges[previous].end
        for node in (end, *aliases.get(end, [])):
            pairs.update((previous, edge.edge_id) for edge in out_edges.get(node, []))
    return NativeTurnPlan(incumbent, ball, states, tuple(sorted(pairs)), ceiling_seconds)


def load_native_turn_weights(
    content: str, plan: NativeTurnPlan, *, expected_sha256: str
) -> NativeTurnWeights:
    digest = hashlib.sha256(content.encode()).hexdigest()
    if digest != expected_sha256:
        raise ValueError("NATIVE_TURN_CONTENT_CHANGED")
    rows = [json.loads(line) for line in content.splitlines()]
    if (
        not rows
        or rows[0]
        != {
            "kind": "turn_pairs_header",
            "scope_id": plan.incumbent.source_scope_id,
            "plan_sha256": plan.fingerprint,
        }
        or rows[-1] != {"kind": "turn_pairs_footer", "pairs": len(plan.pairs)}
    ):
        raise ValueError("NATIVE_TURN_SCOPE_OR_INVENTORY_MISMATCH")
    weights: dict[tuple[int, int], Fraction] = {}
    for row in rows[1:-1]:
        key = (row["from"], row["to"])
        if row["kind"] != "turn_pair" or key in weights:
            raise ValueError("DUPLICATE_OR_INVALID_NATIVE_TURN")
        weights[key] = binary_fraction(row["seconds_bits"])
    if weights.keys() != set(plan.pairs):
        raise ValueError("NATIVE_TURN_PAIR_ROSTER_INCOMPLETE")
    return NativeTurnWeights(
        plan.incumbent.source_scope_id,
        plan.fingerprint,
        digest,
        tuple((a, b, w) for (a, b), w in sorted(weights.items())),
    )


def _solve_native_turn_problem(
    graph: NativeWalkExport,
    plan: NativeTurnPlan,
    weights: NativeTurnWeights,
    origin_costs: Mapping[int, Fraction],
    destination_costs: Mapping[int, Fraction],
) -> tuple[NativeWalkCandidate, Fraction]:
    if (
        plan_native_turn_pairs(
            graph,
            plan.incumbent.source_scope_id,
            plan.incumbent,
            ceiling_seconds=plan.cutoff_seconds,
        )
        != plan
    ):
        raise ValueError("CLOSED_BALL_PLAN_CHANGED")
    if any(
        not isinstance(value, Fraction) or value < 0
        for value in (*origin_costs.values(), *destination_costs.values())
    ):
        raise ValueError("NONNEGATIVE_EXACT_ENDPOINT_WEIGHTS_REQUIRED")
    if (
        weights.source_scope_id != plan.incumbent.source_scope_id
        or weights.plan_sha256 != plan.fingerprint
    ):
        raise ValueError("TURN_WEIGHTS_SOURCE_PROFILE_MISMATCH")
    turns = {(a, b): w for a, b, w in weights.values}
    if (
        len(turns) != len(weights.values)
        or turns.keys() != set(plan.pairs)
        or any(not isinstance(weight, Fraction) or weight < 0 for weight in turns.values())
    ):
        raise ValueError("NATIVE_TURN_PAIR_ROSTER_INCOMPLETE")
    edges = {e.edge_id: e for e in graph.edges if e.retained}
    aliases: dict[int, list[int]] = {}
    outgoing: dict[int, list[int]] = {}
    for start, end in graph.transitions:
        aliases.setdefault(start, []).append(end)
    for edge in edges.values():
        outgoing.setdefault(edge.start, []).append(edge.edge_id)
    origins = [e for e in graph.endpoints if e.endpoint == 0 and e.edge_id in edges]
    finals = [e for e in graph.endpoints if e.endpoint == 1 and e.edge_id in edges]
    distance: dict[int, Fraction] = {}
    parents: dict[int, int | None] = {}
    seeds: dict[int, tuple[int, tuple[tuple[int, int], ...], tuple[Fraction, ...]]] = {}
    queue: list[tuple[Fraction, int]] = []
    best: NativeWalkCandidate | None = None
    best_objective: Fraction | None = None

    def accept(candidate: NativeWalkCandidate) -> None:
        nonlocal best, best_objective
        objective = (
            candidate.lower_seconds
            + origin_costs.get(candidate.origin_index, Fraction())
            + destination_costs.get(candidate.destination_index, Fraction())
        )
        if objective <= plan.ceiling_seconds and (
            best_objective is None or objective < best_objective
        ):
            best = candidate
            best_objective = objective

    def seed(
        state: int,
        origin: int,
        traversals: tuple[tuple[int, int], ...],
        seconds: tuple[Fraction, ...],
    ) -> None:
        total = sum(seconds, Fraction()) + origin_costs.get(origin, Fraction())
        if (
            state in plan.state_edges
            and total <= plan.ceiling_seconds
            and total < distance.get(state, total + 1)
        ):
            distance[state] = total
            parents[state] = None
            seeds[state] = (origin, traversals, seconds)
            heappush(queue, (total, state))

    for origin in origins:
        fraction_bits = _float_bits(1 - origin.percent)
        if binary_fraction(fraction_bits) > 0:
            seed(
                origin.edge_id,
                origin.index,
                ((origin.edge_id, fraction_bits),),
                (origin.partial_seconds,),
            )
        else:
            end = edges[origin.edge_id].end
            for node in (end, *aliases.get(end, [])):
                for edge_id in outgoing.get(node, []):
                    seed(
                        edge_id,
                        origin.index,
                        ((origin.edge_id, fraction_bits), (edge_id, _float_bits(Fraction(1)))),
                        (Fraction(), edges[edge_id].seconds),
                    )
                for final in finals:
                    if edges[final.edge_id].start == node:
                        accept(
                            NativeWalkCandidate(
                                origin.index,
                                final.index,
                                (
                                    (origin.edge_id, fraction_bits),
                                    (final.edge_id, _float_bits(final.percent)),
                                ),
                                final.partial_seconds,
                                (Fraction(), final.partial_seconds),
                                (Fraction(), Fraction()),
                            )
                        )
        for final in finals:
            if origin.edge_id == final.edge_id and final.percent >= origin.percent:
                bits = _float_bits(final.percent - origin.percent)
                seconds = binary_fraction(
                    _float_bits(edges[origin.edge_id].seconds * binary_fraction(bits))
                )
                accept(
                    NativeWalkCandidate(
                        origin.index,
                        final.index,
                        ((origin.edge_id, bits),),
                        seconds,
                        (seconds,),
                        (Fraction(),),
                    )
                )
    next_edges: dict[int, list[int]] = {}
    for previous, current in plan.pairs:
        if current in plan.state_edges:
            next_edges.setdefault(previous, []).append(current)
    while queue:
        elapsed, previous = heappop(queue)
        if elapsed != distance[previous]:
            continue
        for current in next_edges.get(previous, []):
            total = elapsed + turns[previous, current] + edges[current].seconds
            if total <= plan.ceiling_seconds and total < distance.get(current, total + 1):
                distance[current] = total
                parents[current] = previous
                seeds.pop(current, None)
                heappush(queue, (total, current))
    for previous in sorted(distance):
        end = edges[previous].end
        for final in finals:
            if edges[final.edge_id].start not in (end, *aliases.get(end, [])):
                continue
            final_fraction = _float_bits(final.percent)
            terminal_turn = (
                turns[previous, final.edge_id]
                if binary_fraction(final_fraction) > 0
                else Fraction()
            )
            total = (
                distance[previous]
                + terminal_turn
                + final.partial_seconds
                + destination_costs.get(final.index, Fraction())
            )
            if total > plan.ceiling_seconds or (
                best_objective is not None and total >= best_objective
            ):
                continue
            trail = []
            state = previous
            while parents[state] is not None:
                parent = parents[state]
                assert parent is not None
                trail.append((state, turns[parent, state]))
                state = parent
            origin_index, prefix, prefix_seconds = seeds[state]
            trail.reverse()
            traversals = (
                *prefix,
                *((edge, _float_bits(Fraction(1))) for edge, _ in trail),
                (final.edge_id, final_fraction),
            )
            running = (
                *prefix_seconds,
                *(edges[edge].seconds for edge, _ in trail),
                final.partial_seconds,
            )
            turn_values = (
                *(Fraction() for _ in prefix),
                *(turn for _, turn in trail),
                terminal_turn,
            )
            accept(
                NativeWalkCandidate(
                    origin_index,
                    final.index,
                    tuple(traversals),
                    total
                    - origin_costs.get(origin_index, Fraction())
                    - destination_costs.get(final.index, Fraction()),
                    tuple(running),
                    tuple(turn_values),
                )
            )
    if best is None:
        raise ValueError("REPLAYED_INCUMBENT_NOT_REPRESENTED_BY_CLOSED_BALL")
    _validate_candidate(graph, best)
    assert best_objective is not None
    return best, best_objective


def solve_native_turn_plan(
    graph: NativeWalkExport, plan: NativeTurnPlan, weights: NativeTurnWeights
) -> NativeWalkCandidate:
    return _solve_native_turn_problem(graph, plan, weights, {}, {})[0]


def check_native_turn_minimum(candidate: NativeWalkCandidate, replay: str) -> NativeWalkBounds:
    return check_native_walk_bounds(
        candidate,
        replay,
        origin_connector_seconds=Fraction(),
        destination_connector_seconds=Fraction(),
    )
