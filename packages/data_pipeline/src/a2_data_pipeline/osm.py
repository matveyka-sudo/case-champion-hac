"""Extract boundaries, buildings, water, destination candidates and the walk network
from an OSM PBF.

Requires ``osmium`` (not yet in the locked manifest; see README). Output: Parquet
tables with WKB geometry in EPSG:4326 under data/processed/<city>/osm/ and
``extract_meta.json``. Every row carries ``content_hash``: sha256 of the extracted
record (reference, kept tags and geometry), used as evidence hash.

The extract is a cache bound to its input: the meta records the sha256 of the PBF
it was read from, the source dataset id, the walk and road rule versions and the
table list. ``extract_is_current`` reuses it only when the expected PBF sha256,
the rules, the boundaries, the boxes and every table file match.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import osmium
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

EXTRACT_VERSION = 9

BBox = tuple[float, float, float, float]  # west, south, east, north (EPSG:4326)
WALK_RULES_VERSION = "osm-walk-network-v1"
ROAD_RULES_VERSION = "osm-road-network-v1"

WATER_TAGS = {
    ("natural", "water"),
    ("waterway", "riverbank"),
    ("landuse", "reservoir"),
    ("landuse", "basin"),
}
POI_KEYS = ("amenity", "healthcare", "shop", "railway", "public_transport", "aeroway", "building")
POI_VALUES = {
    "amenity": {
        "clinic",
        "doctors",
        "hospital",
        "kindergarten",
        "school",
        "college",
        "university",
        "bus_station",
    },
    "healthcare": {"clinic", "centre", "hospital"},
    "shop": {"mall"},
    "railway": {"station"},
    "aeroway": {"aerodrome", "terminal"},
    "building": {"train_station"},
}
KEEP_TAGS = (
    "name",
    "official_name",
    "short_name",
    "operator",
    "operator:type",
    "ownership",
    "amenity",
    "healthcare",
    "healthcare:speciality",
    "shop",
    "railway",
    "station",
    "train",
    "subway",
    "public_transport",
    "aeroway",
    "aerodrome:type",
    "iata",
    "icao",
    "building",
    "isced:level",
    "school",
    "ref:inn",
    "ref:ogrn",
    "addr:street",
    "addr:housenumber",
    "website",
    "disused",
)
# Walk network v1: ways a pedestrian may use. Motorways never; trunk roads only
# with explicit foot access or a sidewalk; foot=use_sidepath defers to the
# separately mapped sidewalk.
WALK_HIGHWAYS = {
    "footway",
    "path",
    "pedestrian",
    "living_street",
    "residential",
    "service",
    "unclassified",
    "tertiary",
    "tertiary_link",
    "secondary",
    "secondary_link",
    "primary",
    "primary_link",
    "steps",
    "track",
    "cycleway",
    "road",
    "bridleway",
}
TRUNK_HIGHWAYS = {"trunk", "trunk_link"}
FOOT_ALLOWED = {"yes", "designated", "permissive"}
FOOT_DENIED = {"no", "private", "use_sidepath"}
SIDEWALK_PRESENT = {"both", "left", "right", "yes"}
WALK_TAGS = ("highway", "foot", "access", "sidewalk", "area")
# Road network v1 for freight access: ways a lorry may use. No footways or tracks.
# The most specific access tag decides (hgv > motor_vehicle > vehicle > access):
# hgv=no closes a street with motor_vehicle=yes, hgv=yes opens one with access=no.
DRIVE_HIGHWAYS = {
    "motorway",
    "motorway_link",
    "trunk",
    "trunk_link",
    "primary",
    "primary_link",
    "secondary",
    "secondary_link",
    "tertiary",
    "tertiary_link",
    "unclassified",
    "residential",
    "living_street",
    "service",
    "road",
}
LORRY_DENIED = {"no", "private", "agricultural", "forestry"}
LORRY_ACCESS_KEYS = ("hgv", "motor_vehicle", "vehicle", "access")
ROAD_TAGS = ("highway", "access", "hgv", "motor_vehicle", "vehicle", "maxweight", "area")
FREIGHT_VALUES = {
    "landuse": {"industrial", "port"},
    "building": {"warehouse", "industrial"},
    "railway": {"yard"},
    "man_made": {"works", "storage_tank", "pier"},
}
FREIGHT_ANY = ("industrial", "harbour", "port")
# named public transport stops (nodes): timetable stops are located by these names
STOP_TAGS = (
    "name",
    "highway",
    "public_transport",
    "railway",
    "bus",
    "trolleybus",
    "tram",
    "route_ref",
)


def is_stop(tags: Any) -> bool:
    return bool(tags.get("name")) and (
        tags.get("highway") == "bus_stop"
        or tags.get("railway") == "tram_stop"
        or tags.get("public_transport") in {"platform", "stop_position"}
    )


FREIGHT_TAGS = (
    "name",
    "official_name",
    "operator",
    "owner",
    "landuse",
    "industrial",
    "building",
    "railway",
    "man_made",
    "product",
    "content",
    "substance",
    "harbour",
    "port",
    "seamark:type",
    "service",
    "usage",
    "disused",
)


def drivable(tags: Any) -> bool:
    highway = tags.get("highway")
    if highway not in DRIVE_HIGHWAYS or tags.get("area") == "yes":
        return False
    for key in LORRY_ACCESS_KEYS:
        value = tags.get(key)
        if value is not None:
            return value not in LORRY_DENIED
    return True


def is_freight(tags: Any) -> bool:
    if any(tags.get(key) in values for key, values in FREIGHT_VALUES.items()):
        return True
    return any(key in tags for key in FREIGHT_ANY)


def walkable(tags: Any) -> bool:
    highway = tags.get("highway")
    foot = tags.get("foot")
    if highway is None or foot in FOOT_DENIED:
        return False
    if tags.get("access") in {"no", "private"} and foot not in FOOT_ALLOWED:
        return False
    if highway in WALK_HIGHWAYS:
        return True
    if highway in TRUNK_HIGHWAYS:
        return foot in FOOT_ALLOWED or tags.get("sidewalk") in SIDEWALK_PRESENT
    return False


def _ref(kind: str, osm_id: int) -> str:
    return f"{kind}/{osm_id}"


def _is_poi(tags: Any) -> bool:
    return any(tags.get(key) in POI_VALUES.get(key, ()) for key in POI_KEYS)


def content_hash(ref: str, tags: dict[str, Any], wkb: bytes, extra: Any = None) -> str:
    payload = json.dumps(
        {"ref": ref, "tags": tags, "wkb": wkb.hex(), "extra": extra},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class _Tables:
    boundaries: list[dict[str, Any]] = field(default_factory=list)
    buildings: list[dict[str, Any]] = field(default_factory=list)
    water: list[dict[str, Any]] = field(default_factory=list)
    pois: list[dict[str, Any]] = field(default_factory=list)
    walk_ways: list[dict[str, Any]] = field(default_factory=list)
    addresses: list[dict[str, Any]] = field(default_factory=list)
    road_ways: list[dict[str, Any]] = field(default_factory=list)
    freight: list[dict[str, Any]] = field(default_factory=list)
    stops: list[dict[str, Any]] = field(default_factory=list)
    broken_areas: int = 0
    broken_ways: int = 0


def _inside(lon: float, lat: float, bbox: BBox | None) -> bool:
    return bbox is None or (bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3])


class _Handler(osmium.SimpleHandler):
    """``bbox`` clips buildings, water, addresses and the walk network; ``poi_bbox``
    clips destination candidates. Without a box the whole file is kept."""

    def __init__(self, boundary_ids: set[int], bbox: BBox | None, poi_bbox: BBox | None) -> None:
        super().__init__()
        self.boundary_ids = boundary_ids
        self.bbox = bbox
        self.poi_bbox = poi_bbox or bbox
        self.wkb = osmium.geom.WKBFactory()
        self.tables = _Tables()

    @staticmethod
    def _kept(tags: Any, keys: tuple[str, ...]) -> dict[str, Any]:
        return {key: tags.get(key) for key in keys if key in tags}

    def _address(self, ref: str, tags: Any, wkb: bytes, area: float | None) -> None:
        street, house = tags.get("addr:street"), tags.get("addr:housenumber")
        if street and house:
            self.tables.addresses.append(
                {
                    "osm_ref": ref,
                    "street": street,
                    "housenumber": house,
                    "city": tags.get("addr:city"),
                    "wkb": wkb,
                    "is_building": area is not None,
                    "content_hash": content_hash(
                        ref, {"addr:street": street, "addr:housenumber": house}, wkb
                    ),
                }
            )

    def node(self, node: Any) -> None:
        tags = node.tags
        if not len(tags) or not node.location.valid():
            return
        lon, lat = node.location.lon, node.location.lat
        if "addr:housenumber" in tags and _inside(lon, lat, self.bbox):
            self._address(
                _ref("node", node.id), node.tags, bytes.fromhex(self.wkb.create_point(node)), None
            )
        if is_stop(tags) and _inside(lon, lat, self.bbox):
            ref = _ref("node", node.id)
            kept = self._kept(tags, STOP_TAGS)
            wkb = bytes.fromhex(self.wkb.create_point(node))
            self.tables.stops.append(
                {
                    "osm_ref": ref,
                    "tags": json.dumps(kept, ensure_ascii=False),
                    "wkb": wkb,
                    "content_hash": content_hash(ref, kept, wkb),
                }
            )
        if is_freight(tags) and _inside(lon, lat, self.bbox):
            ref = _ref("node", node.id)
            kept = self._kept(tags, FREIGHT_TAGS)
            wkb = bytes.fromhex(self.wkb.create_point(node))
            self.tables.freight.append(
                {
                    "osm_ref": ref,
                    "tags": json.dumps(kept, ensure_ascii=False),
                    "wkb": wkb,
                    "content_hash": content_hash(ref, kept, wkb),
                }
            )
        if _is_poi(tags) and _inside(lon, lat, self.poi_bbox):
            ref = _ref("node", node.id)
            tags = self._kept(node.tags, KEEP_TAGS)
            wkb = bytes.fromhex(self.wkb.create_point(node))
            self.tables.pois.append(
                {
                    "osm_ref": ref,
                    "tags": json.dumps(tags, ensure_ascii=False),
                    "wkb": wkb,
                    "content_hash": content_hash(ref, tags, wkb),
                }
            )

    def way(self, way: Any) -> None:
        walk, road = walkable(way.tags), drivable(way.tags)
        if not (walk or road):
            return
        first = way.nodes[0].location if len(way.nodes) else None
        if first is None or not first.valid() or not _inside(first.lon, first.lat, self.bbox):
            return
        if road:
            self._network_way(way, ROAD_TAGS, self.tables.road_ways)
        if walk:
            self._network_way(way, WALK_TAGS, self.tables.walk_ways)

    def _network_way(self, way: Any, keys: tuple[str, ...], table: list[dict[str, Any]]) -> None:
        ref = _ref("way", way.id)
        try:
            wkb = bytes.fromhex(self.wkb.create_linestring(way))
        except Exception:  # noqa: BLE001 - osmium raises several geometry error types
            self.tables.broken_ways += 1
            return
        tags = self._kept(way.tags, keys)
        nodes = [int(node.ref) for node in way.nodes]
        table.append(
            {
                "osm_ref": ref,
                "highway": tags["highway"],
                "node_refs": nodes,
                "wkb": wkb,
                "content_hash": content_hash(ref, tags, wkb, nodes),
            }
        )

    def _area_position(self, area: Any) -> tuple[bool, bool]:
        """(inside bbox, inside poi bbox) by the first node of the first outer ring."""

        for ring in area.outer_rings():
            for node in ring:
                lon, lat = node.lon, node.lat
                return _inside(lon, lat, self.bbox), _inside(lon, lat, self.poi_bbox)
        return False, False

    def area(self, area: Any) -> None:
        tags = area.tags
        kind = "way" if area.from_way() else "relation"
        ref = _ref(kind, area.orig_id())
        is_boundary = kind == "relation" and area.orig_id() in self.boundary_ids
        if self.bbox is None:
            detail, poi = True, True
        else:
            detail, poi = self._area_position(area)
        if not (detail or poi or is_boundary):
            return
        try:
            geometry: bytes | None = None

            def geom() -> bytes:
                nonlocal geometry
                if geometry is None:
                    geometry = bytes.fromhex(self.wkb.create_multipolygon(area))
                return geometry

            if kind == "relation" and area.orig_id() in self.boundary_ids:
                self.tables.boundaries.append(
                    {
                        "osm_ref": ref,
                        "name": tags.get("name"),
                        "admin_level": tags.get("admin_level"),
                        "wikidata": tags.get("wikidata"),
                        "population_tag": tags.get("population"),
                        "wkb": geom(),
                        "content_hash": content_hash(ref, {"name": tags.get("name")}, geom()),
                    }
                )
            building = tags.get("building") if detail else None
            if building and building != "no" and "addr:housenumber" in tags:
                self._address(ref, tags, geom(), 0.0)
            if building and building != "no":
                self.tables.buildings.append(
                    {
                        "osm_ref": ref,
                        "building": building,
                        "wkb": geom(),
                        "content_hash": content_hash(ref, {"building": building}, geom()),
                    }
                )
            if detail and any(tags.get(key) == value for key, value in WATER_TAGS):
                self.tables.water.append({"osm_ref": ref, "wkb": geom()})
            if detail and is_freight(tags):
                kept = self._kept(tags, FREIGHT_TAGS)
                self.tables.freight.append(
                    {
                        "osm_ref": ref,
                        "tags": json.dumps(kept, ensure_ascii=False),
                        "wkb": geom(),
                        "content_hash": content_hash(ref, kept, geom()),
                    }
                )
            if poi and _is_poi(tags):
                kept = self._kept(tags, KEEP_TAGS)
                self.tables.pois.append(
                    {
                        "osm_ref": ref,
                        "tags": json.dumps(kept, ensure_ascii=False),
                        "wkb": geom(),
                        "content_hash": content_hash(ref, kept, geom()),
                    }
                )
        except RuntimeError:
            self.tables.broken_areas += 1


TABLES = (
    "boundaries",
    "buildings",
    "water",
    "pois",
    "walk_ways",
    "addresses",
    "road_ways",
    "freight",
    "stops",
)


def extract(
    pbf: Path,
    boundary_ids: set[int],
    out_dir: Path,
    bbox: BBox | None = None,
    poi_bbox: BBox | None = None,
    *,
    source_dataset_id: str,
    expected_sha256: str | None = None,
) -> dict[str, int]:
    source_sha256 = file_sha256(pbf)
    if expected_sha256 is not None and source_sha256 != expected_sha256:
        raise ValueError(f"{pbf}: sha256 {source_sha256} differs from expected {expected_sha256}")
    handler = _Handler(boundary_ids, bbox, poi_bbox)
    handler.apply_file(str(pbf), locations=True, idx="flex_mem")
    out_dir.mkdir(parents=True, exist_ok=True)
    meta_path = out_dir / "extract_meta.json"
    # invalidate first: a failed run must not leave old meta over new tables
    meta_path.unlink(missing_ok=True)
    counts: dict[str, int] = {}
    for name in TABLES:
        rows = getattr(handler.tables, name)
        pq.write_table(pa.Table.from_pylist(rows), out_dir / f"{name}.parquet")
        counts[name] = len(rows)
    missing = boundary_ids - {
        int(row["osm_ref"].split("/")[1]) for row in handler.tables.boundaries
    }
    if missing:
        raise RuntimeError(f"boundary relations not assembled: {sorted(missing)}")
    counts["broken_areas"] = handler.tables.broken_areas
    counts["broken_ways"] = handler.tables.broken_ways
    meta = {
        "extract_version": EXTRACT_VERSION,
        "source": {
            "dataset_id": source_dataset_id,
            "sha256": source_sha256,
            "size_bytes": pbf.stat().st_size,
        },
        "walk_rules": WALK_RULES_VERSION,
        "road_rules": ROAD_RULES_VERSION,
        "boundary_ids": sorted(boundary_ids),
        "bbox": list(bbox) if bbox else None,
        "poi_bbox": list(poi_bbox) if poi_bbox else None,
        "tables": list(TABLES),
        "counts": counts,
    }
    staging = out_dir / "extract_meta.json.tmp"
    staging.write_text(json.dumps(meta, indent=2) + "\n")
    staging.replace(meta_path)
    return counts


def file_sha256(path: Path) -> str:
    """sha256 of a file read in 1 MiB chunks (a city PBF is hundreds of MB)."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_is_current(
    out_dir: Path,
    boundary_ids: set[int],
    bbox: BBox | None = None,
    poi_bbox: BBox | None = None,
    *,
    source_sha256: str,
) -> bool:
    """True only if the cached extract was read from this PBF with the current rules.

    ``source_sha256`` is the expected hash of the input PBF (the dataset manifest's
    checksum, checked against the file by the caller).
    """

    path = out_dir / "extract_meta.json"
    if not path.is_file():
        return False
    meta = json.loads(path.read_text())
    source = meta.get("source") or {}
    return bool(
        meta.get("extract_version") == EXTRACT_VERSION
        and source.get("sha256") == source_sha256
        and meta.get("walk_rules") == WALK_RULES_VERSION
        and meta.get("road_rules") == ROAD_RULES_VERSION
        and meta.get("boundary_ids") == sorted(boundary_ids)
        and meta.get("bbox") == (list(bbox) if bbox else None)
        and meta.get("poi_bbox") == (list(poi_bbox) if poi_bbox else None)
        and meta.get("tables") == list(TABLES)
        and all((out_dir / f"{name}.parquet").is_file() for name in TABLES)
    )
