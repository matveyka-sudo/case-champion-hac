"""valhalla / models — canonical urban implementation."""

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from a2_engine.shared.results import finite_number
from a2_engine.urban.models import UrbanGeometry, identifier, references
from a2_engine.urban.valhalla.codec import canonical_json, digest


@dataclass(frozen=True)
class WalkCoordinate:
    lon: float
    lat: float

    def __post_init__(self) -> None:
        for value, limit in ((self.lon, 180), (self.lat, 90)):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("INVALID_COORDINATE")
            finite_number(abs(value) if not isinstance(value, bool) else value, "coordinate")
            if isinstance(value, bool) or not -limit <= value <= limit:
                raise ValueError("INVALID_COORDINATE")

    def location(self) -> dict[str, float]:
        return {"lon": self.lon, "lat": self.lat}


@dataclass(frozen=True)
class ValhallaBuild:
    snapshot_id: str
    source_graph_version: str
    build_id: str
    graph_version: str
    engine_version: str
    profile_version: str
    pedestrian_options_json: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "snapshot_id",
            "source_graph_version",
            "build_id",
            "graph_version",
            "profile_version",
        ):
            identifier(getattr(self, name), name)
        if self.engine_version != "3.5.1":
            raise ValueError("UNSUPPORTED_VALHALLA_VERSION")
        options = json.loads(self.pedestrian_options_json)
        if not isinstance(options, dict) or options.get("type", "foot") != "foot":
            raise ValueError("PEDESTRIAN_FOOT_PROFILE_REQUIRED")
        speed = finite_number(options.get("walking_speed"), "walking_speed")
        if not 0.5 <= speed <= 25:
            raise ValueError("INVALID_PROFILE_WALKING_SPEED")
        # Retain the entire accepted option set, including shortest/penalties/limits.
        object.__setattr__(self, "pedestrian_options_json", canonical_json(options))
        object.__setattr__(self, "evidence_refs", references(self.evidence_refs, required=True))

    @property
    def profile_hash(self) -> str:
        return digest([self.engine_version, self.profile_version, self.pedestrian_options_json])


@dataclass(frozen=True)
class WalkConnector:
    snapshot_id: str
    source_graph_version: str
    physical_node_id: str
    access_node_id: str
    physical_location: WalkCoordinate
    access_location: WalkCoordinate
    seconds: float | None
    traversable: bool | None
    time_basis: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "snapshot_id",
            "source_graph_version",
            "physical_node_id",
            "access_node_id",
            "time_basis",
        ):
            identifier(getattr(self, name), name)
        if self.traversable is not None and not isinstance(self.traversable, bool):
            raise ValueError("EXPLICIT_CONNECTOR_STATE_REQUIRED")
        if self.seconds is not None:
            object.__setattr__(self, "seconds", finite_number(self.seconds, "connector_seconds"))
        object.__setattr__(self, "evidence_refs", references(self.evidence_refs))

    @property
    def confirmed(self) -> bool:
        return self.traversable is True and self.seconds is not None and bool(self.evidence_refs)


@dataclass(frozen=True)
class ValhallaCorrelation:
    build: ValhallaBuild
    access_node_id: str  # ML2 identifier, never a Valhalla internal node ID.
    source_location: WalkCoordinate
    correlated_location: WalkCoordinate
    correlation_id: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        identifier(self.access_node_id, "access_node_id")
        identifier(self.correlation_id, "correlation_id")
        object.__setattr__(self, "evidence_refs", references(self.evidence_refs, required=True))
        # Pilot requires a separately accepted connector if Valhalla re-snaps elsewhere.
        if self.source_location != self.correlated_location:
            raise ValueError("REVISED_ACCESS_CONNECTOR_REQUIRED")


@dataclass(frozen=True)
class WalkResponseEnvelope:
    request_id: str
    point_id: str
    destination_id: str
    build: ValhallaBuild
    request_payload_hash: str
    origin_correlation_id: str
    destination_correlation_id: str
    outcome: Literal["response", "timeout", "http_error", "limit", "no_correlation"]
    response_json: str | None
    evidence_refs: tuple[str, ...]
    # Trusted executor attestations, NOT inferred from HTTP success or shortest=true.
    search_complete: bool = False
    minimum_time_evidence_refs: tuple[str, ...] = ()
    unreachable_evidence_refs: tuple[str, ...] = ()
    pure_walk_evidence_refs: tuple[str, ...] = ()
    matched_origin: WalkCoordinate | None = None
    matched_destination: WalkCoordinate | None = None
    endpoint_evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.outcome not in ("response", "timeout", "http_error", "limit", "no_correlation"):
            raise ValueError("INVALID_EXECUTION_OUTCOME")
        if not isinstance(self.search_complete, bool):
            raise ValueError("EXPLICIT_SEARCH_COMPLETENESS_REQUIRED")
        for name in (
            "evidence_refs",
            "minimum_time_evidence_refs",
            "unreachable_evidence_refs",
            "pure_walk_evidence_refs",
            "endpoint_evidence_refs",
        ):
            object.__setattr__(self, name, references(getattr(self, name)))


@dataclass(frozen=True)
class WalkRouteRequest:
    request_id: str
    point_id: str
    destination_id: str
    build: ValhallaBuild
    search_parameters_id: str
    origin: ValhallaCorrelation
    destination: ValhallaCorrelation
    payload_json: str
    evidence_refs: tuple[str, ...]
    # Exactly network-access -> network-access; both physical connectors are external.
    path_scope: Literal["network_only"] = "network_only"

    def payload(self) -> dict[str, Any]:
        value: dict[str, Any] = json.loads(self.payload_json)
        return value

    @property
    def payload_hash(self) -> str:
        return hashlib.sha256(self.payload_json.encode()).hexdigest()


@dataclass(frozen=True)
class WalkRouteResult:
    request: WalkRouteRequest
    state: Literal["reachable", "confirmed_unreachable", "unknown"]
    network_seconds: float | None
    network_distance_m: float | None
    network_geometry: UrbanGeometry | None
    minimum_proven: bool
    reason_code: str | None
    evidence_refs: tuple[str, ...]
    response_hash: str | None = None
