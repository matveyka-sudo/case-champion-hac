import argparse
import json
from pathlib import Path

import osmium

parser = argparse.ArgumentParser(description="Read the Moscow OSM PBF into the current directory.")
parser.add_argument("pbf", type=Path)
pbf = str(parser.parse_args().pbf)
routes = json.loads(Path("osm-rail-relations.json").read_bytes())
stop_ids = {
    m["id"]
    for row in routes
    for m in row["members"]
    if m["type"] == "n" and m["role"].startswith("stop")
}
way_ids = {
    m["id"]
    for row in routes
    for m in row["members"]
    if m["type"] == "w" and not m["role"].startswith(("stop", "platform"))
}


class Details(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.stops = {}
        self.track_nodes = set()
        self.ways = {}
        self.areas = []

    def node(self, node):
        if node.id in stop_ids and node.location.valid():
            self.stops[node.id] = {
                "lon": node.location.lon,
                "lat": node.location.lat,
                "tags": dict(node.tags),
            }

    def way(self, way):
        if way.id in way_ids:
            ids = [n.ref for n in way.nodes]
            self.ways[way.id] = ids
            self.track_nodes.update(ids)

    def relation(self, relation):
        tags = dict(relation.tags)
        if tags.get("public_transport") not in {"stop_area", "stop_area_group"}:
            return
        members = [{"type": m.type, "id": m.ref, "role": m.role} for m in relation.members]
        self.areas.append({"id": relation.id, "tags": tags, "members": members})


class Coordinates(osmium.SimpleHandler):
    def __init__(self, wanted):
        super().__init__()
        self.wanted = wanted
        self.coords = {}

    def node(self, node):
        if node.id in self.wanted and node.location.valid():
            self.coords[node.id] = [node.location.lon, node.location.lat]


details = Details()
details.apply_file(pbf)
areas = [
    area
    for area in details.areas
    if any(m["type"] == "n" and m["id"] in stop_ids for m in area["members"])
]
area_ids = {area["id"] for area in areas}
groups = [
    area
    for area in details.areas
    if area["tags"].get("public_transport") == "stop_area_group"
    and any(m["type"] == "r" and m["id"] in area_ids for m in area["members"])
]
coords = Coordinates(
    details.track_nodes | {m["id"] for area in areas for m in area["members"] if m["type"] == "n"}
)
coords.apply_file(pbf)
report = {}
for mode in ("metro", "mcc", "mcd"):
    subset = [route for route in routes if route["mode"] == mode]
    report[mode] = {
        "routes": len(subset),
        "refs": sorted({row["tags"].get("ref", "?") for row in subset}),
        "duration_count": sum("duration" in row["tags"] for row in subset),
        "without_duration": sorted(
            {row["tags"].get("ref", "?") for row in subset if "duration" not in row["tags"]}
        ),
        "headway_count": sum(
            any(key.startswith("interval") for key in row["tags"]) for row in subset
        ),
    }
area_stop_ids = {m["id"] for area in areas for m in area["members"] if m["type"] == "n"}
report["inventory"] = {
    "stop_positions": len(stop_ids),
    "stop_positions_with_coordinates": len(details.stops),
    "track_ways": len(way_ids),
    "track_ways_with_refs": len(details.ways),
    "track_nodes": len(details.track_nodes),
    "missing_track_node_coordinates": len(details.track_nodes - coords.coords.keys()),
    "stop_areas": len(areas),
    "stop_area_groups": len(groups),
    "stop_positions_linked_to_area": len(stop_ids & area_stop_ids),
}


def write(path, value):
    temp = Path(path + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temp.replace(path)


write("osm-rail-audit.json", report)
write(
    "osm-rail-inventory.json",
    {
        "source": pbf,
        "routes": routes,
        "stops": details.stops,
        "stop_areas": areas,
        "stop_area_groups": groups,
        "track_ways": details.ways,
        "node_coordinates": coords.coords,
    },
)
print(json.dumps(report, ensure_ascii=False))
