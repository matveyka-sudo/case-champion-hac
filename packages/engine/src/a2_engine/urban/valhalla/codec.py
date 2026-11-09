"""valhalla / codec — canonical urban implementation."""

import hashlib
import json

from a2_engine.urban.models import UrbanGeometry


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
