import argparse
import json
from pathlib import Path

import osmium

parser = argparse.ArgumentParser(
    description="Recover missing D3 members from the full OSM snapshot."
)
parser.add_argument("pbf", type=Path)
pbf = parser.parse_args().pbf
stops = {1994609870, 1994609791, 1994609788, 1994609872}
ways = {45689020, 1210621258, 1210621257, 1210436337, 110368161, 1210436329}


class Inventory(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.ways = {}
        self.stops = {}

    def node(self, node):
        if node.id in stops and node.location.valid():
            self.stops[node.id] = {
                "coordinates": [node.location.lon, node.location.lat],
                "tags": dict(node.tags),
            }

    def way(self, way):
        if way.id in ways:
            self.ways[way.id] = [node.ref for node in way.nodes]


inventory = Inventory()
inventory.apply_file(str(pbf), locations=False)
payload = {"source": str(pbf), "stops": inventory.stops, "ways": inventory.ways}
temp = Path("osm-d3-extra.json.tmp")
temp.write_text(json.dumps(payload, ensure_ascii=False))
temp.replace("osm-d3-extra.json")
print(
    f"Recovered D3 references: {len(inventory.stops)}/{len(stops)} stops, {len(inventory.ways)}/{len(ways)} ways."
)
