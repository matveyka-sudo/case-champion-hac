import argparse
import json
from pathlib import Path

import osmium

parser = argparse.ArgumentParser(description="Read the Moscow OSM PBF into the current directory.")
parser.add_argument("pbf", type=Path)
pbf = str(parser.parse_args().pbf)
data = json.loads(Path("osm-rail-inventory.json").read_bytes())
membership = {}
for area in data["stop_areas"]:
    for member in area["members"]:
        if member["type"] == "n":
            membership.setdefault(member["id"], []).append(area["id"])
filters = osmium.filter.TagFilter(("railway", "subway_entrance"), ("public_transport", "entrance"))
features = []
nodes = osmium.FileProcessor(pbf, osmium.osm.NODE).with_filter(filters)
for node in nodes:
    if not node.location.valid():
        continue
    features.append(
        {
            "id": node.id,
            "coordinates": [node.location.lon, node.location.lat],
            "area_ids": membership.get(node.id, []),
            "tags": dict(node.tags),
        }
    )
temp = Path("osm-rail-entrances.json.tmp")
temp.write_text(json.dumps(features, ensure_ascii=False))
temp.replace("osm-rail-entrances.json")
print(
    "entrances:",
    len(features),
    "linked:",
    sum(bool(row["area_ids"]) for row in features),
    "station areas linked:",
    len({a for row in features for a in row["area_ids"]}),
)
