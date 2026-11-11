"""Pure, pairwise Valhalla 3.5.1 boundary. No IO or urban imports.

Version 3.5.1 parses date_time with minute precision. A subminute network
entry is rejected rather than rounded into a different access restriction.
"""

import hashlib
import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, date, datetime, timedelta
from math import isclose
from typing import Any, Literal
from zoneinfo import ZoneInfo

from a2_engine.shared.results import finite_number


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    raise TypeError("Unsupported canonical road JSON value")


def road_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, default=_json_default, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _refs(values: tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(values, str) or any(not isinstance(v, str) or not v.strip() for v in values):
        raise ValueError("INVALID_ROAD_EVIDENCE")
    return tuple(sorted(set(values)))


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("AWARE_DATETIME_REQUIRED")
    return value.astimezone(UTC)


def road_arrival(start: datetime, seconds: float) -> datetime:
    seconds = finite_number(seconds, "duration")
    try:
        end = _utc(start) + timedelta(seconds=seconds)
    except OverflowError as exc:
        raise ValueError("ROAD_TIME_RANGE_EXCEEDED") from exc
    if seconds > 0 and end == start:
        raise ValueError("DURATION_BELOW_TIMESTAMP_RESOLUTION")
    return end


@dataclass(frozen=True)
class RoadCoordinate:
    lon: float
    lat: float

    def __post_init__(self) -> None:
        for value, limit in ((self.lon, 180), (self.lat, 90)):
            finite_number(abs(value), "coordinate")
            if isinstance(value, bool) or not -limit <= value <= limit:
                raise ValueError("INVALID_ROAD_COORDINATE")


@dataclass(frozen=True)
class RoadBuild:
    snapshot_id: str
    source_graph_version: str
    build_id: str
    graph_version: str
    profile_version: str
    costing: Literal["auto", "pedestrian"]
    options_json: str
    evidence_refs: tuple[str, ...]
    engine_version: str = "3.5.1"
    traffic_mode: Literal["static"] = "static"

    def __post_init__(self) -> None:
        if self.engine_version != "3.5.1" or self.traffic_mode != "static":
            raise ValueError("UNSUPPORTED_ROAD_VERSION_OR_TRAFFIC")
        for value in (
            self.snapshot_id,
            self.source_graph_version,
            self.build_id,
            self.graph_version,
            self.profile_version,
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("INVALID_ROAD_ID")
        options = json.loads(self.options_json)
        if not isinstance(options, dict) or self.costing not in ("auto", "pedestrian"):
            raise ValueError("INVALID_ROAD_PROFILE")
        if self.costing == "auto":
            if options.get("speed_types") != ["freeflow"]:
                raise ValueError("EXPLICIT_FREEFLOW_REQUIRED")
            for flag in (
                "ignore_restrictions",
                "ignore_turn_restrictions",
                "ignore_access",
                "ignore_closures",
            ):
                if options.get(flag, False) is not False:
                    raise ValueError("ROAD_RESTRICTIONS_MUST_BE_RESPECTED")
                options[flag] = False
        elif options.get("type", "foot") != "foot":
            raise ValueError("FOOT_PROFILE_REQUIRED")
        else:
            finite_number(options.get("walking_speed"), "walking_speed", positive=True)
        object.__setattr__(
            self,
            "options_json",
            json.dumps(options, sort_keys=True, separators=(",", ":"), allow_nan=False),
        )
        object.__setattr__(self, "evidence_refs", _refs(self.evidence_refs))
        if not self.evidence_refs:
            raise ValueError("ROAD_BUILD_EVIDENCE_REQUIRED")

    @property
    def profile_hash(self) -> str:
        return road_hash(asdict(self))


@dataclass(frozen=True)
class RoadEndpoint:
    """Directed external walk connector; direction is never inferred."""

    build: RoadBuild
    physical_node_id: str
    access_node_id: str
    coordinate: RoadCoordinate
    correlation_id: str
    direction: Literal["entry", "exit"]
    seconds: float
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.direction not in ("entry", "exit"):
            raise ValueError("INVALID_CONNECTOR_DIRECTION")
        for value in (self.physical_node_id, self.access_node_id, self.correlation_id):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("INVALID_ENDPOINT_ID")
        object.__setattr__(self, "seconds", finite_number(self.seconds, "connector seconds"))
        object.__setattr__(self, "evidence_refs", _refs(self.evidence_refs))
        if not self.evidence_refs:
            raise ValueError("DIRECTED_CONNECTOR_AND_CORRELATION_EVIDENCE_REQUIRED")


@dataclass(frozen=True)
class RoadRequest:
    origin_id: str
    destination_id: str
    origin: RoadEndpoint
    destination: RoadEndpoint
    departure_at: datetime
    search_parameters_id: str
    timezone: str

    def __post_init__(self) -> None:
        if self.origin.direction != "entry" or self.destination.direction != "exit":
            raise ValueError("CONNECTOR_DIRECTION_MISMATCH")
        if self.origin.build != self.destination.build:
            raise ValueError("ROAD_BUILD_MISMATCH")
        if not all(
            isinstance(v, str) and v.strip()
            for v in (self.origin_id, self.destination_id, self.search_parameters_id)
        ):
            raise ValueError("INVALID_ROAD_REQUEST_ID")
        object.__setattr__(self, "departure_at", _utc(self.departure_at))
        ZoneInfo(self.timezone)
        network = self.network_departure_at.astimezone(ZoneInfo(self.timezone))
        if network.second or network.microsecond:
            raise ValueError("VALHALLA_SUBMINUTE_ENTRY_UNSUPPORTED")
        # Do not guess which fold the upstream local timestamp would mean.
        if network.replace(fold=0).utcoffset() != network.replace(fold=1).utcoffset():
            raise ValueError("AMBIGUOUS_NETWORK_ENTRY")

    @property
    def network_departure_at(self) -> datetime:
        return road_arrival(self.departure_at, self.origin.seconds)

    @property
    def request_id(self) -> str:
        value = asdict(self)
        value["departure_at"] = self.departure_at.isoformat()
        return road_hash(value)

    def payload(self) -> dict[str, Any]:
        build = self.origin.build
        return {
            "id": self.request_id,
            "locations": [
                dict(
                    asdict(e.coordinate),
                    type="break",
                    radius=0,
                    search_cutoff=1,
                    node_snap_tolerance=0,
                    search_filter={"exclude_ferry": True},
                )
                for e in (self.origin, self.destination)
            ],
            "costing": build.costing,
            "costing_options": {build.costing: json.loads(build.options_json)},
            "date_time": {
                "type": 1,
                "value": self.network_departure_at.astimezone(ZoneInfo(self.timezone)).strftime(
                    "%Y-%m-%dT%H:%M"
                ),
            },
            "units": "kilometers",
            "format": "json",
            "alternates": 0,
            "prioritize_bidirectional": False,
            "directions_type": "maneuvers",
        }

    @property
    def payload_hash(self) -> str:
        return road_hash(self.payload())


@dataclass(frozen=True)
class RoadResponse:
    request_id: str
    payload_hash: str
    profile_hash: str
    outcome: Literal["response", "timeout", "cancelled", "limit", "http_error"]
    response_json: str | None
    evidence_refs: tuple[str, ...]
    matched_origin: RoadCoordinate | None = None
    matched_destination: RoadCoordinate | None = None
    endpoint_evidence_refs: tuple[str, ...] = ()
    minimum_time_evidence_refs: tuple[str, ...] = ()
    unreachable_evidence_refs: tuple[str, ...] = ()
    search_complete: bool = False

    def __post_init__(self) -> None:
        if (
            self.outcome not in ("response", "timeout", "cancelled", "limit", "http_error")
            or type(self.search_complete) is not bool
        ):
            raise ValueError("INVALID_ROAD_EXECUTION_STATE")
        for name in (
            "evidence_refs",
            "endpoint_evidence_refs",
            "minimum_time_evidence_refs",
            "unreachable_evidence_refs",
        ):
            object.__setattr__(self, name, _refs(getattr(self, name)))


@dataclass(frozen=True)
class RoadResult:
    request: RoadRequest
    state: Literal["reachable", "confirmed_unreachable", "unknown"]
    network_seconds: float | None
    network_distance_m: float | None
    minimum_proven: bool
    evidence_refs: tuple[str, ...]
    reason_code: str | None
    response_hash: str | None

    @property
    def total_seconds(self) -> float | None:
        if self.network_seconds is None:
            return None
        start = self.request.departure_at
        end = road_arrival(
            road_arrival(self.request.network_departure_at, self.network_seconds),
            self.request.destination.seconds,
        )
        return (end - start).total_seconds()


def normalize_road_response(request: RoadRequest, response: RoadResponse) -> RoadResult:
    if (response.request_id, response.payload_hash, response.profile_hash) != (
        request.request_id,
        request.payload_hash,
        request.origin.build.profile_hash,
    ):
        raise ValueError("ROAD_RESPONSE_IDENTITY_MISMATCH")
    evidence = _refs(
        request.origin.build.evidence_refs
        + request.origin.evidence_refs
        + request.destination.evidence_refs
        + response.evidence_refs
        + response.endpoint_evidence_refs
        + response.minimum_time_evidence_refs
        + response.unreachable_evidence_refs
    )
    response_hash = (
        road_hash(response.response_json) if response.response_json is not None else None
    )

    def unknown(reason: str) -> RoadResult:
        return RoadResult(request, "unknown", None, None, False, evidence, reason, response_hash)

    if response.outcome != "response":
        return unknown("PROVIDER_" + response.outcome.upper())
    if not response.response_json or not response.evidence_refs:
        return unknown("INCOMPLETE_ROAD_ENVELOPE")
    try:
        body = json.loads(response.response_json)
        if not isinstance(body, dict) or body.get("id") != request.request_id:
            return unknown("ROAD_RESPONSE_ID_MISSING_OR_WRONG")
        if "error_code" in body:
            if (
                body["error_code"] == 442
                and response.search_complete
                and response.unreachable_evidence_refs
            ):
                return RoadResult(
                    request,
                    "confirmed_unreachable",
                    None,
                    None,
                    True,
                    evidence,
                    None,
                    response_hash,
                )
            return unknown("ROAD_PROVIDER_ERROR")
        if (
            not response.endpoint_evidence_refs
            or response.matched_origin is None
            or response.matched_destination is None
        ):
            return unknown("ACTUAL_ENDPOINTS_UNCONFIRMED")
        if (response.matched_origin, response.matched_destination) != (
            request.origin.coordinate,
            request.destination.coordinate,
        ):
            raise ValueError("ACTUAL_ENDPOINT_MISMATCH")
        trip = body["trip"]
        if trip["status"] != 0 or trip.get("warnings") or body.get("warnings"):
            return unknown("ROAD_STATUS_OR_WARNINGS")
        if trip["units"] != "kilometers" or len(trip["locations"]) != 2 or len(trip["legs"]) != 1:
            return unknown("INVALID_ROAD_RESPONSE_SHAPE")
        for row, endpoint in zip(
            trip["locations"], (request.origin, request.destination), strict=True
        ):
            if RoadCoordinate(row["lon"], row["lat"]) != endpoint.coordinate:
                raise ValueError("ROAD_ECHO_ENDPOINT_MISMATCH")
        if trip["summary"].get("has_ferry") is not False:
            return unknown("ROAD_FERRY_UNCONFIRMED_OR_UNSUPPORTED")
        seconds = finite_number(trip["summary"]["time"], "network time")
        distance = finite_number(trip["summary"]["length"], "network distance") * 1000
        finite_number(distance, "network distance metres")
        leg = trip["legs"][0]
        if not isclose(
            finite_number(leg["summary"]["time"], "leg time"), seconds, abs_tol=1e-6
        ) or not isclose(
            finite_number(leg["summary"]["length"], "leg length") * 1000, distance, abs_tol=1e-3
        ):
            return unknown("ROAD_SUMMARY_MISMATCH")
        expected = "drive" if request.origin.build.costing == "auto" else "pedestrian"
        if not leg["maneuvers"] or any(m.get("travel_mode") != expected for m in leg["maneuvers"]):
            return unknown("ROAD_MODE_UNCONFIRMED")
        road_arrival(request.network_departure_at, seconds)
        proven = response.search_complete and bool(response.minimum_time_evidence_refs)
        return RoadResult(
            request,
            "reachable",
            seconds,
            distance,
            proven,
            evidence,
            None if proven else "MINIMUM_TIME_UNPROVEN",
            response_hash,
        )
    except (KeyError, TypeError, IndexError, json.JSONDecodeError):
        return unknown("INVALID_ROAD_RESPONSE")
    except ValueError as exc:
        if "MISMATCH" in str(exc) or "RESOLUTION" in str(exc) or "RANGE_EXCEEDED" in str(exc):
            raise
        return unknown("INVALID_ROAD_RESPONSE")
