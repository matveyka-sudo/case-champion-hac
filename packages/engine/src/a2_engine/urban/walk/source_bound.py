"""Exact bounds on a pinned native pedestrian relaxation; never a score adapter.

The native exporter binds topology and weights to libvalhalla 3.5.1. A complete
Loki result is an endpoint set for this declared search, not an exhaustive roster
of physical entrances. Physical completeness and correlation offsets stay explicit.
The objective is the exact additive sum of binary32 model weights, not the
millisecond HTTP summary or Valhalla's rounded floating accumulation.
"""

import hashlib
import json
import math
import struct
from dataclasses import dataclass
from fractions import Fraction
from heapq import heappop, heappush
from typing import Any


def binary_fraction(bits: int, *, double: bool = False) -> Fraction:
    if type(bits) is not int or not 0 <= bits < 2 ** (64 if double else 32):
        raise ValueError("INVALID_FLOAT_BITS")
    number = struct.unpack("<d" if double else "<f", bits.to_bytes(8 if double else 4, "little"))[0]
    if not math.isfinite(number) or number < 0:
        raise ValueError("FINITE_NONNEGATIVE_NATIVE_WEIGHT_REQUIRED")
    return Fraction(number)


def _float_bits(value: Fraction) -> int:
    return int.from_bytes(struct.pack("<f", float(value)), "little")


@dataclass(frozen=True)
class NativeEdge:
    edge_id: int
    start: int
    end: int
    seconds: Fraction
    retained: bool


@dataclass(frozen=True)
class NativeEndpoint:
    endpoint: int
    index: int
    edge_id: int
    percent: Fraction
    lon_bits: int
    lat_bits: int
    partial_seconds: Fraction


@dataclass(frozen=True)
class NativeWalkExport:
    content_sha256: str
    nodes: frozenset[int]
    edges: tuple[NativeEdge, ...]
    transitions: tuple[tuple[int, int], ...]
    endpoints: tuple[NativeEndpoint, ...]
    tile_ids: frozenset[int]


@dataclass(frozen=True)
class NativeWalkCandidate:
    origin_index: int
    destination_index: int
    # Each pair is (directed edge ID, binary32 traversal-fraction bits).
    traversals: tuple[tuple[int, int], ...]
    lower_seconds: Fraction
    edge_seconds: tuple[Fraction, ...]

    def replay_json(self) -> str:
        return json.dumps(
            {
                "edges": [
                    {"id": edge, "fraction_bits": bits, "final": i == len(self.traversals) - 1}
                    for i, (edge, bits) in enumerate(self.traversals)
                ]
            },
            separators=(",", ":"),
        )


@dataclass(frozen=True)
class NativeWalkBounds:
    network_lower_seconds: Fraction
    network_upper_seconds: Fraction | None
    physical_lower_seconds: Fraction | None
    physical_upper_seconds: Fraction | None
    network_minimum_proven: bool
    physical_minimum_proven: bool
    reasons: tuple[str, ...]
    candidate: NativeWalkCandidate


def load_native_walk_export(content: str, *, expected_sha256: str) -> NativeWalkExport:
    """Check transport/inventory integrity of an actually executed native export.

    expected_sha256 must come from the executor's immutable receipt. This checker
    does not manufacture that receipt or prove arbitrary caller-supplied hashes.
    """
    digest = hashlib.sha256(content.encode()).hexdigest()
    if digest != expected_sha256:
        raise ValueError("NATIVE_EXPORT_CONTENT_CHANGED")
    rows: list[dict[str, Any]] = [json.loads(line) for line in content.splitlines()]
    if (
        not rows
        or rows[0]
        != {
            "kind": "header",
            "schema": 1,
            "model": "valhalla-3.5.1-binary32-edge-seconds-zero-turn-relaxation",
        }
        or rows[-1].get("kind") != "footer"
    ):
        raise ValueError("INCOMPLETE_NATIVE_EXPORT")
    nodes: set[int] = set()
    edges: dict[int, NativeEdge] = {}
    transitions: set[tuple[int, int]] = set()
    endpoints: dict[tuple[int, int], NativeEndpoint] = {}
    tiles: dict[int, tuple[int, int]] = {}
    for row in rows[1:-1]:
        kind = row["kind"]
        if kind == "node":
            if row["id"] in nodes:
                raise ValueError("DUPLICATE_NATIVE_NODE")
            nodes.add(row["id"])
        elif kind == "edge":
            if row["id"] in edges or type(row["retained"]) is not bool:
                raise ValueError("INVALID_NATIVE_EDGE_ROSTER")
            edges[row["id"]] = NativeEdge(
                row["id"],
                row["from"],
                row["to"],
                binary_fraction(row["seconds_bits"]),
                row["retained"],
            )
        elif kind == "transition":
            transition = (row["from"], row["to"])
            if transition in transitions:
                raise ValueError("DUPLICATE_HIERARCHY_TRANSITION")
            transitions.add(transition)
        elif kind == "endpoint":
            key = (row["endpoint"], row["index"])
            if key in endpoints or row["endpoint"] not in (0, 1):
                raise ValueError("INVALID_ENDPOINT_ROSTER")
            percent = binary_fraction(row["percent_bits"], double=True)
            if percent > 1:
                raise ValueError("INVALID_NATIVE_PERCENT")
            # Coordinates retain their exact binary64 representation. They are
            # not silently declared equal to a physical/accepted access point.
            for axis in ("lon_bits", "lat_bits"):
                bits = row[axis]
                value = struct.unpack("<d", bits.to_bytes(8, "little"))[0]
                if not math.isfinite(value):
                    raise ValueError("INVALID_NATIVE_COORDINATE")
            endpoints[key] = NativeEndpoint(
                *key,
                row["edge"],
                percent,
                row["lon_bits"],
                row["lat_bits"],
                binary_fraction(row["partial_seconds_bits"]),
            )
        elif kind == "tile":
            if row["id"] in tiles:
                raise ValueError("DUPLICATE_NATIVE_TILE")
            tiles[row["id"]] = (row["nodes"], row["edges"])
        else:
            raise ValueError("UNSUPPORTED_NATIVE_RECORD")
    footer = rows[-1]
    if (
        len(nodes),
        len(edges),
        len(tiles),
        len(transitions),
        sum(e.retained for e in edges.values()),
    ) != (
        footer["nodes"],
        footer["edges"],
        footer["tiles"],
        footer["transitions"],
        footer["retained_edges"],
    ) or (sum(n for n, _ in tiles.values()), sum(e for _, e in tiles.values())) != (
        len(nodes),
        len(edges),
    ):
        raise ValueError("NATIVE_INVENTORY_INCOMPLETE")
    if any(e.start not in nodes or e.end not in nodes for e in edges.values()) or any(
        a not in nodes or b not in nodes for a, b in transitions
    ):
        raise ValueError("NATIVE_GRAPH_NOT_CLOSED")
    for endpoint in (0, 1):
        indices = sorted(i for ep, i in endpoints if ep == endpoint)
        if not indices or indices != list(range(len(indices))):
            raise ValueError("NATIVE_ENDPOINT_INVENTORY_INCOMPLETE")
    if any(e.edge_id not in edges for e in endpoints.values()):
        raise ValueError("UNKNOWN_ENDPOINT_EDGE")
    for match in endpoints.values():
        edge = edges[match.edge_id]
        if edge.retained:
            fraction = match.percent if match.endpoint else 1 - match.percent
            expected = binary_fraction(
                _float_bits(edge.seconds * binary_fraction(_float_bits(fraction)))
            )
            if expected != match.partial_seconds:
                raise ValueError("NATIVE_SPLIT_WEIGHT_MISMATCH")
    return NativeWalkExport(
        digest,
        frozenset(nodes),
        tuple(edges.values()),
        tuple(sorted(transitions)),
        tuple(endpoints.values()),
        frozenset(tiles),
    )


def solve_native_walk_relaxation(graph: NativeWalkExport) -> NativeWalkCandidate:
    """All inventoried edges/zero-time hierarchy transitions; exact predecessors."""
    edges = {edge.edge_id: edge for edge in graph.edges}
    outgoing: dict[int, list[tuple[int, int | None, Fraction]]] = {}
    for edge in graph.edges:
        if edge.retained:
            outgoing.setdefault(edge.start, []).append((edge.end, edge.edge_id, edge.seconds))
    for start, end in graph.transitions:
        outgoing.setdefault(start, []).append((end, None, Fraction()))
    origins = [e for e in graph.endpoints if e.endpoint == 0 and edges[e.edge_id].retained]
    finals = [e for e in graph.endpoints if e.endpoint == 1 and edges[e.edge_id].retained]
    distances: dict[int, Fraction] = {}
    previous: dict[int, tuple[int, int | None]] = {}
    chosen_origin: dict[int, int] = {}
    heap: list[tuple[Fraction, int]] = []
    origin_map = {e.index: e for e in origins}
    for origin in origins:
        node = edges[origin.edge_id].end
        if origin.partial_seconds < distances.get(node, origin.partial_seconds + 1):
            distances[node] = origin.partial_seconds
            chosen_origin[node] = origin.index
            heappush(heap, (origin.partial_seconds, node))
    while heap:
        seconds, node = heappop(heap)
        if seconds != distances[node]:
            continue
        for target, edge_id, weight in outgoing.get(node, []):
            total = seconds + weight
            if total < distances.get(target, total + 1):
                distances[target] = total
                previous[target] = (node, edge_id)
                chosen_origin[target] = chosen_origin[node]
                heappush(heap, (total, target))
    best: NativeWalkCandidate | None = None
    for final in finals:
        node = edges[final.edge_id].start
        if node not in distances:
            continue
        origin = origin_map[chosen_origin[node]]
        middle = []
        while node in previous:
            node, edge_id = previous[node]
            if edge_id is not None:
                middle.append((edge_id, _float_bits(Fraction(1))))
        traversals = (
            (origin.edge_id, _float_bits(1 - origin.percent)),
            *reversed(middle),
            (final.edge_id, _float_bits(final.percent)),
        )
        candidate = NativeWalkCandidate(
            origin.index,
            final.index,
            tuple(traversals),
            distances[edges[final.edge_id].start] + final.partial_seconds,
            (
                origin.partial_seconds,
                *(edges[edge_id].seconds for edge_id, _ in reversed(middle)),
                final.partial_seconds,
            ),
        )
        if best is None or candidate.lower_seconds < best.lower_seconds:
            best = candidate
    # Direct traversal of an edge shared by both endpoints is a separate route;
    # travelling to its end and back would otherwise omit this legal alternative.
    for origin in origins:
        for final in finals:
            if origin.edge_id == final.edge_id and final.percent >= origin.percent:
                fraction_bits = _float_bits(final.percent - origin.percent)
                weight = binary_fraction(
                    _float_bits(edges[origin.edge_id].seconds * binary_fraction(fraction_bits))
                )
                candidate = NativeWalkCandidate(
                    origin.index, final.index, ((origin.edge_id, fraction_bits),), weight, (weight,)
                )
                if best is None or candidate.lower_seconds < best.lower_seconds:
                    best = candidate
    if best is None:
        raise ValueError("NO_RELAXED_PATH_IN_DECLARED_ENDPOINT_SET")
    return best


def check_native_walk_bounds(
    candidate: NativeWalkCandidate,
    replay: str | None,
    *,
    origin_connector_seconds: Fraction,
    destination_connector_seconds: Fraction,
    origin_offset_seconds: Fraction | None = None,
    destination_offset_seconds: Fraction | None = None,
    physical_endpoint_inventory_complete: bool = False,
) -> NativeWalkBounds:
    """Physical minimum requires exact equality, known offsets and complete access.

    Completeness is an external source obligation and defaults to false. Positive
    connector weights occur exactly once on each side. Unknown offsets are zero
    only in the relaxation, because a nonnegative unknown has that lower bound.
    """
    for value in (
        origin_connector_seconds,
        destination_connector_seconds,
        origin_offset_seconds,
        destination_offset_seconds,
    ):
        if value is not None and (not isinstance(value, Fraction) or value < 0):
            raise ValueError("EXACT_NONNEGATIVE_CONNECTOR_SECONDS_REQUIRED")
    upper: Fraction | None = None
    reasons = []
    if replay is not None:
        rows = [json.loads(line) for line in replay.splitlines()]
        if not rows or rows[-1] != {
            "kind": "replay_footer",
            "feasible": True,
            "edges": len(candidate.traversals),
        }:
            raise ValueError("INCOMPLETE_NATIVE_REPLAY")
        if len(rows) - 1 != len(candidate.traversals) or any(
            row.get("kind") != "replay"
            or row["id"] != expected[0]
            or row.get("fraction_bits") != expected[1]
            for row, expected in zip(rows[:-1], candidate.traversals, strict=True)
        ):
            raise ValueError("NATIVE_REPLAY_CANDIDATE_MISMATCH")
        if sum(candidate.edge_seconds, Fraction()) != candidate.lower_seconds or any(
            binary_fraction(row["edge_seconds_bits"]) != expected
            for row, expected in zip(rows[:-1], candidate.edge_seconds, strict=True)
        ):
            raise ValueError("NATIVE_REPLAY_EDGE_WEIGHT_MISMATCH")
        upper = sum(
            (
                binary_fraction(row["edge_seconds_bits"])
                + binary_fraction(row["turn_seconds_bits"])
                for row in rows[:-1]
            ),
            Fraction(),
        )
        if upper < candidate.lower_seconds:
            raise ValueError("RELAXATION_EXCEEDS_FEASIBLE_PATH")
    else:
        reasons.append("FEASIBLE_NATIVE_REPLAY_UNAVAILABLE")
    connectors = origin_connector_seconds + destination_connector_seconds
    offsets = (origin_offset_seconds or Fraction()) + (destination_offset_seconds or Fraction())
    lower = connectors + offsets + candidate.lower_seconds
    physical_upper = (
        connectors + offsets + upper
        if upper is not None
        and origin_offset_seconds is not None
        and destination_offset_seconds is not None
        else None
    )
    if origin_offset_seconds is None or destination_offset_seconds is None:
        reasons.append("PHYSICAL_CORRELATION_OFFSET_UNCONFIRMED")
    if not physical_endpoint_inventory_complete:
        reasons.append("PHYSICAL_ENDPOINT_ALTERNATIVES_UNCONFIRMED")
    minimum = upper == candidate.lower_seconds
    if not minimum:
        reasons.append("ELAPSED_MINIMUM_UNPROVEN")
    return NativeWalkBounds(
        candidate.lower_seconds,
        upper,
        lower if physical_endpoint_inventory_complete else None,
        physical_upper,
        minimum,
        minimum and physical_upper == lower and physical_endpoint_inventory_complete,
        tuple(sorted(reasons)),
        candidate,
    )
