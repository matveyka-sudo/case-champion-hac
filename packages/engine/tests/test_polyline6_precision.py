"""One-microdegree endpoint comparisons preserve the existing decimal limit."""

import json
import math
from dataclasses import replace
from decimal import localcontext
from pathlib import Path

import pytest
from a2_engine import normalize_walk_response
from a2_engine.urban.valhalla.codec import polyline6_coordinate_matches
from a2_engine.urban.valhalla.models import WalkResponseEnvelope, WalkRouteRequest
from pydantic import TypeAdapter
from test_walk_integration import encode_shape


@pytest.mark.parametrize(
    ("actual", "expected", "matches"),
    [
        (55.817587, 55.817586, True),
        (55.817586, 55.817587, True),
        (-55.817587, -55.817586, True),
        (-55.817586, -55.817587, True),
        (37.731331, 37.73133, True),
        (37.73133, 37.731331, True),
        (-0.0, 0.0, True),
        (0.000001, 0.0, True),
        (-0.000001, 0.0, True),
        (55.817588, 55.817586, False),
        (math.nextafter(55.817587, math.inf), 55.817586, False),
        (math.nextafter(55.817586, -math.inf), 55.817587, False),
        (-5e-324, 0.000001, False),
        (5e-324, 0.000001, True),
    ],
)
def test_raw_decimal_boundary_never_quantizes_away_an_excess(actual, expected, matches):
    assert polyline6_coordinate_matches(actual, expected) is matches
    # Correctness is independent of unrelated users of the Decimal context.
    with localcontext() as context:
        context.prec = 2
        assert polyline6_coordinate_matches(actual, expected) is matches


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, True, "55.817587"])
def test_nonfinite_or_nonnumeric_endpoint_is_rejected(bad):
    for actual, expected in ((bad, 55.817586), (55.817586, bad)):
        with pytest.raises(ValueError, match="MUST_BE_FINITE"):
            polyline6_coordinate_matches(actual, expected)


def actual_boundary():
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/actual_moscow_polyline6_boundary.json").read_text()
    )
    return (
        TypeAdapter(WalkRouteRequest).validate_python(fixture["request"]),
        TypeAdapter(WalkResponseEnvelope).validate_python(fixture["envelope"]),
    )


def test_actual_saved_moscow_response_keeps_route_geometry_and_no_minimum_claim():
    request, envelope = actual_boundary()
    normalized = normalize_walk_response(request, envelope)
    assert normalized.state == "reachable"
    assert normalized.network_geometry.coordinates[0] == (37.73133, 55.817587)
    assert request.origin.correlated_location.lat == 55.817586
    assert normalized.minimum_proven is False
    assert normalized.reason_code == "MINIMUM_TIME_UNPROVEN"


def test_actual_response_two_microdegree_shift_still_fails():
    request, envelope = actual_boundary()
    valid = normalize_walk_response(request, envelope)
    coordinates = list(valid.network_geometry.coordinates)
    coordinates[0] = (coordinates[0][0], 55.817588)
    body = json.loads(envelope.response_json)
    body["trip"]["legs"][0]["shape"] = encode_shape(coordinates)
    invalid = replace(envelope, response_json=json.dumps(body))
    with pytest.raises(ValueError, match="GEOMETRY_ENDPOINT_MISMATCH"):
        normalize_walk_response(request, invalid)
