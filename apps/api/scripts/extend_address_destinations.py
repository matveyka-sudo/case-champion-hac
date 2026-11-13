"""Add mall and bus stop data from the existing OSM snapshot without rebuilding addresses."""

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

import osmium
from shapely import wkb


class Destinations(osmium.SimpleHandler):
    def __init__(self, bounds):
        super().__init__()
        self.bounds = bounds
        self.geometry = osmium.geom.WKBFactory()
        self.rows = {}

    def add(self, kind, identifier, category, tags, point):
        if point is None or tags.get("access") in {"no", "private"}:
            return
        west, south, east, north = self.bounds
        if not (west <= point[0] <= east and south <= point[1] <= north):
            return
        name = tags.get("name") or (
            "Автобусная остановка" if category == "bus_stop" else "Торговый центр"
        )
        key = (f"{kind}/{identifier}", category)
        self.rows[key] = (
            *key,
            name,
            *point,
            "stop_approach" if category == "bus_stop" else "object",
            "",
        )

    def node(self, node):
        tags = node.tags
        if not node.location.valid():
            return
        point = (node.location.lon, node.location.lat)
        if tags.get("shop") == "mall":
            self.add("node", node.id, "mall", tags, point)
        # Platforms and roadside signs represent the passenger approach.
        if tags.get("highway") == "bus_stop" or (
            tags.get("public_transport") == "platform" and tags.get("bus") == "yes"
        ):
            self.add("node", node.id, "bus_stop", tags, point)

    def way(self, way):
        if way.tags.get("public_transport") != "platform" or way.tags.get("bus") != "yes":
            return
        try:
            point = wkb.loads(self.geometry.create_linestring(way), hex=True).interpolate(
                0.5, normalized=True
            )
        except (RuntimeError, ValueError):
            return
        self.add("way", way.id, "bus_stop", way.tags, (point.x, point.y))

    def area(self, area):
        if area.tags.get("shop") != "mall":
            return
        try:
            point = wkb.loads(
                self.geometry.create_multipolygon(area), hex=True
            ).representative_point()
        except (RuntimeError, ValueError):
            return
        kind = "way" if area.from_way() else "relation"
        self.add(kind, area.orig_id(), "mall", area.tags, (point.x, point.y))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("catalogue", type=Path)
    parser.add_argument("extract", type=Path)
    args = parser.parse_args()
    with args.extract.open("rb") as extract:
        digest = hashlib.file_digest(extract, "sha256").hexdigest()
    with sqlite3.connect(args.catalogue.resolve().as_uri() + "?mode=rw", uri=True) as db:
        metadata = dict(db.execute("SELECT key, value FROM metadata"))
        if digest != metadata["sha256"]:
            parser.error("The extract SHA-256 must match the catalogue snapshot")
        collector = Destinations(json.loads(metadata["coverage"]))
        collector.apply_file(str(args.extract), locations=True, idx="flex_mem")
        counts = {
            category: sum(key[1] == category for key in collector.rows)
            for category in ("mall", "bus_stop")
        }
        if not all(counts.values()):
            parser.error("The extract did not supply both categories")
        db.execute("DELETE FROM destination WHERE category IN ('mall', 'bus_stop')")
        db.executemany("INSERT INTO destination VALUES(?,?,?,?,?,?,?)", collector.rows.values())
        print(counts)


if __name__ == "__main__":
    main()
