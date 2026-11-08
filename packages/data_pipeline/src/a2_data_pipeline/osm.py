"""Extract boundaries, buildings, water and destination candidates from an OSM PBF.

Requires ``osmium`` and ``shapely`` (not yet in the locked manifest; see README).
Output: Parquet tables with WKB geometry in EPSG:4326 under data/processed/<city>/osm/.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import osmium
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

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
    "aeroway": {"aerodrome"},
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


def _ref(kind: str, osm_id: int) -> str:
    return f"{kind}/{osm_id}"


def _is_poi(tags: Any) -> bool:
    return any(tags.get(key) in POI_VALUES.get(key, ()) for key in POI_KEYS)


@dataclass
class _Tables:
    boundaries: list[dict[str, Any]] = field(default_factory=list)
    buildings: list[dict[str, Any]] = field(default_factory=list)
    water: list[dict[str, Any]] = field(default_factory=list)
    pois: list[dict[str, Any]] = field(default_factory=list)


class _Handler(osmium.SimpleHandler):
    def __init__(self, boundary_ids: set[int]) -> None:
        super().__init__()
        self.boundary_ids = boundary_ids
        self.wkb = osmium.geom.WKBFactory()
        self.tables = _Tables()

    @staticmethod
    def _tags(tags: Any) -> str:
        return json.dumps(
            {key: tags.get(key) for key in KEEP_TAGS if key in tags}, ensure_ascii=False
        )

    def node(self, node: Any) -> None:
        if _is_poi(node.tags):
            self.tables.pois.append(
                {
                    "osm_ref": _ref("node", node.id),
                    "tags": self._tags(node.tags),
                    "wkb": bytes.fromhex(self.wkb.create_point(node)),
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
                    }
                )
            building = tags.get("building")
            if building and building != "no":
                self.tables.buildings.append({"osm_ref": ref, "building": building, "wkb": geom()})
            if any(tags.get(key) == value for key, value in WATER_TAGS):
                self.tables.water.append({"osm_ref": ref, "wkb": geom()})
            if _is_poi(tags):
                self.tables.pois.append({"osm_ref": ref, "tags": self._tags(tags), "wkb": geom()})
        except RuntimeError:
            # Broken multipolygons are counted in QA by the caller via the absence of rows.
            return


def extract(pbf: Path, boundary_ids: set[int], out_dir: Path) -> dict[str, int]:
    handler = _Handler(boundary_ids)
    handler.apply_file(str(pbf), locations=True, idx="flex_mem")
    out_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for name in ("boundaries", "buildings", "water", "pois"):
        rows = getattr(handler.tables, name)
        pq.write_table(pa.Table.from_pylist(rows), out_dir / f"{name}.parquet")
        counts[name] = len(rows)
    missing = boundary_ids - {
        int(row["osm_ref"].split("/")[1]) for row in handler.tables.boundaries
    }
    if missing:
        raise RuntimeError(f"boundary relations not assembled: {sorted(missing)}")
    return counts
