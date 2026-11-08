"""Access nodes: where a sample point or a destination joins the walk network.

PRODUCT_SPEC §2: the access to the graph is built separately and its distance
does not disappear from travel time. v1 projects the point onto the nearest
way of the *largest connected component* of the OSM walk network
(``osm-walk-network-v1``), so an access node never lands on an isolated
fragment. Ties go to the smaller way id. The node id encodes the way and the
offset along it, so identical inputs give identical ids.

Requires ``shapely`` and ``pyproj`` (see README).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import numpy as np
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import shapely  # type: ignore[import-untyped]

from .geo import to_crs
from .osm import WALK_RULES_VERSION

ACCESS_KIND: Final = "walk_network_projection"
TIE_TOLERANCE_M = 1e-3


@dataclass
class WalkNetwork:
    crs: str
    graph_version: str
    lines: np.ndarray  # UTM LineStrings of the main component
    refs: list[str]
    hashes: dict[str, str]
    tree: shapely.STRtree
    stats: dict[str, Any]


@dataclass(frozen=True)
class Access:
    node_id: str
    way_ref: str
    offset_m: float
    point: shapely.Point  # UTM
    distance_m: float


def _way_number(ref: str) -> int:
    return int(ref.split("/")[1])


def load_walk_network(osm_dir: Path, utm_epsg: int, dataset_id: str) -> WalkNetwork:
    rows: list[dict[str, Any]] = pq.read_table(osm_dir / "walk_ways.parquet").to_pylist()
    parent: dict[int, int] = {}

    def find(node: int) -> int:
        root = node
        while parent.setdefault(root, root) != root:
            root = parent[root]
        while parent[node] != root:
            parent[node], node = root, parent[node]
        return root

    for row in rows:
        nodes = row["node_refs"]
        for a, b in zip(nodes, nodes[1:]):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)
    crs = f"EPSG:{utm_epsg}"
    lines = to_crs(shapely.from_wkb([row["wkb"] for row in rows]), "EPSG:4326", crs)
    lengths = shapely.length(lines)
    component = np.array([find(row["node_refs"][0]) for row in rows])
    totals: dict[int, float] = {}
    for comp, length in zip(component, lengths):
        totals[int(comp)] = totals.get(int(comp), 0.0) + float(length)
    main = max(totals, key=lambda key: (totals[key], -key))
    keep = component == main
    refs = [row["osm_ref"] for row, flag in zip(rows, keep) if flag]
    kept_lines = lines[keep]
    total_length = float(lengths.sum())
    stats = {
        "walk_rules": WALK_RULES_VERSION,
        "ways_total": len(rows),
        "ways_main_component": int(keep.sum()),
        "components": len(totals),
        "main_component_length_share": round(totals[main] / total_length, 4),
    }
    return WalkNetwork(
        crs=crs,
        graph_version=f"{dataset_id}:{WALK_RULES_VERSION}:main-component",
        lines=kept_lines,
        refs=refs,
        hashes={row["osm_ref"]: row["content_hash"] for row in rows},
        tree=shapely.STRtree(kept_lines),
        stats=stats,
    )


def snap(points_utm: list[shapely.Point], network: WalkNetwork) -> list[Access]:
    """Project UTM points onto the main walk component.

    The nearest way wins; ways within ``TIE_TOLERANCE_M`` of the minimum distance
    are a tie, resolved by the smaller way id, so float noise cannot pick a way.
    """

    if not points_utm:
        return []
    geoms = np.array(points_utm, dtype=object)
    pairs, distances = network.tree.query_nearest(geoms, return_distance=True)
    minimum: dict[int, float] = {}
    for point_index, distance in zip(pairs[0], distances):
        index = int(point_index)
        minimum[index] = min(minimum.get(index, float("inf")), float(distance))
    result: list[Access] = []
    for index, point in enumerate(points_utm):
        if index not in minimum:
            raise RuntimeError(f"point {index} has no walk way")
        limit = minimum[index] + TIE_TOLERANCE_M
        candidates = network.tree.query(point, predicate="dwithin", distance=limit)
        line_index = min(
            (int(i) for i in candidates if network.lines[int(i)].distance(point) <= limit),
            key=lambda i: _way_number(network.refs[i]),
        )
        line = network.lines[line_index]
        offset = round(float(line.project(point)), 1)
        projected = line.interpolate(offset)
        ref = network.refs[line_index]
        result.append(
            Access(
                node_id=f"walk:way:{_way_number(ref)}:{offset:.1f}",
                way_ref=ref,
                offset_m=offset,
                point=projected,
                distance_m=float(point.distance(projected)),
            )
        )
    return result
