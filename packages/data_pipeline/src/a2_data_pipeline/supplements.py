"""Bundle supplements: walk connectors and timetable stops, published as a new bundle version.

``walk-connector-geometry-v2`` (ML1 ``WalkConnector``): the straight link from every
sample point and destination to its walk access node is checked against the OSM
layers of the bundle's own extract and PBF (same sha256):

- it crosses no building other than the endpoint's own (the building a sample point
  was placed on, the destination's OSM building, any building holding the point);
- no water area, no surface railway (``rail``, ``light_rail``, ``narrow_gauge``,
  ``monorail``; tunnels and bridges excluded), no motorway or trunk;
- v2: no linear barrier (``barrier=fence|wall|city_wall|retaining_wall|hedge|
  guard_rail|jersey_barrier|cable_barrier|ditch``) unless it crosses it at a gate
  node of that barrier (within 10 m) open to pedestrians: ``foot=yes|designated|
  permissive`` or ``access=yes|permissive`` without ``foot=no``; a gate with
  ``foot``/``access`` ``no|private`` or ``locked=yes`` is closed, a gate without
  access tags is unknown;
- it is at most 500 m;

then ``traversable=True``: no mapped obstacle on the line, which is the evidence
the link has. A crossing leaves it unknown (``None``) with reasons: a bridge, an
underpass or an unmapped gate may exist, so nothing is declared impassable.
(v1 had no barriers: a fence with ``foot=no`` did not stop the link.)
Seconds are not invented: ``walk_connectors()`` derives them from the walking speed
of the accepted pedestrian profile it is given (the Valhalla build's), and without
one the connector stays unconfirmed.

``transit-stops-v1``: stops of a timetable (GTFS) that trips use, within 10 km of the
city boundary, are joined to the walk network like points and destinations; a stop
farther than 150 m from the network is quarantined. The GTFS archive itself stays
outside the bundle, bound by sha256 (``TimetableRef``).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import zipfile
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import shapely  # type: ignore[import-untyped]

from .access import WalkNetwork, load_walk_network, snap
from .bundle import (
    AccessNode,
    BundleData,
    CoverageRecord,
    Evidence,
    SourceVersion,
    TimetableRef,
    TransitStop,
    WalkConnector,
)
from .cities import CITIES
from .geo import to_crs
from .manifest import load_dataset_manifest
from .osm import ensure_extract, file_sha256
from .osm_national import _entities
from .paths import data_dir, processed_dir, raw_dir
from .store import BUNDLE_COLLECTIONS, load_bundle, publish_bundle

CONNECTOR_RULES = "walk-connector-geometry-v2"
STOP_RULES = "transit-stops-v1"
MAX_CONNECTOR_M = 500.0
STOP_ACCESS_M = 150.0
STOP_SEARCH_M = 10_000.0
MAJOR_ROADS = {"motorway", "motorway_link", "trunk", "trunk_link"}
RAILWAYS = ("rail", "light_rail", "narrow_gauge", "monorail")
LINEAR_BARRIERS = (
    "fence",
    "wall",
    "city_wall",
    "retaining_wall",
    "hedge",
    "guard_rail",
    "jersey_barrier",
    "cable_barrier",
    "ditch",
)
GATES = {
    "gate",
    "lift_gate",
    "swing_gate",
    "sliding_gate",
    "wicket_gate",
    "kissing_gate",
    "turnstile",
    "full-height_turnstile",
    "stile",
    "entrance",
    "bump_gate",
    "hampshire_gate",
}
GATE_TOLERANCE_M = 10.0
OPEN_FOOT = {"yes", "designated", "permissive"}
CLOSED = {"no", "private"}


def gate_access(tags: dict[str, str]) -> str:
    """``open``, ``closed`` or ``unknown`` for a pedestrian at a gate node."""

    foot, access = tags.get("foot"), tags.get("access")
    if tags.get("locked") == "yes" or foot in CLOSED:
        return "closed"
    if foot in OPEN_FOOT:
        return "open"
    if access in CLOSED:
        return "closed"
    if access in {"yes", "permissive"}:
        return "open"
    return "unknown"


WGS84 = "EPSG:4326"


def railway_lines(pbf: Path, bbox: tuple[float, float, float, float] | None) -> list[Any]:
    """Surface railway lines (lon/lat) of the PBF inside ``bbox``: two filtered passes."""

    import osmium

    ways: dict[int, list[int]] = {}
    for way in _entities(
        str(pbf),
        osmium.osm.WAY,
        osmium.filter.TagFilter(*(("railway", value) for value in RAILWAYS)),
    ):
        tags = {t.k: t.v for t in way.tags}
        if tags.get("tunnel", "no") != "no" or tags.get("bridge", "no") != "no":
            continue
        ways[way.id] = [n.ref for n in way.nodes]
    needed = {n for nodes in ways.values() for n in nodes}
    location: dict[int, tuple[float, float]] = {}
    if needed:
        for node in _entities(str(pbf), osmium.osm.NODE, osmium.filter.IdFilter(needed)):
            location[node.id] = (node.location.lon, node.location.lat)
    lines = []
    for way_id in sorted(ways):
        points = [location[n] for n in ways[way_id] if n in location]
        if len(points) < 2:
            continue
        if bbox is not None:
            west, south, east, north = bbox
            if not any(west <= x <= east and south <= y <= north for x, y in points):
                continue
        lines.append(shapely.LineString(points))
    return lines


class BarrierLayer:
    """Linear barriers with the gate nodes on them (UTM)."""

    def __init__(self, lines: list[Any], gates: list[list[tuple[Any, str]]]) -> None:
        self.lines = np.array(lines, dtype=object)
        self.gates = gates
        self.tree = shapely.STRtree(self.lines)

    def reasons(self, segment: Any) -> list[str]:
        found: set[str] = set()
        for index in self.tree.query(segment, predicate="intersects"):
            crossing = segment.intersection(self.lines[int(index)])
            points = (
                [crossing]
                if crossing.geom_type == "Point"
                else list(getattr(crossing, "geoms", [crossing]))
            )
            for point in points:
                near = [
                    (gate.distance(point), state)
                    for gate, state in self.gates[int(index)]
                    if gate.distance(point) <= GATE_TOLERANCE_M
                ]
                if not near:
                    found.add("CROSSES_BARRIER")
                    continue
                state = min(near, key=lambda item: item[0])[1]
                if state == "closed":
                    found.add("CROSSES_CLOSED_GATE")
                elif state == "unknown":
                    found.add("GATE_ACCESS_UNKNOWN")
        return sorted(found)


def barrier_layer(pbf: Path, bbox: Any, crs: str) -> BarrierLayer:
    """Linear barriers of the PBF inside ``bbox`` and their gates: two filtered passes."""

    import osmium

    ways: dict[int, list[int]] = {}
    for way in _entities(
        str(pbf),
        osmium.osm.WAY,
        osmium.filter.TagFilter(*(("barrier", value) for value in LINEAR_BARRIERS)),
    ):
        ways[way.id] = [n.ref for n in way.nodes]
    needed = {n for nodes in ways.values() for n in nodes}
    location: dict[int, tuple[float, float]] = {}
    gate_state: dict[int, str] = {}
    if needed:
        for node in _entities(str(pbf), osmium.osm.NODE, osmium.filter.IdFilter(needed)):
            location[node.id] = (node.location.lon, node.location.lat)
            tags = {t.k: t.v for t in node.tags}
            if tags.get("barrier") in GATES:
                gate_state[node.id] = gate_access(tags)
    lines: list[Any] = []
    gates: list[list[tuple[Any, str]]] = []
    for way_id in sorted(ways):
        refs = [n for n in ways[way_id] if n in location]
        if len(refs) < 2:
            continue
        points = [location[n] for n in refs]
        if bbox is not None:
            west, south, east, north = bbox
            if not any(west <= x <= east and south <= y <= north for x, y in points):
                continue
        lines.append(to_crs(shapely.LineString(points), WGS84, crs))
        gates.append(
            [
                (to_crs(shapely.Point(location[n]), WGS84, crs), gate_state[n])
                for n in refs
                if n in gate_state
            ]
        )
    return BarrierLayer(lines, gates)


class Layer:
    def __init__(self, geoms: list[Any], refs: list[str]) -> None:
        self.geoms = np.array(geoms, dtype=object)
        self.refs = refs
        self.tree = shapely.STRtree(self.geoms)

    def crossed(self, segment: Any, skip: set[str] = frozenset()) -> bool:  # type: ignore[assignment]
        for index in self.tree.query(segment, predicate="intersects"):
            if self.refs[int(index)] not in skip:
                return True
        return False


def obstacle_layers(osm_dir: Path, pbf: Path, bbox: Any, crs: str) -> dict[str, Layer]:
    def table(name: str, columns: list[str]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = pq.read_table(
            osm_dir / f"{name}.parquet", columns=columns
        ).to_pylist()
        return rows

    def projected(wkbs: list[bytes]) -> list[Any]:
        return list(to_crs(shapely.from_wkb(np.array(wkbs, dtype=object)), WGS84, crs))

    buildings = table("buildings", ["osm_ref", "wkb"])
    water = table("water", ["osm_ref", "wkb"])
    roads = [
        r for r in table("road_ways", ["osm_ref", "highway", "wkb"]) if r["highway"] in MAJOR_ROADS
    ]
    rails = railway_lines(pbf, bbox)
    return {
        "building": Layer(
            projected([r["wkb"] for r in buildings]), [r["osm_ref"] for r in buildings]
        ),
        "water": Layer(projected([r["wkb"] for r in water]), [r["osm_ref"] for r in water]),
        "motorway": Layer(projected([r["wkb"] for r in roads]), [r["osm_ref"] for r in roads]),
        "railway": Layer(
            list(to_crs(np.array(rails, dtype=object), WGS84, crs)) if rails else [],
            [f"railway/{i}" for i in range(len(rails))],
        ),
    }


REASON = {
    "building": "CROSSES_OTHER_BUILDING",
    "water": "CROSSES_WATER",
    "railway": "CROSSES_RAILWAY",
    "motorway": "CROSSES_MOTORWAY_OR_TRUNK",
}


def build_connectors(
    data: BundleData,
    layers: dict[str, Layer],
    crs: str,
    osm_evidence_id: str,
    barriers: BarrierLayer | None = None,
) -> list[WalkConnector]:
    nodes = {n.access_node_id: n for n in data.access_nodes}
    ends: list[tuple[str, str, float, float, str, set[str]]] = []
    for point in data.sample_points:
        own = {point.building_ref} if point.building_ref else set()
        ends.append(("point", point.point_id, point.lon, point.lat, point.access_node_id, own))
    for destination in data.destinations:
        own = {destination.osm_ref} if destination.osm_ref else set()
        ends.append(
            (
                "destination",
                destination.destination_id,
                destination.lon,
                destination.lat,
                destination.access_node_id,
                own,
            )
        )
    physical = to_crs(np.array([shapely.Point(e[2], e[3]) for e in ends], dtype=object), WGS84, crs)
    access = to_crs(
        np.array([shapely.Point(nodes[e[4]].lon, nodes[e[4]].lat) for e in ends], dtype=object),
        WGS84,
        crs,
    )
    connectors = []
    for (kind, entity_id, _, _, access_id, own), start, end in zip(
        ends, physical, access, strict=True
    ):
        segment = shapely.LineString([start, end])
        holding = {
            layers["building"].refs[int(i)]
            for i in layers["building"].tree.query(start, predicate="intersects")
        }
        skip = own | holding
        reasons = [
            REASON[name]
            for name in ("building", "water", "railway", "motorway")
            if layers[name].crossed(segment, skip if name == "building" else set())
        ]
        if barriers is not None:
            reasons.extend(barriers.reasons(segment))
        length = round(float(segment.length), 1)
        if length > MAX_CONNECTOR_M:
            reasons.append("LONGER_THAN_500M")
        connectors.append(
            WalkConnector(
                physical_node_id=f"physical:{kind}:{entity_id}",
                entity_kind=kind,  # type: ignore[arg-type]
                entity_id=entity_id,
                access_node_id=access_id,
                length_m=length,
                traversable=None if reasons else True,
                reasons=reasons,
                evidence_ids=sorted({osm_evidence_id, *nodes[access_id].evidence_ids}),
            )
        )
    return connectors


def _gtfs_stops(gtfs_zip: Path) -> list[dict[str, str]]:
    with zipfile.ZipFile(gtfs_zip) as archive:
        used: set[str] = set()
        with archive.open("stop_times.txt") as handle:
            for row in csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8")):
                used.add(row["stop_id"])
        with archive.open("stops.txt") as handle:
            stops = list(csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8")))
    return [s for s in stops if s["stop_id"] in used]


def build_transit_stops(
    data: BundleData,
    gtfs_zip: Path,
    timetable_id: str,
    network: WalkNetwork,
    crs: str,
    stop_evidence_id: str,
    osm_dataset_id: str,
    osm_fetched_at: datetime,
) -> tuple[list[TransitStop], list[AccessNode], list[Evidence], dict[str, Any]]:
    city = next(t for t in data.territories if t.level == "city")
    boundary = to_crs(shapely.geometry.shape(city.geometry), WGS84, crs).buffer(STOP_SEARCH_M)
    rows = _gtfs_stops(gtfs_zip)
    points = to_crs(
        np.array(
            [shapely.Point(float(r["stop_lon"]), float(r["stop_lat"])) for r in rows], dtype=object
        ),
        WGS84,
        crs,
    )
    inside = [(r, p) for r, p in zip(rows, points, strict=True) if boundary.contains(p)]
    accesses = snap([p for _, p in inside], network)
    existing = {n.access_node_id for n in data.access_nodes}
    evidence_ids = {e.evidence_id for e in data.evidence}
    new_nodes: dict[str, AccessNode] = {}
    new_evidence: dict[str, Evidence] = {}
    stops = []
    for (row, _), acc in zip(inside, accesses, strict=True):
        way_evidence = f"osm:{acc.way_ref.replace('/', ':')}"
        if way_evidence not in evidence_ids and way_evidence not in new_evidence:
            new_evidence[way_evidence] = Evidence(
                evidence_id=way_evidence,
                source_id="osm_geofabrik",
                dataset_id=osm_dataset_id,
                record_id=acc.way_ref,
                url=f"https://www.openstreetmap.org/{acc.way_ref}",
                fetched_at=osm_fetched_at,
                content_hash=network.hashes[acc.way_ref],
                verification_state="verified",
            )
        if acc.node_id not in existing and acc.node_id not in new_nodes:
            lon, lat = to_crs(acc.point, crs, WGS84).coords[0]
            new_nodes[acc.node_id] = AccessNode(
                access_node_id=acc.node_id,
                kind=network.kind,
                lon=round(lon, 7),
                lat=round(lat, 7),
                graph_version=network.graph_version,
                osm_way_ref=acc.way_ref,
                offset_along_way_m=acc.offset_m,
                evidence_ids=[way_evidence],
            )
        far = acc.distance_m > STOP_ACCESS_M
        stops.append(
            TransitStop(
                stop_id=f"{timetable_id}:{row['stop_id']}",
                timetable_id=timetable_id,
                gtfs_stop_id=row["stop_id"],
                name=row["stop_name"] or None,
                lon=round(float(row["stop_lon"]), 7),
                lat=round(float(row["stop_lat"]), 7),
                access_node_id=acc.node_id,
                access_distance_m=round(acc.distance_m, 1),
                status="quarantined" if far else "accepted",
                quarantine_reasons=["FAR_FROM_WALK_NETWORK"] if far else [],
                evidence_ids=[stop_evidence_id, way_evidence],
            )
        )
    report = {
        "rules": STOP_RULES,
        "stops_used_by_trips": len(rows),
        "within_10km_of_city": len(inside),
        "accepted": sum(1 for s in stops if s.status == "accepted"),
        "far_from_walk_network": sum(1 for s in stops if s.status != "accepted"),
        "new_access_nodes": len(new_nodes),
    }
    return stops, list(new_nodes.values()), list(new_evidence.values()), report


def _connector_checks(connectors: list[WalkConnector]) -> list[dict[str, Any]]:
    reasons = Counter(r for c in connectors for r in c.reasons)
    by_kind = Counter((c.entity_kind, c.traversable is True) for c in connectors)
    return [
        {
            "check_id": "walk_connectors_checked",
            "status": "pass",
            "severity": "fail",
            "details": {
                "rules": CONNECTOR_RULES,
                "connectors": len(connectors),
                "traversable": sum(1 for c in connectors if c.traversable),
                "points_traversable": by_kind[("point", True)],
                "points_unknown": by_kind[("point", False)],
                "destinations_traversable": by_kind[("destination", True)],
                "destinations_unknown": by_kind[("destination", False)],
            },
        },
        {
            "check_id": "walk_connectors_unknown",
            "status": "pass" if not reasons else "warn",
            "severity": "warn",
            "details": dict(sorted(reasons.items())),
        },
    ]


def derive(  # noqa: C901
    base_id: str,
    new_id: str,
    timetable: dict[str, Any] | None = None,
) -> Path:
    """Publish ``new_id`` = ``base_id`` + walk connectors (+ timetable stops)."""

    data = load_bundle(base_id)
    city_id = next(t.territory_id for t in data.territories if t.level == "city")
    config = CITIES[city_id]
    osm_manifest = load_dataset_manifest(config.osm_dataset_id)
    pbf = raw_dir() / osm_manifest.raw_path
    sha = file_sha256(pbf)
    if sha != osm_manifest.checksum.value:
        raise ValueError(f"{osm_manifest.dataset_id}: raw sha256 differs from the manifest")
    boundary_ids = {config.osm_boundary_relation, *config.osm_district_relations}
    osm_dir = ensure_extract(
        pbf,
        boundary_ids,
        processed_dir() / city_id / "osm",
        config.extract_bbox,
        config.poi_bbox,
        source_dataset_id=osm_manifest.dataset_id,
        expected_sha256=sha,
    )
    crs = f"EPSG:{config.utm_epsg}"
    osm_evidence = "osm:dataset:" + osm_manifest.dataset_id
    if osm_evidence not in {e.evidence_id for e in data.evidence}:
        raise ValueError(f"{base_id}: no evidence {osm_evidence}")
    layers = obstacle_layers(osm_dir, pbf, config.extract_bbox, crs)
    barriers = barrier_layer(pbf, config.extract_bbox, crs)
    connectors = build_connectors(data, layers, crs, osm_evidence, barriers)
    checks = _connector_checks(connectors)
    collections: dict[str, Any] = {name: getattr(data, name) for name in BUNDLE_COLLECTIONS}
    for name in ("freight_nodes", "freight_sites", "stop_frequencies"):
        if getattr(data, name):
            collections[name] = getattr(data, name)
    collections["walk_connectors"] = connectors
    manifest_update: dict[str, Any] = {}
    limitations = [
        line for line in data.manifest.limitations if not line.startswith("Связи точек и объектов")
    ]
    limitations.append(
        "Связи точек и объектов с пешеходной сетью проверены геометрией "
        f"({CONNECTOR_RULES}); пересечение здания, воды, железной дороги или магистрали "
        "оставляет связь unknown; секунды — по скорости принятого пешеходного профиля"
    )
    coverage = list(data.manifest.coverage)
    sources = list(data.manifest.source_versions)
    if timetable is not None:
        network = load_walk_network(osm_dir, config.utm_epsg, config.osm_dataset_id)
        if network.graph_version != data.manifest.graph_version:
            raise ValueError("walk network differs from the bundle's graph version")
        gtfs_zip = data_dir() / timetable["path"]
        if file_sha256(gtfs_zip) != timetable["sha256"]:
            raise ValueError(f"{gtfs_zip}: sha256 differs from the timetable record")
        stop_evidence = Evidence(
            evidence_id=f"gtfs:{timetable['timetable_id']}",
            source_id=timetable["source_id"],
            dataset_id=timetable["stops_dataset_id"],
            record_id=timetable["path"],
            url=timetable["url"],
            fetched_at=timetable["retrieved_at"],
            content_hash=timetable["sha256"],
            verification_state="verified",
        )
        stops, new_nodes, new_evidence, stop_report = build_transit_stops(
            data,
            gtfs_zip,
            timetable["timetable_id"],
            network,
            crs,
            stop_evidence.evidence_id,
            osm_manifest.dataset_id,
            osm_manifest.retrieved_at,
        )
        collections["transit_stops"] = stops
        collections["access_nodes"] = sorted(
            [*data.access_nodes, *new_nodes], key=lambda n: n.access_node_id
        )
        collections["evidence"] = sorted(
            [*data.evidence, stop_evidence, *new_evidence], key=lambda e: e.evidence_id
        )
        known = {s.dataset_id for s in sources}
        for dataset_id in timetable["dataset_ids"]:
            if dataset_id in known:
                continue
            m = load_dataset_manifest(dataset_id)
            sources.append(
                SourceVersion(
                    source_id=m.source_id,
                    dataset_id=m.dataset_id,
                    retrieved_at=m.retrieved_at,
                    checksum=m.checksum,
                    license=m.license,
                    publication_date=m.publication_date,
                )
            )
        ref = TimetableRef(
            timetable_id=timetable["timetable_id"],
            format="gtfs",
            path=timetable["path"],
            sha256=timetable["sha256"],
            dataset_ids=timetable["dataset_ids"],
            valid_from=timetable["valid_from"],
            valid_to=timetable["valid_to"],
            unknown_dates=timetable["unknown_dates"],
            modes=timetable["modes"],
            qa_report=timetable["qa_report"],
            limitations=timetable["limitations"],
        )
        manifest_update["timetables"] = [ref]
        for collection in ("stops", "trips"):
            coverage.append(
                CoverageRecord(
                    collection=collection,
                    city_id=city_id,
                    mode="public_transport",
                    valid_from=ref.valid_from,
                    valid_to=ref.valid_to,
                    status="partial",
                    reason=timetable["coverage_reason"],
                    evidence_ids=[stop_evidence.evidence_id],
                )
            )
        limitations = [line for line in limitations if not line.lower().startswith("расписани")]
        limitations.append(timetable["limitation"])
        checks.append(
            {
                "check_id": "transit_stops_joined",
                "status": "pass" if stop_report["far_from_walk_network"] == 0 else "warn",
                "severity": "warn",
                "details": stop_report,
            }
        )
    now = datetime.now(UTC)
    manifest = data.manifest.model_copy(
        update={
            "bundle_id": new_id,
            "created_at": now,
            "accepted_at": now,
            "coverage": coverage,
            "limitations": limitations,
            "source_versions": sources,
            "files": {},
            **manifest_update,
        }
    )
    qa = dict(data.qa_report)
    qa["bundle_id"] = new_id
    qa["derived_from"] = base_id
    qa["checks"] = [*qa.get("checks", []), *checks]
    return publish_bundle(manifest, collections, qa)


def walk_connectors(
    bundle_id: str,
    *,
    walking_speed_kmh: float,
    speed_evidence_refs: list[str],
    profile_version: str,
    root: Path | None = None,
) -> list[dict[str, Any]]:
    """ML1 ``WalkConnector`` fields for every connector of a bundle.

    The speed must be the one of the accepted pedestrian profile (the Valhalla
    build's ``walking_speed``); unknown connectors keep ``seconds=None``.
    """

    if not 0.5 <= walking_speed_kmh <= 25 or not speed_evidence_refs:
        raise ValueError("an accepted walking speed with evidence is required")
    data = load_bundle(bundle_id, root=root)
    nodes = {n.access_node_id: n for n in data.access_nodes}
    places = {
        **{("point", p.point_id): (p.lon, p.lat) for p in data.sample_points},
        **{("destination", d.destination_id): (d.lon, d.lat) for d in data.destinations},
    }
    rows = []
    for c in data.walk_connectors:
        lon, lat = places[(c.entity_kind, c.entity_id)]
        node = nodes[c.access_node_id]
        rows.append(
            {
                "snapshot_id": bundle_id,
                "source_graph_version": data.manifest.graph_version,
                "physical_node_id": c.physical_node_id,
                "access_node_id": c.access_node_id,
                "physical_location": {"lon": lon, "lat": lat},
                "access_location": {"lon": node.lon, "lat": node.lat},
                "seconds": round(c.length_m / (walking_speed_kmh / 3.6), 1)
                if c.traversable
                else None,
                "traversable": c.traversable,
                "time_basis": f"{CONNECTOR_RULES}:straight_line_length/{profile_version}",
                "evidence_refs": [*c.evidence_ids, *speed_evidence_refs],
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_id")
    parser.add_argument("new_id")
    parser.add_argument("--timetable", type=Path, help="JSON describing a GTFS timetable")
    args = parser.parse_args()
    timetable = None
    if args.timetable:
        timetable = json.loads(args.timetable.read_text(encoding="utf-8"))
        timetable["valid_from"] = date.fromisoformat(timetable["valid_from"])
        timetable["valid_to"] = date.fromisoformat(timetable["valid_to"])
        timetable["unknown_dates"] = [date.fromisoformat(d) for d in timetable["unknown_dates"]]
        timetable["retrieved_at"] = datetime.fromisoformat(timetable["retrieved_at"])
    print(derive(args.base_id, args.new_id, timetable))


__all__ = [
    "barrier_layer",
    "build_connectors",
    "build_transit_stops",
    "derive",
    "gate_access",
    "railway_lines",
    "walk_connectors",
]

if __name__ == "__main__":
    main()
