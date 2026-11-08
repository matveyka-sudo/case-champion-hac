"""Extract boundaries, buildings, water, destination candidates and the walk network
from an OSM PBF.

Requires ``osmium`` (not yet in the locked manifest; see README). Output: Parquet
tables with WKB geometry in EPSG:4326 under data/processed/<city>/osm/ and
``extract_meta.json``. Every row carries ``content_hash``: sha256 of the extracted
record (reference, kept tags and geometry), used as evidence hash.
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

EXTRACT_VERSION = 3
WALK_RULES_VERSION = "osm-walk-network-v1"

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
    broken_areas: int = 0
    broken_ways: int = 0


class _Handler(osmium.SimpleHandler):
    def __init__(self, boundary_ids: set[int]) -> None:
        super().__init__()
        self.boundary_ids = boundary_ids
        self.wkb = osmium.geom.WKBFactory()
        self.tables = _Tables()

    @staticmethod
    def _kept(tags: Any, keys: tuple[str, ...]) -> dict[str, Any]:
        return {key: tags.get(key) for key in keys if key in tags}

    def node(self, node: Any) -> None:
        if _is_poi(node.tags):
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
        if not walkable(way.tags):
            return
        ref = _ref("way", way.id)
        try:
            wkb = bytes.fromhex(self.wkb.create_linestring(way))
        except Exception:  # noqa: BLE001 - osmium raises several geometry error types
            self.tables.broken_ways += 1
            return
        tags = self._kept(way.tags, WALK_TAGS)
        nodes = [int(node.ref) for node in way.nodes]
        self.tables.walk_ways.append(
            {
                "osm_ref": ref,
                "highway": tags["highway"],
                "node_refs": nodes,
                "wkb": wkb,
                "content_hash": content_hash(ref, tags, wkb, nodes),
            }
        )

    def area(self, area: Any) -> None:
        tags = area.tags
        kind = "way" if area.from_way() else "relation"
        ref = _ref(kind, area.orig_id())
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
            building = tags.get("building")
            if building and building != "no":
                self.tables.buildings.append(
                    {
                        "osm_ref": ref,
                        "building": building,
                        "wkb": geom(),
                        "content_hash": content_hash(ref, {"building": building}, geom()),
                    }
                )
            if any(tags.get(key) == value for key, value in WATER_TAGS):
                self.tables.water.append({"osm_ref": ref, "wkb": geom()})
            if _is_poi(tags):
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


TABLES = ("boundaries", "buildings", "water", "pois", "walk_ways")


def extract(pbf: Path, boundary_ids: set[int], out_dir: Path) -> dict[str, int]:
    handler = _Handler(boundary_ids)
    handler.apply_file(str(pbf), locations=True, idx="flex_mem")
    out_dir.mkdir(parents=True, exist_ok=True)
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
        "walk_rules": WALK_RULES_VERSION,
        "boundary_ids": sorted(boundary_ids),
        "counts": counts,
    }
    (out_dir / "extract_meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    return counts


def extract_is_current(out_dir: Path, boundary_ids: set[int]) -> bool:
    path = out_dir / "extract_meta.json"
    if not path.is_file():
        return False
    meta = json.loads(path.read_text())
    return bool(
        meta.get("extract_version") == EXTRACT_VERSION
        and meta.get("boundary_ids") == sorted(boundary_ids)
    )
