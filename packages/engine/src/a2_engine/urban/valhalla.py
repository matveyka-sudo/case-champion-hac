"""Pure Valhalla 3.5.1 route JSON boundary; execution and attestations are external."""

import hashlib
import json
from dataclasses import dataclass
from math import isclose
from typing import Any, Literal

from a2_engine.shared.results import finite_number
from a2_engine.urban.models import UrbanGeometry, identifier, references


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


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


def make_route_request(
    point_id: str,
    destination_id: str,
    build: ValhallaBuild,
    search_parameters_id: str,
    origin: ValhallaCorrelation,
    destination: ValhallaCorrelation,
) -> WalkRouteRequest:
    if origin.build != build or destination.build != build:
        raise ValueError("CORRELATION_BUILD_MISMATCH")
    locations = [
        dict(
            c.correlated_location.location(),
            type="break",
            radius=0,
            search_cutoff=1,
            node_snap_tolerance=0,
            search_filter={"exclude_ferry": True},
        )
        for c in (origin, destination)
    ]
    payload = {
        "locations": locations,
        "costing": "pedestrian",
        "costing_options": {"pedestrian": json.loads(build.pedestrian_options_json)},
        "units": "kilometers",
        "format": "json",
        "alternates": 0,
        "directions_type": "maneuvers",
    }
    request_id = digest(
        [
            point_id,
            destination_id,
            build.__dict__,
            search_parameters_id,
            origin.correlation_id,
            destination.correlation_id,
            payload,
        ]
    )
    payload["id"] = request_id
    return WalkRouteRequest(
        request_id,
        point_id,
        destination_id,
        build,
        search_parameters_id,
        origin,
        destination,
        canonical_json(payload),
        references(build.evidence_refs + origin.evidence_refs + destination.evidence_refs),
    )


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


def _polyline6(encoded: str, evidence: tuple[str, ...]) -> UrbanGeometry:
    coordinates: list[tuple[float, float]] = []
    latitude, longitude, position = 0, 0, 0
    while position < len(encoded):
        values = []
        for _ in range(2):
            value, shift = 0, 0
            while True:
                if position >= len(encoded) or shift > 35:
                    raise ValueError("INVALID_POLYLINE6")
                digit = ord(encoded[position]) - 63
                position += 1
                if not 0 <= digit <= 63:
                    raise ValueError("INVALID_POLYLINE6")
                value |= (digit & 31) << shift
                shift += 5
                if digit < 32:
                    break
            values.append(~(value >> 1) if value & 1 else value >> 1)
        latitude += values[0]
        longitude += values[1]
        coordinates.append((longitude / 1e6, latitude / 1e6))
    return UrbanGeometry(tuple(coordinates), evidence)


def normalize_walk_response(
    request: WalkRouteRequest, envelope: WalkResponseEnvelope
) -> WalkRouteResult:
    if (
        envelope.request_id != request.request_id
        or envelope.point_id != request.point_id
        or envelope.destination_id != request.destination_id
        or envelope.build != request.build
        or envelope.request_payload_hash != request.payload_hash
        or envelope.origin_correlation_id != request.origin.correlation_id
        or envelope.destination_correlation_id != request.destination.correlation_id
    ):
        raise ValueError("RESPONSE_REQUEST_IDENTITY_MISMATCH")
    evidence = references(
        request.evidence_refs
        + envelope.evidence_refs
        + envelope.minimum_time_evidence_refs
        + envelope.unreachable_evidence_refs
        + envelope.pure_walk_evidence_refs
        + envelope.endpoint_evidence_refs
    )
    response_hash = (
        hashlib.sha256(envelope.response_json.encode()).hexdigest()
        if envelope.response_json is not None
        else None
    )

    def unknown(reason: str) -> WalkRouteResult:
        return WalkRouteResult(
            request, "unknown", None, None, None, False, reason, evidence, response_hash
        )

    if envelope.outcome != "response":
        return unknown("PROVIDER_" + envelope.outcome.upper())
    if not envelope.response_json or not envelope.evidence_refs:
        return unknown("INCOMPLETE_RESPONSE_ENVELOPE")
    try:
        body = json.loads(envelope.response_json)
        if not isinstance(body, dict):
            return unknown("INVALID_RESPONSE")
        if body.get("id") is not None and body["id"] != request.request_id:
            raise ValueError("RESPONSE_PAIR_MISMATCH")
        if "error_code" in body:
            # 442 alone can mean algorithm limits, not proven global no-route.
            if (
                body["error_code"] == 442
                and envelope.search_complete
                and envelope.unreachable_evidence_refs
            ):
                return WalkRouteResult(
                    request,
                    "confirmed_unreachable",
                    None,
                    None,
                    None,
                    True,
                    None,
                    evidence,
                    response_hash,
                )
            return unknown("PROVIDER_ERROR_" + str(body["error_code"]))
        if body.get("id") != request.request_id:
            return unknown("RESPONSE_ID_MISSING")
        if (
            envelope.matched_origin is None
            or envelope.matched_destination is None
            or not envelope.endpoint_evidence_refs
        ):
            return unknown("ACTUAL_ENDPOINTS_UNCONFIRMED")
        if (
            envelope.matched_origin != request.origin.correlated_location
            or envelope.matched_destination != request.destination.correlated_location
        ):
            raise ValueError("ACTUAL_ENDPOINT_MISMATCH")
        trip = body["trip"]
        if trip.get("status") != 0 or trip.get("warnings") or body.get("warnings"):
            return unknown("PROVIDER_STATUS_OR_WARNINGS")
        locations = trip["locations"]
        if len(locations) != 2:
            return unknown("INVALID_RESPONSE_LOCATIONS")
        for row, correlation in zip(locations, (request.origin, request.destination), strict=True):
            if WalkCoordinate(row["lon"], row["lat"]) != correlation.correlated_location:
                raise ValueError("RESPONSE_ENDPOINT_MISMATCH")
        # Locations in /route echo inputs. Actual matching is separately attested by
        # correlation artifacts + executor IDs above; the echo is not that evidence.
        if not envelope.pure_walk_evidence_refs:
            return unknown("PATH_MODE_UNCONFIRMED")
        summary = trip["summary"]
        seconds = finite_number(summary["time"], "elapsed seconds")
        units = trip["units"]
        if units not in ("kilometers", "miles"):
            return unknown("UNSUPPORTED_DISTANCE_UNIT")
        multiplier = 1000 if units == "kilometers" else 1609.344
        distance = finite_number(summary["length"], "distance") * multiplier
        finite_number(distance, "distance_m")
        if summary.get("has_ferry") is not False:
            return unknown("FERRY_STATUS_UNCONFIRMED_OR_FORBIDDEN")
        legs = trip["legs"]
        if len(legs) != 1:
            return unknown("INVALID_NETWORK_LEG_COUNT")
        leg = legs[0]
        if not isclose(finite_number(leg["summary"]["time"], "leg time"), seconds, abs_tol=1e-6):
            return unknown("INCONSISTENT_ROUTE_SUMMARY")
        if not isclose(
            finite_number(leg["summary"]["length"], "leg length") * multiplier,
            distance,
            abs_tol=1e-3,
        ):
            return unknown("INCONSISTENT_ROUTE_SUMMARY")
        maneuvers = leg["maneuvers"]
        if not maneuvers:
            return unknown("MISSING_PATH_MODE")
        for maneuver in maneuvers:
            if (
                maneuver.get("travel_mode") != "pedestrian"
                or maneuver.get("travel_type") != "foot"
                or maneuver.get("ferry", False)
                or maneuver.get("type") in range(28, 37)
            ):
                return unknown("NON_WALK_PATH")
            finite_number(maneuver["time"], "maneuver time")
            finite_number(maneuver["length"], "maneuver distance")
        geometry = _polyline6(leg["shape"], evidence) if "shape" in leg else None
        if geometry is not None:
            for point, correlation in zip(
                (geometry.coordinates[0], geometry.coordinates[-1]),
                (request.origin, request.destination),
                strict=True,
            ):
                expected = correlation.correlated_location
                if abs(point[0] - expected.lon) > 1e-6 or abs(point[1] - expected.lat) > 1e-6:
                    raise ValueError("GEOMETRY_ENDPOINT_MISMATCH")
        minimum = envelope.search_complete and bool(envelope.minimum_time_evidence_refs)
        return WalkRouteResult(
            request,
            "reachable",
            seconds,
            distance,
            geometry,
            minimum,
            None if minimum else "MINIMUM_TIME_UNPROVEN",
            evidence,
            response_hash,
        )
    except (KeyError, TypeError, json.JSONDecodeError, IndexError):
        return unknown("INCOMPLETE_OR_INVALID_RESPONSE")
