"""Validate the whole accepted input before exposing any searchable arcs."""

from dataclasses import dataclass
from math import isfinite
from typing import Literal

from a2_engine.routing.reference.models import ReferenceGraph, ReferenceScope
from a2_engine.routing.road import road_hash


@dataclass(frozen=True)
class CalibratedReferenceArc:
    arc_id: str
    kind: Literal["movement", "operation"]
    from_state_id: str
    to_state_id: str
    seconds: float
    calibration_id: str | None
    evidence_refs: tuple[str, ...]
    effective_speed_mps: float | None


@dataclass(frozen=True)
class CalibratedReferenceGraph:
    graph: ReferenceGraph
    scope: ReferenceScope
    arcs: tuple[CalibratedReferenceArc, ...]
    coverage: Literal["full", "partial"]
    reasons: tuple[str, ...]
    excluded_arc_ids: tuple[str, ...]
    graph_fingerprint: str


def calibrate_reference_graph(
    graph: ReferenceGraph, scope: ReferenceScope
) -> CalibratedReferenceGraph:
    """Minimize *each edge's* confirmed samples in one fixed acceptance scope.

    Road samples are fixed elapsed seconds of one physical edge or explicitly
    bounded logical connection. Conflicting road costs under the same fixed build
    are rejected: this is not a minimum of alternative pair-route preferences.
    """
    if graph.network != scope.network:
        raise ValueError("REFERENCE_NETWORK_SCOPE_MISMATCH")
    states = {state.state_id: state for state in graph.states}
    if not set(scope.origin_node_ids + scope.destination_node_ids) <= {
        state.node_id for state in graph.states
    }:
        raise ValueError("UNKNOWN_REFERENCE_BOUNDARY_NODE")
    arcs: list[CalibratedReferenceArc] = []
    reasons: set[str] = set()
    excluded: set[str] = set()
    if graph.coverage == "partial":
        reasons.add("PARTIAL_ACCEPTED_NETWORK")
    for edge in graph.edges:
        if edge.from_state_id not in states or edge.to_state_id not in states:
            raise ValueError("DANGLING_REFERENCE_EDGE")
        start, end = states[edge.from_state_id], states[edge.to_state_id]
        if start.phase != "departure" or end.phase != "arrival" or start.mode != end.mode:
            raise ValueError("INVALID_REFERENCE_MOVEMENT_STATES")
        if edge.length_basis == "geodesic" and start.mode != "air":
            raise ValueError("GEODESIC_REFERENCE_LENGTH_ONLY_FOR_AIR")
        for sample in edge.calibrations:
            if (
                sample.network != graph.network
                or sample.edge_id != edge.edge_id
                or sample.from_state_id != start.state_id
                or sample.to_state_id != end.state_id
            ):
                raise ValueError("REFERENCE_CALIBRATION_CONTEXT_OR_DIRECTION_MISMATCH")
            if start.mode == "road":
                if (
                    sample.semantics != "static_freeflow_elapsed"
                    or sample.road_build is None
                    or sample.road_build != graph.network.road_build
                ):
                    raise ValueError("FIXED_STATIC_PHYSICAL_ROAD_COST_REQUIRED")
            elif sample.semantics != "running_time" or sample.road_build is not None:
                raise ValueError("CONFIRMED_EDGE_RUNNING_TIME_REQUIRED")
        if start.mode not in scope.allowed_modes or edge.compatible is False:
            excluded.add(edge.edge_id)
            continue
        confirmed = [sample for sample in edge.calibrations if sample.confirmed]
        if start.mode == "road" and len({s.seconds for s in confirmed}) > 1:
            raise ValueError("CONFLICTING_FIXED_ROAD_COSTS")
        if edge.compatible is None:
            reasons.add(f"UNKNOWN_COMPATIBILITY:{edge.edge_id}")
        if not confirmed:
            reasons.add(f"UNKNOWN_EDGE_COST:{edge.edge_id}")
        if edge.coverage == "partial" or any(not s.confirmed for s in edge.calibrations):
            reasons.add(f"PARTIAL_EDGE_CALIBRATION:{edge.edge_id}")
        if edge.compatible is None or not confirmed:
            excluded.add(edge.edge_id)
            continue
        sample = min(confirmed, key=lambda s: (s.seconds, s.calibration_id))
        speed = (
            edge.length_m / sample.seconds if edge.length_m is not None and sample.seconds else None
        )
        if speed is not None and not isfinite(speed):
            speed = None  # Informational only; no inferred speed enters the cost.
        evidence = set(edge.evidence_refs + sample.evidence_refs)
        if sample.road_build is not None:
            evidence.update(sample.road_build.evidence_refs)
        arcs.append(
            CalibratedReferenceArc(
                edge.edge_id,
                "movement",
                start.state_id,
                end.state_id,
                sample.seconds,
                sample.calibration_id,
                tuple(sorted(evidence)),
                speed,
            )
        )
    for operation in graph.operations:
        if operation.from_state_id not in states or operation.to_state_id not in states:
            raise ValueError("DANGLING_REFERENCE_OPERATION")
        start, end = states[operation.from_state_id], states[operation.to_state_id]
        if operation.network != graph.network:
            raise ValueError("REFERENCE_OPERATION_SCOPE_MISMATCH")
        if start.phase not in ("arrival", "transfer") or end.phase not in ("departure", "transfer"):
            raise ValueError("INVALID_REFERENCE_OPERATION_STATES")
        if (
            start.mode not in scope.allowed_modes
            or end.mode not in scope.allowed_modes
            or operation.compatible is False
        ):
            excluded.add(operation.operation_id)
            continue
        if operation.compatible is None:
            reasons.add(f"UNKNOWN_COMPATIBILITY:{operation.operation_id}")
        if operation.seconds is None:
            reasons.add(f"UNKNOWN_OPERATION_COST:{operation.operation_id}")
        if operation.compatible is None or operation.seconds is None:
            excluded.add(operation.operation_id)
            continue
        arcs.append(
            CalibratedReferenceArc(
                operation.operation_id,
                "operation",
                start.state_id,
                end.state_id,
                operation.seconds,
                None,
                operation.evidence_refs,
                None,
            )
        )
    return CalibratedReferenceGraph(
        graph,
        scope,
        tuple(sorted(arcs, key=lambda a: a.arc_id)),
        "partial" if reasons else "full",
        tuple(sorted(reasons)),
        tuple(sorted(excluded)),
        road_hash(graph),
    )
