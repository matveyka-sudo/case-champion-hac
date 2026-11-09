"""National OSM pass over the Geofabrik ``russia`` PBF (rule set ``osm-national-v1``).

A country does not fit a full node-location index in memory, so the PBF is read in
filtered passes: relations, then the ways they use and the tagged ways, then only
the nodes those ways need and the tagged nodes. Output, as Parquet with WKB in
EPSG:4326 under ``data/processed/<dataset_id>/national/``:

- ``city_boundaries``: a relation or closed way whose own ``wikidata`` tag is the
  Wikidata id of a directory city and that outlines it (``place=city|town``, most
  often a multipolygon, or a boundary relation); the link is the tag, never the
  name; a city with several outlines is the union of them; a city without one
  takes the boundary of its urban settlement (``admin_level=8``) or urban okrug
  (``admin_level=6`` named or officially «городской округ»), whose
  ``admin_centre`` member is a node with the city's ``wikidata`` tag — basis
  ``admin_centre``, it may hold the okrug's villages; a municipal district is
  never a city outline;
- ``bus_stations``: ``amenity=bus_station`` nodes and ways (a way gives its
  polygon's representative point);
- ``port_areas``: ``landuse=harbour|port``, ``industrial=port`` and
  ``man_made=container_terminal`` ways and multipolygons, and industrial areas
  with the word «порт» in the name («аэропорт» is not one) — the urban rule of
  ``freight``.

``extract_meta.json`` binds the output to the PBF sha256, the rules and the
table list, like ``osm.extract``; ``national_is_current`` reuses it only when all
match. Rings are assembled from member ways with ``polygonize``; a relation whose
rings do not close is reported, not repaired.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .osm import file_sha256

RULES_VERSION = "osm-national-v1"
TABLES = ("city_boundaries", "bus_stations", "port_areas")
PORT_TAGS = (
    ("landuse", "harbour"),
    ("landuse", "port"),
    ("industrial", "port"),
    ("man_made", "container_terminal"),
)
PORT_NAME = re.compile(r"(?<![а-яё])порт(?![а-яё])", re.I)  # same as freight.PORT_NAME
CENTRE_ROLES = {"admin_centre", "label"}
BUS_TAGS = (("amenity", "bus_station"),)
BOUNDARY_VALUES = {"administrative", "place"}
PLACE_TAGS = (("place", "city"), ("place", "town"))


def _outlines_city(tags: dict[str, str]) -> bool:
    return (
        tags.get("place") in {"city", "town"}
        or tags.get("type") == "boundary"
        or tags.get("boundary") in BOUNDARY_VALUES
    )


def _is_port(tags: dict[str, str]) -> bool:
    if any(tags.get(k) == v for k, v in PORT_TAGS):
        return True
    return tags.get("landuse") == "industrial" and bool(PORT_NAME.search(tags.get("name", "")))


def _urban_municipality(tags: dict[str, str]) -> int | None:
    """Admin level of an urban settlement or urban okrug boundary, else None."""

    if tags.get("boundary") != "administrative":
        return None
    level = tags.get("admin_level")
    status = f"{tags.get('official_status', '')} {tags.get('name', '')}".lower()
    if level == "8":
        return 8
    if level == "6" and "городской округ" in status:
        return 6
    return None


def _tags(entity: Any) -> dict[str, str]:
    return {t.k: t.v for t in entity.tags}


def _entities(path: str, kind: Any, entity_filter: Any) -> Iterator[Any]:
    import osmium

    yield from osmium.FileProcessor(path, kind).with_filter(entity_filter)


def _polygon(outer: list[list[tuple[float, float]]], inner: list[list[tuple[float, float]]]) -> Any:
    """A (multi)polygon from member way coordinates, or None if the rings do not close."""

    from shapely.geometry import LineString  # type: ignore[import-untyped]
    from shapely.ops import polygonize, unary_union  # type: ignore[import-untyped]

    def area(lines: list[list[tuple[float, float]]]) -> Any:
        parts = [LineString(c) for c in lines if len(c) >= 2]
        polygons = list(polygonize(unary_union(parts))) if parts else []
        return unary_union(polygons) if polygons else None

    shell = area(outer)
    if shell is None or shell.is_empty:
        return None
    holes = area(inner)
    return shell.difference(holes) if holes is not None else shell


def extract_national(
    pbf: Path,
    city_qids: set[str],
    out_dir: Path,
    *,
    source_dataset_id: str,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    import osmium
    import pyarrow as pa  # type: ignore[import-untyped]
    import pyarrow.parquet as pq  # type: ignore[import-untyped]
    from shapely import wkb as shapely_wkb  # type: ignore[import-untyped]
    from shapely.geometry import Polygon

    source_sha256 = file_sha256(pbf)
    if expected_sha256 is not None and source_sha256 != expected_sha256:
        raise ValueError(f"{pbf}: sha256 {source_sha256} differs from expected {expected_sha256}")
    path = str(pbf)

    # pass 1: relations
    boundary_rel: dict[int, dict[str, Any]] = {}
    port_rel: dict[int, dict[str, Any]] = {}
    municipal_rel: dict[int, dict[str, Any]] = {}
    for rel in _entities(
        path,
        osmium.osm.RELATION,
        osmium.filter.KeyFilter("wikidata", "landuse", "industrial", "man_made", "boundary"),
    ):
        tags = _tags(rel)
        members = [(m.ref, m.role) for m in rel.members if m.type == "w"]
        if tags.get("wikidata") in city_qids and _outlines_city(tags):
            boundary_rel[rel.id] = {"tags": tags, "members": members, "basis": "wikidata_tag"}
        elif tags.get("type") == "multipolygon" and _is_port(tags):
            port_rel[rel.id] = {"tags": tags, "members": members}
        else:
            level = _urban_municipality(tags)
            centres = [m.ref for m in rel.members if m.type == "n" and m.role in CENTRE_ROLES]
            if level is not None and centres:
                municipal_rel[rel.id] = {
                    "tags": tags,
                    "members": members,
                    "level": level,
                    "centres": centres,
                }

    # pass 1b: the centre nodes of urban municipalities; a city without its own outline
    # takes the most local municipality centred on it (ties are reported, not chosen)
    centre_qid: dict[int, str] = {}
    centre_ids = {n for r in municipal_rel.values() for n in r["centres"]}
    if centre_ids:
        for node in _entities(path, osmium.osm.NODE, osmium.filter.IdFilter(centre_ids)):
            qid = _tags(node).get("wikidata")
            if qid in city_qids:
                centre_qid[node.id] = qid
    outlined = {r["tags"]["wikidata"] for r in boundary_rel.values()}
    by_city: dict[str, list[tuple[int, int]]] = {}
    for rel_id, record in municipal_rel.items():
        for qid in {centre_qid[n] for n in record["centres"] if n in centre_qid}:
            if qid not in outlined:
                by_city.setdefault(qid, []).append((record["level"], rel_id))
    centre_ties: list[str] = []
    for qid, options in sorted(by_city.items()):
        best = max(level for level, _ in options)
        chosen = sorted(rel_id for level, rel_id in options if level == best)
        if len(chosen) > 1:
            centre_ties.append(qid)
            continue
        record = municipal_rel[chosen[0]]
        boundary_rel[chosen[0]] = {
            "tags": {**record["tags"], "wikidata": qid},
            "members": record["members"],
            "basis": "admin_centre",
        }

    # pass 2: tagged ways; pass 3: member ways of the kept relations
    tagged_ways: dict[int, dict[str, Any]] = {}
    for way in _entities(
        path,
        osmium.osm.WAY,
        osmium.filter.TagFilter(*BUS_TAGS, *PORT_TAGS, *PLACE_TAGS, ("landuse", "industrial")),
    ):
        tags = _tags(way)
        if not (_is_port(tags) or tags.get("amenity") == "bus_station"):
            if "place" not in tags or tags.get("wikidata") not in city_qids:
                continue
        tagged_ways[way.id] = {"tags": tags, "nodes": [n.ref for n in way.nodes]}
    member_ids = {m for r in (*boundary_rel.values(), *port_rel.values()) for m, _ in r["members"]}
    member_ways: dict[int, list[int]] = {}
    if member_ids:
        for way in _entities(path, osmium.osm.WAY, osmium.filter.IdFilter(member_ids)):
            member_ways[way.id] = [n.ref for n in way.nodes]

    # pass 4: the nodes those ways need; pass 5: tagged nodes
    node_ids = {n for w in tagged_ways.values() for n in w["nodes"]}
    node_ids |= {n for nodes in member_ways.values() for n in nodes}
    location: dict[int, tuple[float, float]] = {}
    if node_ids:
        for node in _entities(path, osmium.osm.NODE, osmium.filter.IdFilter(node_ids)):
            location[node.id] = (node.location.lon, node.location.lat)
    bus_nodes = [
        (node.id, _tags(node), (node.location.lon, node.location.lat))
        for node in _entities(path, osmium.osm.NODE, osmium.filter.TagFilter(*BUS_TAGS))
    ]

    def coords(nodes: list[int]) -> list[tuple[float, float]] | None:
        points = [location.get(n) for n in nodes]
        return None if any(p is None for p in points) else points  # type: ignore[return-value]

    report: dict[str, Any] = {
        "rules": RULES_VERSION,
        "unclosed": [],
        "admin_centre_ties": centre_ties,
    }

    def relation_polygon(rel_id: int, record: dict[str, Any]) -> Any:
        outer: list[list[tuple[float, float]]] = []
        inner: list[list[tuple[float, float]]] = []
        for way_id, role in record["members"]:
            line = coords(member_ways.get(way_id, []))
            if line:
                (inner if role == "inner" else outer).append(line)
        polygon = _polygon(outer, inner)
        if polygon is None:
            report["unclosed"].append(f"relation/{rel_id}")
        return polygon

    boundaries = []
    for rel_id in sorted(boundary_rel):
        found = boundary_rel[rel_id]
        polygon = relation_polygon(rel_id, found)
        if polygon is None:
            continue
        boundaries.append(
            {
                "osm_ref": f"relation/{rel_id}",
                "city_wikidata": found["tags"]["wikidata"],
                "name": found["tags"].get("name"),
                "admin_level": found["tags"].get("admin_level"),
                "boundary": found["tags"].get("boundary") or found["tags"].get("place"),
                "basis": found["basis"],
                "wkb": shapely_wkb.dumps(polygon),
            }
        )

    stations = [
        {
            "osm_ref": f"node/{node_id}",
            "name": tags.get("name"),
            "wikidata": tags.get("wikidata"),
            "lon": round(lon, 7),
            "lat": round(lat, 7),
        }
        for node_id, tags, (lon, lat) in sorted(bus_nodes)
    ]
    ports = []
    for way_id in sorted(tagged_ways):
        way = tagged_ways[way_id]
        line = coords(way["nodes"])
        if line is None or len(line) < 4 or line[0] != line[-1]:
            continue  # an open way has no area
        polygon = Polygon(line)
        if way["tags"].get("wikidata") in city_qids and way["tags"].get("place") in {
            "city",
            "town",
        }:
            boundaries.append(
                {
                    "osm_ref": f"way/{way_id}",
                    "city_wikidata": way["tags"]["wikidata"],
                    "name": way["tags"].get("name"),
                    "admin_level": way["tags"].get("admin_level"),
                    "boundary": way["tags"].get("place"),
                    "basis": "wikidata_tag",
                    "wkb": shapely_wkb.dumps(polygon),
                }
            )
        if way["tags"].get("amenity") == "bus_station":
            point = polygon.representative_point()
            stations.append(
                {
                    "osm_ref": f"way/{way_id}",
                    "name": way["tags"].get("name"),
                    "wikidata": way["tags"].get("wikidata"),
                    "lon": round(point.x, 7),
                    "lat": round(point.y, 7),
                }
            )
        if _is_port(way["tags"]):
            ports.append(
                {
                    "osm_ref": f"way/{way_id}",
                    "name": way["tags"].get("name"),
                    "wkb": shapely_wkb.dumps(polygon),
                }
            )
    for rel_id in sorted(port_rel):
        polygon = relation_polygon(rel_id, port_rel[rel_id])
        if polygon is not None:
            ports.append(
                {
                    "osm_ref": f"relation/{rel_id}",
                    "name": port_rel[rel_id]["tags"].get("name"),
                    "wkb": shapely_wkb.dumps(polygon),
                }
            )

    out_dir.mkdir(parents=True, exist_ok=True)
    meta_path = out_dir / "extract_meta.json"
    meta_path.unlink(missing_ok=True)
    tables = {"city_boundaries": boundaries, "bus_stations": stations, "port_areas": ports}
    for name in TABLES:
        pq.write_table(pa.Table.from_pylist(tables[name]), out_dir / f"{name}.parquet")
    report["counts"] = {name: len(rows) for name, rows in tables.items()}
    report["cities_with_boundary"] = len({b["city_wikidata"] for b in boundaries})
    report["cities_by_basis"] = {
        basis: len({b["city_wikidata"] for b in boundaries if b["basis"] == basis})
        for basis in ("wikidata_tag", "admin_centre")
    }
    meta = {
        "rules": RULES_VERSION,
        "source": {
            "dataset_id": source_dataset_id,
            "sha256": source_sha256,
            "size_bytes": pbf.stat().st_size,
        },
        "city_qids_sha256": _qids_hash(city_qids),
        "tables": list(TABLES),
        "report": report,
    }
    staging = out_dir / "extract_meta.json.tmp"
    staging.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
    staging.replace(meta_path)
    return report


def _qids_hash(qids: set[str]) -> str:
    import hashlib

    return hashlib.sha256("\n".join(sorted(qids)).encode()).hexdigest()


def national_is_current(out_dir: Path, city_qids: set[str], *, source_sha256: str) -> bool:
    path = out_dir / "extract_meta.json"
    if not path.is_file():
        return False
    meta = json.loads(path.read_text())
    return bool(
        meta.get("rules") == RULES_VERSION
        and (meta.get("source") or {}).get("sha256") == source_sha256
        and meta.get("city_qids_sha256") == _qids_hash(city_qids)
        and meta.get("tables") == list(TABLES)
        and all((out_dir / f"{name}.parquet").is_file() for name in TABLES)
    )


def load_table(out_dir: Path, name: str) -> list[dict[str, Any]]:
    import pyarrow.parquet as pq

    rows: list[dict[str, Any]] = pq.read_table(out_dir / f"{name}.parquet").to_pylist()
    return rows


__all__ = ["extract_national", "load_table", "national_is_current"]


def main() -> None:
    import argparse

    from . import territories
    from .manifest import load_dataset_manifest
    from .paths import processed_dir, raw_dir

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("osm_dataset_id")
    parser.add_argument("territory_directory_id")
    args = parser.parse_args()
    manifest = load_dataset_manifest(args.osm_dataset_id)
    city_qids = {
        e.wikidata_id
        for e in territories.load_directory(args.territory_directory_id)
        if e.level == "city"
    }
    out_dir = processed_dir() / args.osm_dataset_id / "national"
    sha = manifest.checksum.value
    if national_is_current(out_dir, city_qids, source_sha256=sha):
        print(f"{out_dir}: current")
        return
    report = extract_national(
        raw_dir() / manifest.raw_path,
        city_qids,
        out_dir,
        source_dataset_id=manifest.dataset_id,
        expected_sha256=sha,
    )
    print(json.dumps({k: v for k, v in report.items() if k != "unclosed"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
