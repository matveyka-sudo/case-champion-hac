"""Build a local address and destination catalogue from the Valhalla source extract."""

import argparse
import hashlib
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

import osmium
from a2_engine.shared.area import select_area_h
from a2_engine.shared.config import load_urban_config
from pyproj import Geod
from shapely import wkb
from shapely.geometry import MultiPolygon, Point, mapping, shape
from shapely.ops import orient
from shapely.prepared import prep
from shapely.strtree import STRtree

BBOX = (36.62, 55.04, 38.15, 56.12)
FACILITIES = {"hospital", "school", "kindergarten", "college", "university"}


def normalize(value):
    return " ".join(
        "".join(
            c if c.isalnum() or c in "/-" else " " for c in value.lower().replace("ё", "е")
        ).split()
    )


class Collector(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.geometry = osmium.geom.WKBFactory()
        self.addresses = []
        self.facilities = []
        self.stops = {}
        self.members = defaultdict(set)
        self.areas = []
        self.districts = []
        self.municipalities = []
        self.entrances = []

    def point(self, item, key, point):
        tags = item.tags
        if tags.get("addr:street") and tags.get("addr:housenumber"):
            self.addresses.append(
                (
                    key,
                    tags["addr:street"],
                    tags["addr:housenumber"],
                    tags.get("addr:city", ""),
                    point,
                )
            )
        category = tags.get("amenity")
        if category not in FACILITIES and tags.get("healthcare") == "hospital":
            category = "hospital"
        if (
            category in FACILITIES
            and tags.get("name")
            and tags.get("access") not in {"no", "private"}
        ):
            self.facilities.append((key, category, tags["name"], point, "object", ""))
        if tags.get("public_transport") in {"stop_position", "platform"} and tags.get("name"):
            self.stops[key] = (tags["name"], point)
        if tags.get("railway") in {"subway_entrance", "train_station_entrance"} and tags.get(
            "access"
        ) not in {"no", "private"}:
            self.entrances.append((key, point))

    def node(self, node):
        if node.tags and node.location.valid():
            self.point(node, f"node/{node.id}", Point(node.location.lon, node.location.lat))

    def way(self, way):
        tags = way.tags
        if not (
            tags.get("addr:housenumber")
            or tags.get("public_transport") == "platform"
            or tags.get("amenity") in FACILITIES
            or tags.get("healthcare") == "hospital"
        ):
            return
        try:
            point = wkb.loads(self.geometry.create_linestring(way), hex=True).centroid
        except (RuntimeError, ValueError):
            return
        self.point(way, f"way/{way.id}", point)

    def relation(self, relation):
        tags = relation.tags
        if tags.get("route") == "subway" and tags.get("network:wikidata") == "Q5499":
            category = "metro"
        elif tags.get("route") == "train" and tags.get("network") == "МЦД":
            category = "mcd"
        elif tags.get("route") == "train" and tags.get("wikidata") == "Q26856861":
            category = "mck"
        else:
            return
        for member in relation.members:
            if member.role.startswith("stop") and member.type == "n":
                self.members[f"node/{member.ref}"].add((category, tags.get("ref", "")))

    def area(self, area):
        tags = area.tags
        city = tags.get("place") == "city" or (
            tags.get("place") == "town"
            and (
                tags.get("official_status") == "ru:город"
                or tags.get("description", "").startswith("город ")
            )
        )
        locality = city and tags.get("addr:region") == "Московская область"
        if area.from_way() and not locality:
            return
        municipality = (
            tags.get("boundary") == "administrative"
            and tags.get("admin_level") == "6"
            and tags.get("addr:region") == "Московская область"
        )
        district = tags.get("boundary") == "administrative" and tags.get("admin_level") == "8"
        if not (
            district
            or municipality
            or locality
            or tags.get("ISO3166-2") == "RU-MOS"
            or tags.get("amenity") in FACILITIES
            or tags.get("healthcare") == "hospital"
        ):
            return
        try:
            polygon = wkb.loads(self.geometry.create_multipolygon(area), hex=True)
        except (RuntimeError, ValueError):
            return
        kind = "way" if area.from_way() else "relation"
        key = f"{kind}/{area.orig_id()}"
        if municipality and polygon.is_valid and tags.get("name"):
            self.municipalities.append((f"oblast-{key.replace('/', '-')}", tags["name"], polygon))
        if district and polygon.is_valid and tags.get("name"):
            self.districts.append((f"moscow-{key.replace('/', '-')}", tags["name"], polygon))
        if locality or tags.get("ISO3166-2") == "RU-MOS":
            if polygon.is_valid and tags.get("name"):
                self.areas.append((key, tags["name"], polygon, locality))
        else:
            self.point(area, key, polygon.representative_point())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("extract", type=Path)
    parser.add_argument("region_boundary", type=Path)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--bbox-file", type=Path, help="Comma-separated routing extract bounds")
    args = parser.parse_args()
    bbox = tuple(map(float, args.bbox_file.read_text().split(","))) if args.bbox_file else BBOX
    data = Collector()
    data.apply_file(str(args.extract), locations=True, idx="flex_mem")
    region = Collector()
    region.apply_file(str(args.region_boundary), locations=True)
    oblast = next(polygon for key, _, polygon, _ in region.areas if key == "relation/51490")
    territories = json.loads((args.bundle / "territories.json").read_text())
    moscow = shape(next(row["geometry"] for row in territories if row["territory_id"] == "moscow"))
    prepared_moscow, prepared_oblast = prep(moscow), prep(oblast)
    localities = [
        row
        for row in data.areas
        if row[3] and prepared_oblast.covers(row[2].representative_point())
    ]
    municipalities = [
        (key, name, polygon)
        for key, name, polygon in data.municipalities
        if prepared_oblast.covers(polygon.representative_point())
        and polygon.intersection(oblast).area >= 0.99 * polygon.area
    ]
    municipality_index = STRtree([row[2] for row in municipalities])
    area_index = STRtree([row[2] for row in localities])
    geod = Geod(ellps="WGS84")
    config = load_urban_config()

    def half_life(geometry):
        area = MultiPolygon([orient(polygon) for polygon in geometry.geoms])
        return select_area_h(
            abs(geod.geometry_area_perimeter(area)[0]) / 1e6, bands=config.area_h_bands
        ).value

    city_h = {row[0]: half_life(row[2]) for row in localities}
    moscow_h = half_life(moscow)
    entrances = STRtree([row[1] for row in data.entrances])
    # Group stops by line/category and use actual mapped entrances where a nearby
    # entrance can be associated by proximity. Preserve the approach otherwise.
    for key, memberships in data.members.items():
        if key not in data.stops:
            continue
        name, point = data.stops[key]
        for category, line in sorted(memberships):
            data.facilities.append((key, category, name, point, "station_approach", line))
            if category == "metro" and len(data.entrances):
                entrance_key, entrance = data.entrances[int(entrances.nearest(point))]
                if geod.inv(point.x, point.y, entrance.x, entrance.y)[2] <= 180:
                    data.facilities.append(
                        (entrance_key, category, name, entrance, "mapped_entrance", line)
                    )
    temp = args.output.with_suffix(".tmp")
    temp.unlink(missing_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(temp) as db:
        db.executescript("""
            CREATE TABLE address(id TEXT PRIMARY KEY, label TEXT, search TEXT, region TEXT,
                                 lon REAL, lat REAL, h_seconds REAL);
            CREATE TABLE destination(id TEXT, category TEXT, name TEXT, lon REAL, lat REAL,
                                     location TEXT, line TEXT);
            CREATE INDEX destination_area ON destination(category, lat, lon);
            CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE district(id TEXT PRIMARY KEY, name TEXT, geometry TEXT);
        """)
        addresses = {}
        for key, street, number, tagged_city, point in data.addresses:
            if not (bbox[0] <= point.x <= bbox[2] and bbox[1] <= point.y <= bbox[3]):
                continue
            if prepared_moscow.covers(point):
                place, region_name, h = "Москва", "Москва", moscow_h
            elif prepared_oblast.covers(point):
                matches = sorted(
                    (localities[int(i)] for i in area_index.query(point, predicate="within")),
                    key=lambda row: (row[2].area, row[0]),
                )
                place, h = (matches[0][1], city_h[matches[0][0]]) if matches else ("", None)
                region_name = "Московская область"
                if not place:
                    place = tagged_city
                    if not place:
                        matches = municipality_index.query(point, predicate="within")
                        if len(matches) == 1:
                            place = municipalities[int(matches[0])][1]
            else:
                continue
            label = ", ".join(filter(None, (place or region_name, street, number)))
            identity = (normalize(label), round(point.x, 4), round(point.y, 4))
            addresses.setdefault(
                identity,
                (key, label, normalize(f"{region_name} {label}"), region_name, point.x, point.y, h),
            )
        db.executemany("INSERT INTO address VALUES(?,?,?,?,?,?,?)", addresses.values())
        facilities = {
            (key, category, name, point.x, point.y, location, line)
            for key, category, name, point, location, line in data.facilities
            if bbox[0] <= point.x <= bbox[2] and bbox[1] <= point.y <= bbox[3]
        }
        db.executemany("INSERT INTO destination VALUES(?,?,?,?,?,?,?)", sorted(facilities))
        districts = [
            (row["territory_id"], row["name"], json.dumps(row["geometry"]))
            for row in territories
            if row["level"] == "district"
        ]
        district_ids = {row[0] for row in districts}
        districts.extend(
            (key, name, json.dumps(mapping(polygon)))
            for key, name, polygon in data.districts
            if key not in district_ids
            and moscow.covers(polygon.representative_point())
            and polygon.intersection(moscow).area >= 0.95 * polygon.area
        )
        if args.bbox_file:
            districts.extend(
                (key, name, json.dumps(mapping(polygon))) for key, name, polygon in municipalities
            )
        db.executemany("INSERT INTO district VALUES(?,?,?)", districts)
        with args.extract.open("rb") as extract:
            digest = hashlib.file_digest(extract, "sha256").hexdigest()
        db.executemany(
            "INSERT INTO metadata VALUES(?,?)",
            [
                ("source", args.extract.name),
                ("sha256", digest),
                ("coverage", json.dumps(bbox)),
                ("moscow_h_seconds", str(moscow_h)),
            ],
        )
        print(f"{len(addresses)} addresses, {len(facilities)} destination records")
    temp.replace(args.output)
    if args.bbox_file:
        args.output.with_name("localities.json").write_text(
            json.dumps(
                [
                    {
                        "id": key,
                        "name": name,
                        "geometry": mapping(polygon),
                        "h_seconds": city_h[key],
                    }
                    for key, name, polygon, _ in localities
                ],
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
