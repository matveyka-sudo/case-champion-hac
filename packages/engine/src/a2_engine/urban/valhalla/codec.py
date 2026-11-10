"""valhalla / codec — canonical urban implementation."""

import hashlib
import json
from decimal import ROUND_DOWN, Decimal

from a2_engine.urban.models import UrbanGeometry
from a2_engine.urban.valhalla._grisu2 import coordinate_boundary_echo

# Opt-in source evidence for 3.5.1's JSON route locations, distinct from /locate.
ROUTE_ECHO_TRUNC6_EVIDENCE_REF = "valhalla-3.5.1-route-echo-trunc6-v1"


def _valhalla351_route_echo(value: float) -> float | None:
    """Return a supported pinned six-place echo, without resnapping.

    route_serializer_valhalla.cc locations() writes original ll at precision6;
    rapidjson_utils.h forwards this to SetMaxDecimalPlaces. Prettify in dtoa.h
    truncates decimal digits (not the binary value multiplied by 1e6). Its tiny
    1e-7 decade uses scientific notation unchanged; smaller values become signed
    zero. Decimal preserves that sign. For six-place noninteger source boundaries,
    reproduce Grisu2's digits: they can fall below the shortest Python decimal.
    This is serialization only, not evidence that either endpoint is a physically
    exact network match.
    """
    decimal = Decimal(str(value))
    if decimal and decimal.copy_abs().adjusted() == -7:
        return value
    truncated = decimal.quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
    if decimal == truncated and decimal != decimal.to_integral_value():
        return coordinate_boundary_echo(value)
    return float(truncated)


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


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
