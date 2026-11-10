"""Exact shortest path on explicit arrival/departure states, without schedules."""

from dataclasses import dataclass
from fractions import Fraction
from heapq import heappop, heappush
from sys import float_info
from typing import Literal

from a2_engine.routing.reference.calibration import (
    CalibratedReferenceArc,
    CalibratedReferenceGraph,
    calibrate_reference_graph,
)
from a2_engine.routing.reference.models import ReferenceGraph, ReferenceScope
from a2_engine.routing.road import road_hash


@dataclass(frozen=True)
class ReferencePath:
    origin_node_id: str
    destination_node_id: str
    state_ids: tuple[str, ...]
    arcs: tuple[CalibratedReferenceArc, ...]
    movement_seconds: float
    operation_seconds: float
    seconds: float
    evidence_refs: tuple[str, ...]

    @property
    def edge_ids(self) -> tuple[str, ...]:
        return tuple(arc.arc_id for arc in self.arcs if arc.kind == "movement")

    @property
    def operation_ids(self) -> tuple[str, ...]:
        return tuple(arc.arc_id for arc in self.arcs if arc.kind == "operation")


@dataclass(frozen=True)
class ReferenceSearchResult:
    reference_id: str
    calibrated_graph: CalibratedReferenceGraph
    path: ReferencePath | None
    search_status: Literal["minimum_proven", "no_path_proven", "known_subnetwork_no_path"]
    reasons: tuple[str, ...]
    settled_states: int

    @property
    def scope(self) -> ReferenceScope:
        return self.calibrated_graph.scope

    @property
    def seconds(self) -> float | None:
        return self.path.seconds if self.path is not None else None

    @property
    def completeness(self) -> Literal["full", "partial"]:
        return "full" if self.calibrated_graph.coverage == "full" else "partial"

    @property
    def evidence_refs(self) -> tuple[str, ...]:
        """Include calibration/coverage evidence even for unchosen or unknown arcs."""
        graph = self.calibrated_graph.graph
        refs = set(graph.evidence_refs + graph.network.evidence_refs)
        if graph.network.road_build is not None:
            refs.update(graph.network.road_build.evidence_refs)
        for edge in graph.edges:
            refs.update(edge.evidence_refs)
            for sample in edge.calibrations:
                refs.update(sample.evidence_refs)
        for operation in graph.operations:
            refs.update(operation.evidence_refs)
        return tuple(sorted(refs))

    @property
    def full_ranking_eligible(self) -> bool:
        return self.search_status == "minimum_proven" and self.completeness == "full"


def build_reference(graph: ReferenceGraph, scope: ReferenceScope) -> ReferenceSearchResult:
    """Calibrate then run multi-source Dijkstra to the first destination arrival.

    Costs are exact rationals of the supplied finite IEEE values, with no
    intermediate rounding. Labels order by (cost, hops, arc IDs, start state).
    The hop criterion strictly penalizes zero cycles, making tie-breaking well
    founded. One label per *explicit state* is sufficient: all continuation
    restrictions are encoded in that state and its accepted outgoing operations.
    No duration/transfer cutoffs or resource interruption are implemented.
    """
    calibrated = calibrate_reference_graph(graph, scope)
    states = {state.state_id: state for state in graph.states}
    outgoing: dict[str, list[CalibratedReferenceArc]] = {}
    arc_by_id = {arc.arc_id: arc for arc in calibrated.arcs}
    for arc in calibrated.arcs:
        outgoing.setdefault(arc.from_state_id, []).append(arc)
    type Label = tuple[Fraction, int, tuple[str, ...], str]
    best: dict[str, Label] = {}
    queue: list[tuple[Label, str]] = []
    for state in graph.states:
        if (
            state.phase == "departure"
            and state.node_id in scope.origin_node_ids
            and state.mode in scope.allowed_modes
        ):
            label: Label = (Fraction(0), 0, (), state.state_id)
            best[state.state_id] = label
            heappush(queue, (label, state.state_id))
    settled = 0
    path = None
    while queue:
        current, state_id = heappop(queue)
        if best[state_id] != current:
            continue
        settled += 1
        state = states[state_id]
        if state.phase == "arrival" and state.node_id in scope.destination_node_ids:
            arcs = tuple(arc_by_id[arc_id] for arc_id in current[2])
            movement = sum((Fraction(a.seconds) for a in arcs if a.kind == "movement"), Fraction(0))
            operations = current[0] - movement
            path = ReferencePath(
                states[current[3]].node_id,
                state.node_id,
                (current[3],) + tuple(a.to_state_id for a in arcs),
                arcs,
                float(movement),
                float(operations),
                float(current[0]),
                tuple(sorted({ref for a in arcs for ref in a.evidence_refs})),
            )
            break
        for arc in outgoing.get(state_id, ()):
            cost = current[0] + Fraction(arc.seconds)
            if cost > Fraction(float_info.max):
                raise ValueError("REFERENCE_PATH_SUM_OVERFLOW")
            candidate: Label = (cost, current[1] + 1, current[2] + (arc.arc_id,), current[3])
            previous = best.get(arc.to_state_id)
            if previous is None or candidate < previous:
                best[arc.to_state_id] = candidate
                heappush(queue, (candidate, arc.to_state_id))
    status: Literal["minimum_proven", "no_path_proven", "known_subnetwork_no_path"]
    status = (
        "minimum_proven"
        if path is not None
        else ("no_path_proven" if calibrated.coverage == "full" else "known_subnetwork_no_path")
    )
    reasons = set(calibrated.reasons)
    if path is None:
        reasons.add(
            "REFERENCE_NO_FEASIBLE_PATH"
            if status == "no_path_proven"
            else "REFERENCE_COVERAGE_INCOMPLETE"
        )
    if calibrated.coverage == "partial":
        reasons.add("PARTIAL_REFERENCE")
    reference_id = "reference:" + road_hash(("state-dijkstra-v1", calibrated, status, path))
    return ReferenceSearchResult(
        reference_id, calibrated, path, status, tuple(sorted(reasons)), settled
    )
