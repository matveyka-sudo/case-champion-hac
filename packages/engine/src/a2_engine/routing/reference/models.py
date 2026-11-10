"""Accepted, directed reference network. Acceptance is an input, optimality is computed.

Arrival/departure states must distinguish *every* relevant platform, incoming
service/edge and possession restriction. Only explicit operations connect them;
sharing a physical node never supplies a transfer or carriage of a private car.
"""

from dataclasses import dataclass, field
from typing import Literal

from a2_engine.routing.road import RoadBuild
from a2_engine.shared.results import finite_number

ReferenceMode = Literal["road", "rail", "air", "water"]


def identifier(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("INVALID_REFERENCE_ID")


def identifiers(values: tuple[str, ...], *, nonempty: bool = True) -> tuple[str, ...]:
    if isinstance(values, str):
        raise ValueError("REFERENCE_ID_COLLECTION_REQUIRED")
    result = tuple(values)
    for value in result:
        identifier(value)
    if (nonempty and not result) or len(set(result)) != len(result):
        raise ValueError("EMPTY_OR_DUPLICATE_REFERENCE_IDS")
    return tuple(sorted(result))


def compatibility(value: bool | None) -> None:
    if value is not None and type(value) is not bool:
        raise ValueError("INVALID_REFERENCE_COMPATIBILITY")


@dataclass(frozen=True)
class ReferenceNetwork:
    graph_version: str
    snapshot_id: str
    acceptance_id: str
    applicability_id: str
    compatibility_version: str
    methodology_version: str
    profile_version: str
    evidence_refs: tuple[str, ...]
    road_build: RoadBuild | None = None

    def __post_init__(self) -> None:
        for value in (
            self.graph_version,
            self.snapshot_id,
            self.acceptance_id,
            self.applicability_id,
            self.compatibility_version,
        ):
            identifier(value)
        if (
            self.methodology_version != "a2-methodology-v3"
            or self.profile_version != "edge-calibrated-v1"
        ):
            raise ValueError("UNSUPPORTED_REFERENCE_VERSION")
        object.__setattr__(self, "evidence_refs", identifiers(self.evidence_refs))
        if self.road_build is not None and (
            not isinstance(self.road_build, RoadBuild) or self.road_build.costing != "auto"
        ):
            raise ValueError("REFERENCE_AUTO_FREEFLOW_BUILD_REQUIRED")


@dataclass(frozen=True)
class ReferenceScope:
    network: ReferenceNetwork
    origin_territory_id: str
    destination_territory_id: str
    origin_node_ids: tuple[str, ...]
    destination_node_ids: tuple[str, ...]
    allowed_modes: tuple[ReferenceMode, ...]
    comparison_kind: Literal["exact_node_pair", "city_pair_best_node"]

    def __post_init__(self) -> None:
        if not isinstance(self.network, ReferenceNetwork):
            raise ValueError("REFERENCE_NETWORK_REQUIRED")
        identifier(self.origin_territory_id)
        identifier(self.destination_territory_id)
        if self.origin_territory_id == self.destination_territory_id:
            raise ValueError("DISTINCT_INTERCITY_TERRITORIES_REQUIRED")
        for field_name in ("origin_node_ids", "destination_node_ids", "allowed_modes"):
            object.__setattr__(self, field_name, identifiers(getattr(self, field_name)))
        if set(self.origin_node_ids) & set(self.destination_node_ids):
            raise ValueError("OVERLAPPING_INTERCITY_BOUNDARIES")
        if any(mode not in ("road", "rail", "air", "water") for mode in self.allowed_modes):
            raise ValueError("INVALID_REFERENCE_MODE")
        if self.comparison_kind not in ("exact_node_pair", "city_pair_best_node"):
            raise ValueError("INVALID_REFERENCE_COMPARISON_KIND")
        if self.comparison_kind == "exact_node_pair" and (
            len(self.origin_node_ids) != 1 or len(self.destination_node_ids) != 1
        ):
            raise ValueError("EXACT_REFERENCE_REQUIRES_SINGLETON_BOUNDARIES")


@dataclass(frozen=True)
class ReferenceState:
    state_id: str
    node_id: str
    phase: Literal["departure", "arrival", "transfer"]
    mode: ReferenceMode
    compatibility_context_id: str

    def __post_init__(self) -> None:
        for value in (self.state_id, self.node_id, self.compatibility_context_id):
            identifier(value)
        if self.phase not in ("departure", "arrival", "transfer") or self.mode not in (
            "road",
            "rail",
            "air",
            "water",
        ):
            raise ValueError("INVALID_REFERENCE_STATE")


@dataclass(frozen=True)
class ReferenceCalibration:
    calibration_id: str
    edge_id: str
    from_state_id: str
    to_state_id: str
    network: ReferenceNetwork
    seconds: float
    semantics: Literal["running_time", "static_freeflow_elapsed"]
    evidence_refs: tuple[str, ...]
    confirmed: bool = True
    units: Literal["seconds"] = "seconds"
    road_build: RoadBuild | None = None

    def __post_init__(self) -> None:
        for value in (self.calibration_id, self.edge_id, self.from_state_id, self.to_state_id):
            identifier(value)
        if not isinstance(self.network, ReferenceNetwork):
            raise ValueError("REFERENCE_NETWORK_REQUIRED")
        if type(self.confirmed) is not bool or self.units != "seconds":
            raise ValueError("INVALID_REFERENCE_CALIBRATION_UNITS_OR_CONFIRMATION")
        if self.semantics not in ("running_time", "static_freeflow_elapsed"):
            raise ValueError("PHYSICAL_REFERENCE_SECONDS_REQUIRED")
        object.__setattr__(self, "seconds", finite_number(self.seconds, "reference seconds"))
        object.__setattr__(self, "evidence_refs", identifiers(self.evidence_refs))


@dataclass(frozen=True)
class ReferenceEdge:
    edge_id: str
    from_state_id: str
    to_state_id: str
    calibrations: tuple[ReferenceCalibration, ...]
    evidence_refs: tuple[str, ...]
    compatible: bool | None = field(kw_only=True)
    coverage: Literal["full", "partial"] = "full"
    boundary_semantics: Literal["physical_edge", "logical_connection"] = "physical_edge"
    length_m: float | None = None
    length_basis: Literal["network", "geodesic"] | None = None
    geometry_ref: str | None = None

    def __post_init__(self) -> None:
        for value in (self.edge_id, self.from_state_id, self.to_state_id):
            identifier(value)
        compatibility(self.compatible)
        if self.coverage not in ("full", "partial") or self.boundary_semantics not in (
            "physical_edge",
            "logical_connection",
        ):
            raise ValueError("INVALID_REFERENCE_EDGE_SEMANTICS")
        object.__setattr__(self, "evidence_refs", identifiers(self.evidence_refs))
        calibrations = tuple(self.calibrations)
        if any(not isinstance(c, ReferenceCalibration) for c in calibrations):
            raise ValueError("REFERENCE_CALIBRATION_REQUIRED")
        object.__setattr__(
            self, "calibrations", tuple(sorted(calibrations, key=lambda c: c.calibration_id))
        )
        if self.length_m is not None:
            object.__setattr__(self, "length_m", finite_number(self.length_m, "reference length"))
            if self.length_basis not in ("network", "geodesic"):
                raise ValueError("REFERENCE_LENGTH_BASIS_REQUIRED")
        elif self.length_basis is not None:
            raise ValueError("REFERENCE_LENGTH_REQUIRED")
        if self.geometry_ref is not None:
            identifier(self.geometry_ref)


@dataclass(frozen=True)
class ReferenceOperation:
    operation_id: str
    from_state_id: str
    to_state_id: str
    network: ReferenceNetwork
    seconds: float | None
    operation_kind: str
    evidence_refs: tuple[str, ...]
    compatible: bool | None = field(kw_only=True)

    def __post_init__(self) -> None:
        for value in (self.operation_id, self.from_state_id, self.to_state_id, self.operation_kind):
            identifier(value)
        if not isinstance(self.network, ReferenceNetwork):
            raise ValueError("REFERENCE_NETWORK_REQUIRED")
        compatibility(self.compatible)
        if self.seconds is not None:
            object.__setattr__(self, "seconds", finite_number(self.seconds, "operation seconds"))
        object.__setattr__(self, "evidence_refs", identifiers(self.evidence_refs))


@dataclass(frozen=True)
class ReferenceGraph:
    network: ReferenceNetwork
    states: tuple[ReferenceState, ...]
    edges: tuple[ReferenceEdge, ...]
    operations: tuple[ReferenceOperation, ...]
    coverage: Literal["full", "partial"]
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.network, ReferenceNetwork) or self.coverage not in (
            "full",
            "partial",
        ):
            raise ValueError("INVALID_REFERENCE_GRAPH")
        for field_name, cls, key in (
            ("states", ReferenceState, "state_id"),
            ("edges", ReferenceEdge, "edge_id"),
            ("operations", ReferenceOperation, "operation_id"),
        ):
            values = tuple(getattr(self, field_name))
            if any(not isinstance(value, cls) for value in values):
                raise ValueError("INVALID_REFERENCE_GRAPH_MEMBER")
            identifiers(tuple(getattr(value, key) for value in values), nonempty=False)
            object.__setattr__(
                self, field_name, tuple(sorted(values, key=lambda v: getattr(v, key)))
            )
        identifiers(
            tuple(e.edge_id for e in self.edges) + tuple(o.operation_id for o in self.operations),
            nonempty=False,
        )
        identifiers(
            tuple(c.calibration_id for e in self.edges for c in e.calibrations), nonempty=False
        )
        object.__setattr__(self, "evidence_refs", identifiers(self.evidence_refs))
