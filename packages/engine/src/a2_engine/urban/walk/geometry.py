"""Shapes for a certified traversal sequence, without rerouting or score changes.

Expected hashes come from a reviewed deployment registry. Native output is an
attested read of the same pinned tiles, not authority for arbitrary caller hashes.
No geometry joins are interpolated, snapped, or rounded by this checker.
"""

import hashlib
import json
import math
import struct
from dataclasses import dataclass
from typing import Any

from a2_engine.urban.walk.physical_certificate import DesignatedAccessReceipt
from a2_engine.urban.walk.source_bound import (
    NativeWalkCandidate,
    NativeWalkExport,
    binary_fraction,
)

Coordinate = tuple[float, float]
Bits = tuple[int, int]


def _sha(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _bits(value: float) -> int:
    return int.from_bytes(struct.pack("<d", value), "little")


def _coordinate(value: Any) -> Coordinate:
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise ValueError("GEOMETRY_COORDINATE_PAIR_REQUIRED")
    if any(type(v) is not int or not 0 <= v < 2**64 for v in value):
        raise ValueError("GEOMETRY_BINARY64_BITS_REQUIRED")
    lon, lat = (struct.unpack("<d", v.to_bytes(8, "little"))[0] for v in value)
    if not math.isfinite(lon) or not math.isfinite(lat) or abs(lon) > 180 or abs(lat) > 90:
        raise ValueError("GEOMETRY_FINITE_LONLAT_REQUIRED")
    return lon, lat


def _coordinate_bits(value: Any) -> Bits:
    _coordinate(value)
    return value[0], value[1]


def _node_shape_representations(value: Any) -> set[Bits]:
    # NodeInfo's native seventh-digit lattice and encoded.h's sixth-digit shape
    # lattice differ. Decimal half-microdegree points can encode on either side
    # because their original and reconstructed binary64 representations differ.
    # This is only a necessary orientation cross-check. DirectedEdge.forward()
    # in the attested native output is the direction authority. Never alter shape.
    def encoded(v: float) -> tuple[int, ...]:
        seventh = int(math.copysign(math.floor(abs(v * 1e7) + 0.5), v))
        q, remainder = divmod(seventh, 10)
        sixths = (q, q + 1) if remainder == 5 else (q + (remainder > 5),)
        return tuple(_bits(float(n) * 1e-6) for n in sixths)

    lon, lat = _coordinate(value)
    return {(x, y) for x in encoded(lon) for y in encoded(lat)}


def _projection_segment(
    raw: tuple[Bits, ...], endpoint: dict[str, Any], projection: dict[str, Any]
) -> int:
    """Replay pinned projector arithmetic and strict storage-order tie handling.

    Native libm-derived constants are attested binary64 operands. Python repeats
    the subsequent binary64 operations; the exporter disables FMA contraction.
    """
    scale = float(binary_fraction(projection["lon_scale_bits"], double=True))
    meters_lon = float(binary_fraction(projection["approx_m_per_lng_degree_bits"], double=True))
    if not 0 <= scale <= 1 or not 0 <= meters_lon <= 110567:
        raise ValueError("GEOMETRY_PROJECTOR_OPERANDS_INVALID")
    lon, lat = _coordinate(endpoint["query_bits"])
    best, selected, selected_bits = math.inf, -1, None
    for i, (a, b) in enumerate(zip(raw, raw[1:])):
        u, v = _coordinate(a), _coordinate(b)
        bx, by = v[0] - u[0], v[1] - u[1]
        bx2 = bx * scale
        square = bx2 * bx2 + by * by
        product = (lon - u[0]) * scale * bx2 + (lat - u[1]) * by
        if u == v or product <= 0:
            p = u
        elif product >= square:
            p = v
        else:
            ratio = product / square
            p = u[0] + bx * ratio, u[1] + by * ratio
        dy, dx = (p[1] - lat) * 110567.0, (p[0] - lon) * meters_lon
        distance = dy * dy + dx * dx
        if distance < best:
            best, selected, selected_bits = distance, i, (_bits(p[0]), _bits(p[1]))
    if (
        type(projection["storage_segment"]) is not int
        or selected != projection["storage_segment"]
        or selected_bits != tuple(endpoint["projected_bits"])
    ):
        raise ValueError("GEOMETRY_PINNED_PROJECTION_REPLAY_MISMATCH")
    return selected


def _plan(content: str) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(content)
    core = {k: v for k, v in body.items() if k != "plan_sha256"}
    if body.get("schema") != "waydex-certified-walk-geometry-plan/1" or _sha(
        _json(core)
    ) != body.get("plan_sha256"):
        raise ValueError("GEOMETRY_PLAN_CHANGED")
    return body


def make_certified_geometry_plan(
    native_receipt_json: str,
    candidate_json: str,
    request_json: str,
    *,
    native_receipt_sha256: str,
) -> str:
    """Bind the exact reviewed path, endpoint roster, query and original sources."""
    if _sha(native_receipt_json) != native_receipt_sha256:
        raise ValueError("GEOMETRY_NATIVE_RECEIPT_CHANGED")
    native = json.loads(native_receipt_json)
    files = native["files_sha256"]
    result = native["closed_turn_certificate"]["result"]
    source = native["closed_turn_certificate"]["source_binding"]
    scope = _sha(json.dumps(source, sort_keys=True))
    if (
        scope != native["closed_turn_certificate"]["source_scope_id"]
        or result != native["result"]
        or result["network_minimum_proven"] is not True
        or result["network_lower_seconds"] != result["network_upper_seconds"]
        or _sha(candidate_json) != files["turn-candidate.json"]
        or _sha(request_json) != source["request.json"]
    ):
        raise ValueError("GEOMETRY_CERTIFIED_SOURCE_MISMATCH")
    path = result["candidate"]
    edges = [
        {"id": edge, "fraction_bits": fraction, "final": i == len(path["traversals"]) - 1}
        for i, (edge, fraction) in enumerate(path["traversals"])
    ]
    if not edges or json.loads(candidate_json) != {"edges": edges}:
        raise ValueError("GEOMETRY_CERTIFIED_SEQUENCE_MISMATCH")
    request = json.loads(request_json)
    endpoints = []
    for side, index in ((0, path["origin_index"]), (1, path["destination_index"])):
        rows = [
            row
            for row in native["native_endpoint_inventory"]
            if row["endpoint"] == side and row["index"] == index
        ]
        if len(rows) != 1:
            raise ValueError("GEOMETRY_CERTIFIED_ENDPOINT_MISSING")
        row = rows[0]
        query = request["locations"][side]
        endpoints.append(
            {
                **row,
                "query_bits": [_bits(float(query["lon"])), _bits(float(query["lat"]))],
                "projected_bits": [row["lon_bits"], row["lat_bits"]],
            }
        )
    core = {
        "schema": "waydex-certified-walk-geometry-plan/1",
        "bindings": {
            "native_receipt_sha256": native_receipt_sha256,
            "source_scope_id": scope,
            "candidate_sha256": _sha(candidate_json),
            **source,
        },
        "edges": edges,
        "endpoints": endpoints,
    }
    return _json({**core, "plan_sha256": _sha(_json(core))})


@dataclass(frozen=True)
class CertifiedWalkGeometry:
    source_scope_id: str
    candidate_sha256: str
    native_export_sha256: str
    geometry_export_sha256: str
    physical_access_sha256: str | None
    edge_ids: tuple[int, ...]
    network_components: tuple[tuple[Coordinate, ...], ...]
    network_coordinates: tuple[Coordinate, ...] | None
    physical_coordinates: tuple[Coordinate, ...] | None
    discontinuities: tuple[int, ...]
    limitations: tuple[str, ...]

    @property
    def network_geojson(self) -> dict[str, Any] | None:
        if self.network_coordinates is None:
            return None
        return {"type": "LineString", "coordinates": self.network_coordinates}

    @property
    def physical_geojson(self) -> dict[str, Any] | None:
        if self.physical_coordinates is None:
            return None
        return {"type": "LineString", "coordinates": self.physical_coordinates}


def _join(parts: tuple[tuple[Bits, ...], ...]) -> tuple[tuple[Bits, ...] | None, tuple[int, ...]]:
    nonempty = [(i, p) for i, p in enumerate(parts) if p]
    if not nonempty:
        raise ValueError("GEOMETRY_EMPTY_TRAVERSAL")
    gaps = tuple(
        right[0] for left, right in zip(nonempty, nonempty[1:]) if left[1][-1] != right[1][0]
    )
    if gaps:
        return None, gaps
    combined = list(nonempty[0][1])
    for _, part in nonempty[1:]:
        combined.extend(part[1:])
    return tuple(combined), ()


def _physical_line(raw: dict[str, Any]) -> tuple[Bits, ...]:
    if (
        raw["traversable"] is not True
        or raw["reasons"]
        or raw["geometry_crs"] != "EPSG:4326"
        or raw["policy"] != "walk-connector-geometry-v2"
        or raw["directionality"] != "symmetric straight-link model"
        or len(raw["geometry_lonlat"]) != 2
    ):
        raise ValueError("GEOMETRY_VERIFIED_CONNECTOR_REQUIRED")
    result = tuple(tuple(_bits(float(x)) for x in p) for p in raw["geometry_lonlat"])
    return tuple(_coordinate_bits(p) for p in result)


def load_certified_walk_geometry(
    content: str,
    *,
    expected_sha256: str,
    plan_json: str,
    graph: NativeWalkExport,
    candidate: NativeWalkCandidate,
    access: DesignatedAccessReceipt | None = None,
) -> CertifiedWalkGeometry:
    """Check attested tile shapes against the independently certified exact path.

    A source-verified access receipt is optional. Without it, only the network
    portion has geometry. Disconnected components remain separate; no gap is drawn.
    """
    if _sha(content) != expected_sha256:
        raise ValueError("GEOMETRY_EXPORT_CHANGED")
    plan = _plan(plan_json)
    bindings = plan["bindings"]
    if (
        bindings["export.jsonl"] != graph.content_sha256
        or tuple((r["id"], r["fraction_bits"]) for r in plan["edges"]) != candidate.traversals
        or plan["endpoints"][0]["index"] != candidate.origin_index
        or plan["endpoints"][1]["index"] != candidate.destination_index
    ):
        raise ValueError("GEOMETRY_CERTIFIED_SEQUENCE_OR_SOURCE_CHANGED")
    rows = [json.loads(line) for line in content.splitlines()]
    if (
        len(rows) != len(candidate.traversals) + 4
        or rows[0]
        != {
            "kind": "geometry_header",
            "schema": 1,
            "shape_decode_precision_bits": _bits(1e-6),
            "shape_encode_precision": 1000000,
            "plan_sha256": plan["plan_sha256"],
            "source_scope_id": bindings["source_scope_id"],
        }
        or rows[-1] != {"kind": "geometry_footer", "edges": len(candidate.traversals)}
    ):
        raise ValueError("GEOMETRY_INCOMPLETE_EXPORT")
    projections = rows[1:3]
    edges = {e.edge_id: e for e in graph.edges}
    components: list[tuple[Bits, ...]] = []
    for ordinal, (row, traversal) in enumerate(zip(rows[3:-1], candidate.traversals, strict=True)):
        edge_id, fraction_bits = traversal
        edge = edges[edge_id]
        if (
            row["kind"] != "geometry_edge"
            or row["ordinal"] != ordinal
            or (row["id"], row["fraction_bits"]) != traversal
            or (row["from"], row["to"]) != (edge.start, edge.end)
            or type(row["forward"]) is not bool
            or type(row["way_id"]) is not int
            or row["way_id"] <= 0
        ):
            raise ValueError("GEOMETRY_DIRECTED_EDGE_MISMATCH")
        if ordinal:
            previous = edges[candidate.traversals[ordinal - 1][0]]
            if previous.end != edge.start and (previous.end, edge.start) not in graph.transitions:
                raise ValueError("GEOMETRY_TOPOLOGY_DISCONTINUITY")
        raw = tuple(_coordinate_bits(p) for p in row["shape_bits"])
        if len(raw) < 2 or len(set(raw)) < 2:
            raise ValueError("GEOMETRY_NONDEGENERATE_SHAPE_REQUIRED")
        oriented = raw if row["forward"] else tuple(reversed(raw))
        if oriented[0] not in _node_shape_representations(row["from_node_bits"]) or oriented[
            -1
        ] not in _node_shape_representations(row["to_node_bits"]):
            raise ValueError("GEOMETRY_SHAPE_NODE_ORIENTATION_MISMATCH")
        start, end = 0, len(oriented) - 1
        first, last = oriented[0], oriented[-1]
        for side in (0, 1):
            if ordinal != (0 if side == 0 else len(candidate.traversals) - 1):
                continue
            endpoint, projection = plan["endpoints"][side], projections[side]
            if (
                endpoint["edge_id"] != edge_id
                or projection["kind"] != "geometry_projection"
                or (projection["endpoint"], projection["index"], projection["edge_id"])
                != (side, endpoint["index"], edge_id)
                or projection["projected_bits"] != endpoint["projected_bits"]
            ):
                raise ValueError("GEOMETRY_NATIVE_PROJECTION_CHANGED")
            segment = _projection_segment(raw, endpoint, projection)
            projected = _coordinate_bits(projection["projected_bits"])
            p, u, v = (
                _coordinate(projected),
                _coordinate(raw[segment]),
                _coordinate(raw[segment + 1]),
            )
            if any(not min(a, b) <= x <= max(a, b) for x, a, b in zip(p, u, v, strict=True)):
                raise ValueError("GEOMETRY_PROJECTION_OUTSIDE_SEGMENT")
            directed_segment = segment if row["forward"] else len(raw) - 2 - segment
            if side == 0:
                start, first = directed_segment + 1, projected
            else:
                end, last = directed_segment, projected
        if start > end + 1:
            raise ValueError("GEOMETRY_REVERSED_CLIP_INTERVAL")
        clipped = (first, *oriented[start : end + 1], last)
        # Removing adjacent exact duplicates changes no coordinates or segments.
        clipped = tuple(p for i, p in enumerate(clipped) if i == 0 or p != clipped[i - 1])
        if binary_fraction(fraction_bits) == 0:
            clipped = ()
        components.append(clipped)
    joined, gaps = _join(tuple(components))
    physical = None
    limitations = ["CANONICAL_ACCESS_MODEL", "PINNED_TILE_SHAPE_6_DECIMAL_ENCODING"]
    if gaps:
        limitations.append("EXACT_NETWORK_SHAPE_DISCONTINUITY")
    if access is None:
        limitations.append("PHYSICAL_CONNECTOR_GEOMETRY_UNAVAILABLE")
    else:
        if not access.source_json or (
            access.native_scope_id != bindings["source_scope_id"]
            or access.native_export_sha256 != graph.content_sha256
        ):
            raise ValueError("GEOMETRY_PHYSICAL_SOURCE_MISMATCH")
        raw_access = json.loads(access.source_json)
        links = []
        for side, index in ((0, candidate.origin_index), (1, candidate.destination_index)):
            choice = next(c for c in access.choices if c.endpoint == side and c.index == index)
            endpoint = plan["endpoints"][side]
            if (choice.edge_id, choice.lon_bits, choice.lat_bits) != (
                endpoint["edge_id"],
                *endpoint["projected_bits"],
            ):
                raise ValueError("GEOMETRY_PHYSICAL_ENDPOINT_MISMATCH")
            raw_choice = next(
                r
                for s in raw_access["endpoints"]
                for r in s["native_alternatives"]
                if r["endpoint"] == side and r["index"] == index
            )
            connector = _physical_line(raw_choice["external_physical_connector"])
            offset = _physical_line(raw_choice["access_to_native_offset"])
            links.append(
                (connector, offset)
                if side == 0
                else (tuple(reversed(offset)), tuple(reversed(connector)))
            )
        if joined is not None:
            physical, physical_gaps = _join((*links[0], joined, *links[1]))
            if physical_gaps:
                raise ValueError("GEOMETRY_PHYSICAL_CONNECTOR_DISCONTINUITY")
    return CertifiedWalkGeometry(
        bindings["source_scope_id"],
        bindings["candidate_sha256"],
        graph.content_sha256,
        expected_sha256,
        access.content_sha256 if access is not None else None,
        tuple(e for e, _ in candidate.traversals),
        tuple(tuple(_coordinate(p) for p in part) for part in components),
        tuple(_coordinate(p) for p in joined) if joined is not None else None,
        tuple(_coordinate(p) for p in physical) if physical is not None else None,
        gaps,
        tuple(limitations),
    )
