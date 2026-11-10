"""Sequential supplied-path evaluation, not a road search algorithm."""

from dataclasses import dataclass, replace
from datetime import datetime
from functools import cached_property
from typing import Literal

from a2_engine.routing.road import road_arrival, road_hash
from a2_engine.routing.traffic.models import (
    TrafficEdge,
    TrafficScope,
    TrafficSnapshot,
    traffic_id,
    traffic_refs,
    traffic_utc,
)
from a2_engine.routing.traffic.profiles import select_traffic_profile
from a2_engine.shared.results import finite_number


@dataclass(frozen=True)
class TrafficEndpoint:
    scope: TrafficScope
    physical_node_id: str
    access_node_id: str
    correlation_id: str
    direction: Literal["entry", "exit"]
    seconds: float
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        for value in (self.physical_node_id, self.access_node_id, self.correlation_id):
            traffic_id(value)
        if self.direction not in ("entry", "exit"):
            raise ValueError("INVALID_TRAFFIC_CONNECTOR_DIRECTION")
        object.__setattr__(
            self, "seconds", finite_number(self.seconds, "traffic connector seconds")
        )
        object.__setattr__(self, "evidence_refs", traffic_refs(self.evidence_refs))
        if not self.evidence_refs:
            raise ValueError("TRAFFIC_CONNECTOR_PROOF_REQUIRED")


@dataclass(frozen=True)
class TrafficRequest:
    snapshot: TrafficSnapshot
    origin_id: str
    destination_id: str
    origin: TrafficEndpoint
    destination: TrafficEndpoint
    departure_at: datetime
    checked_at: datetime
    search_parameters_id: str
    search_scope_id: str

    def __post_init__(self) -> None:
        for value in (
            self.origin_id,
            self.destination_id,
            self.search_parameters_id,
            self.search_scope_id,
        ):
            traffic_id(value)
        if (
            self.origin.scope != self.snapshot.scope
            or self.destination.scope != self.snapshot.scope
        ):
            raise ValueError("TRAFFIC_ENDPOINT_SCOPE_MISMATCH")
        if self.origin.direction != "entry" or self.destination.direction != "exit":
            raise ValueError("TRAFFIC_ENDPOINT_DIRECTION_MISMATCH")
        for name in ("departure_at", "checked_at"):
            object.__setattr__(self, name, traffic_utc(getattr(self, name)))

    @property
    def network_departure_at(self) -> datetime:
        return road_arrival(self.departure_at, self.origin.seconds)

    @cached_property
    def request_id(self) -> str:
        return road_hash(
            [
                self.snapshot.content_hash,
                self.origin_id,
                self.destination_id,
                self.origin,
                self.destination,
                self.departure_at,
                self.checked_at,
                self.search_parameters_id,
                self.search_scope_id,
            ]
        )


@dataclass(frozen=True)
class TrafficTransition:
    from_edge_id: str
    to_edge_id: str
    seconds: float
    included_in_previous_edge: bool
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        traffic_id(self.from_edge_id)
        traffic_id(self.to_edge_id)
        if type(self.included_in_previous_edge) is not bool:
            raise ValueError("EXPLICIT_TURN_ACCOUNTING_REQUIRED")
        object.__setattr__(
            self, "seconds", finite_number(self.seconds, "physical transition seconds")
        )
        object.__setattr__(self, "evidence_refs", traffic_refs(self.evidence_refs))
        if not self.evidence_refs:
            raise ValueError("TURN_TIME_EVIDENCE_REQUIRED")


@dataclass(frozen=True)
class TrafficPath:
    edges: tuple[TrafficEdge, ...]  # full directed edges only in entry_speed_v1
    transitions: tuple[TrafficTransition, ...]
    evidence_refs: tuple[str, ...]
    scope: TrafficScope

    def __post_init__(self) -> None:
        if not isinstance(self.scope, TrafficScope):
            raise ValueError("TRAFFIC_PATH_SCOPE_REQUIRED")
        object.__setattr__(self, "edges", tuple(self.edges))
        object.__setattr__(self, "transitions", tuple(self.transitions))
        object.__setattr__(self, "evidence_refs", traffic_refs(self.evidence_refs))
        if not self.edges or not self.evidence_refs:
            raise ValueError("CONFIRMED_NONEMPTY_TRAFFIC_PATH_REQUIRED")
        if len(self.transitions) != len(self.edges) - 1:
            raise ValueError("EXPLICIT_TRANSITION_ROSTER_REQUIRED")
        for old, new, turn in zip(self.edges, self.edges[1:], self.transitions):
            if old.to_node_id != new.from_node_id or (turn.from_edge_id, turn.to_edge_id) != (
                old.edge_id,
                new.edge_id,
            ):
                raise ValueError("DISCONNECTED_DIRECTED_TRAFFIC_PATH")

    @cached_property
    def path_hash(self) -> str:
        return road_hash([self.scope, self.edges, self.transitions, self.evidence_refs])


@dataclass(frozen=True)
class TrafficStep:
    kind: Literal["entry", "edge", "transition", "exit"]
    from_node_id: str
    to_node_id: str
    start_at: datetime
    end_at: datetime
    raw_seconds: float
    duration_seconds: float
    profile_id: str | None
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class TrafficPathResult:
    request: TrafficRequest
    path: TrafficPath
    state: Literal["available", "unknown", "blocked"]
    steps: tuple[TrafficStep, ...]
    known_seconds: float | None
    profile_complete: bool
    search_complete: bool
    minimum_proven: bool
    reasons: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    minimum_evidence_refs: tuple[str, ...] = ()
    minimum_receipt: "TrafficMinimumReceipt | None" = None

    @property
    def evaluation_hash(self) -> str:
        # Receipt-independent fingerprint; binding a proof cannot change the computation.
        return road_hash(
            [
                self.request.request_id,
                self.request.snapshot.content_hash,
                self.path.path_hash,
                self.state,
                self.steps,
                self.known_seconds,
                self.profile_complete,
            ]
        )


@dataclass(frozen=True)
class TrafficMinimumReceipt:
    request_id: str
    traffic_content_hash: str
    path_hash: str
    evaluation_hash: str
    search_complete: bool
    minimum_time_confirmed: bool
    algorithm: Literal["exhaustive", "verified_temporal_algorithm"]
    assumptions_evidence_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        for value in (
            self.request_id,
            self.traffic_content_hash,
            self.path_hash,
            self.evaluation_hash,
        ):
            traffic_id(value)
        if (
            type(self.search_complete) is not bool
            or type(self.minimum_time_confirmed) is not bool
            or self.algorithm not in ("exhaustive", "verified_temporal_algorithm")
        ):
            raise ValueError("INVALID_TRAFFIC_MINIMUM_RECEIPT")
        object.__setattr__(self, "evidence_refs", traffic_refs(self.evidence_refs))
        object.__setattr__(
            self, "assumptions_evidence_refs", traffic_refs(self.assumptions_evidence_refs)
        )
        if not self.evidence_refs or not self.assumptions_evidence_refs:
            raise ValueError("EXECUTOR_AND_ALGORITHM_ASSUMPTIONS_REQUIRED")


def evaluate_traffic_path(request: TrafficRequest, path: TrafficPath) -> TrafficPathResult:
    if path.scope != request.snapshot.scope:
        raise ValueError("TRAFFIC_PATH_GRAPH_OR_POLICY_MISMATCH")
    if (
        path.edges[0].from_node_id != request.origin.access_node_id
        or path.edges[-1].to_node_id != request.destination.access_node_id
    ):
        raise ValueError("TRAFFIC_PATH_BOUNDARY_MISMATCH")
    steps: list[TrafficStep] = []
    evidence = list(
        request.snapshot.scope.evidence_refs
        + request.snapshot.acceptance_evidence_refs
        + request.origin.evidence_refs
        + request.destination.evidence_refs
        + path.evidence_refs
    )
    instant = request.departure_at

    def step(
        kind: Literal["entry", "edge", "transition", "exit"],
        origin: str,
        destination: str,
        raw_seconds: float,
        profile_id: str | None,
        refs: tuple[str, ...],
        *,
        included: bool = False,
    ) -> None:
        nonlocal instant
        end = road_arrival(instant, 0 if included else raw_seconds)
        steps.append(
            TrafficStep(
                kind,
                origin,
                destination,
                instant,
                end,
                raw_seconds,
                (end - instant).total_seconds(),
                profile_id,
                refs,
            )
        )
        instant = end
        evidence.extend(refs)

    def incomplete(reason: str, *, blocked: bool = False) -> TrafficPathResult:
        return TrafficPathResult(
            request,
            path,
            "blocked" if blocked else "unknown",
            tuple(steps),
            None,
            False,
            False,
            False,
            (reason,),
            traffic_refs(tuple(evidence)),
        )

    step(
        "entry",
        request.origin.physical_node_id,
        request.origin.access_node_id,
        request.origin.seconds,
        None,
        request.origin.evidence_refs,
    )
    for index, edge in enumerate(path.edges):
        if index:
            turn = path.transitions[index - 1]
            step(
                "transition",
                edge.from_node_id,
                edge.from_node_id,
                turn.seconds,
                None,
                turn.evidence_refs,
                included=turn.included_in_previous_edge,
            )
        profile, reason = select_traffic_profile(
            request.snapshot, edge.edge_id, instant, checked_at=request.checked_at
        )
        if profile is not None:
            evidence.extend(profile.evidence_refs + profile.provenance.evidence_refs)
            if profile.group.edge != edge:
                raise ValueError("TRAFFIC_PATH_EDGE_CONTENT_MISMATCH")
        if reason or profile is None:
            return incomplete(reason or "TRAFFIC_PROFILE_MISSING")
        if profile.closed:
            return incomplete("DIRECTED_EDGE_CLOSED_NOT_PAIR_UNREACHABLE", blocked=True)
        assert profile.speed_kph is not None
        seconds = edge.length_m / (profile.speed_kph / 3.6)
        finite_number(seconds, "traffic edge time", positive=True)
        end = road_arrival(instant, seconds)
        # Bin crossing is explicitly entry-speed, but global applicability/TTL
        # must cover the whole traversal, including for live and historical.
        if end > profile.valid_until:
            return incomplete("TRAFFIC_EXPIRES_DURING_EDGE")
        if request.snapshot.scope.mode == "historical" and (
            profile.observation_end_at is None or end > profile.observation_end_at
        ):
            return incomplete("HISTORICAL_SOURCE_COVERAGE_ENDS_DURING_EDGE")
        step(
            "edge",
            edge.from_node_id,
            edge.to_node_id,
            seconds,
            profile.profile_id,
            profile.evidence_refs,
        )
    step(
        "exit",
        request.destination.access_node_id,
        request.destination.physical_node_id,
        request.destination.seconds,
        None,
        request.destination.evidence_refs,
    )
    return TrafficPathResult(
        request,
        path,
        "available",
        tuple(steps),
        (instant - request.departure_at).total_seconds(),
        True,
        False,
        False,
        ("MINIMUM_TIME_UNPROVEN",),
        traffic_refs(tuple(evidence)),
    )


def verify_traffic_minimum(
    result: TrafficPathResult, receipt: TrafficMinimumReceipt
) -> TrafficPathResult:
    if not isinstance(receipt, TrafficMinimumReceipt):
        raise ValueError("DYNAMIC_MINIMUM_RECEIPT_REQUIRED")
    expected = (
        result.request.request_id,
        result.request.snapshot.content_hash,
        result.path.path_hash,
        result.evaluation_hash,
    )
    if (
        receipt.request_id,
        receipt.traffic_content_hash,
        receipt.path_hash,
        receipt.evaluation_hash,
    ) != expected:
        raise ValueError("TRAFFIC_MINIMUM_BINDING_MISMATCH")
    proven = (
        result.profile_complete
        and result.state == "available"
        and receipt.search_complete
        and receipt.minimum_time_confirmed
    )
    return replace(
        result,
        search_complete=receipt.search_complete,
        minimum_proven=proven,
        reasons=() if proven else result.reasons,
        minimum_evidence_refs=receipt.evidence_refs if proven else (),
        minimum_receipt=receipt,
    )


def validate_traffic_result(result: TrafficPathResult) -> None:
    """Publication adapters re-evaluate immutable inputs and validate receipt binding."""
    computed = evaluate_traffic_path(result.request, result.path)
    if result.minimum_receipt is not None:
        computed = verify_traffic_minimum(computed, result.minimum_receipt)
    if computed != result:
        raise ValueError("TRAFFIC_RESULT_DIFFERS_FROM_INPUTS_OR_RECEIPT")
