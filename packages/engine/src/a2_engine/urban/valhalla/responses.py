"""valhalla / responses — canonical urban implementation."""

import hashlib
import json
from math import isclose

from a2_engine.shared.results import finite_number
from a2_engine.urban.models import references
from a2_engine.urban.valhalla.codec import _polyline6, polyline6_coordinate_matches
from a2_engine.urban.valhalla.models import (
    WalkCoordinate,
    WalkResponseEnvelope,
    WalkRouteRequest,
    WalkRouteResult,
)


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
                and request.origin.coordinate_precision_digits is None
                and request.destination.coordinate_precision_digits is None
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
            if not correlation.matches_response_location(WalkCoordinate(row["lon"], row["lat"])):
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
                if not (
                    polyline6_coordinate_matches(point[0], expected.lon)
                    and polyline6_coordinate_matches(point[1], expected.lat)
                ):
                    raise ValueError("GEOMETRY_ENDPOINT_MISMATCH")
        minimum = (
            envelope.search_complete
            and bool(envelope.minimum_time_evidence_refs)
            and request.origin.coordinate_precision_digits is None
            and request.destination.coordinate_precision_digits is None
        )
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
