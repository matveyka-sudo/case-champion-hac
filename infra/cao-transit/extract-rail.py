import argparse
import json
import re
from collections import Counter
from pathlib import Path

import osmium

parser = argparse.ArgumentParser(description="Read the Moscow OSM PBF into the current directory.")
parser.add_argument("pbf", type=Path)
pbf = str(parser.parse_args().pbf)
mcd = re.compile(r"(^|[^\w])[dд][ -]?[1-4]($|[^\w])|мцд|московские центральные диаметры", re.I)
mcc = re.compile(r"мцк|московское центральное кольцо", re.I)


class Extract(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.routes = []

    def relation(self, relation):
        tags = dict(relation.tags)
        kind = tags.get("route")
        identity = " ".join(tags.get(key, "") for key in ("ref", "name", "network"))
        if tags.get("type") != "route":
            return
        if kind == "subway":
            mode = "metro"
        elif kind in ("train", "light_rail") and mcc.search(identity):
            mode = "mcc"
        elif kind in ("train", "light_rail") and mcd.search(identity):
            mode = "mcd"
        else:
            return
        members = [{"type": m.type, "id": m.ref, "role": m.role} for m in relation.members]
        self.routes.append({"id": relation.id, "mode": mode, "tags": tags, "members": members})


extract = Extract()
extract.apply_file(pbf)
Path("osm-rail-relations.json.tmp").write_text(json.dumps(extract.routes, ensure_ascii=False))
Path("osm-rail-relations.json.tmp").replace("osm-rail-relations.json")
print(Counter(row["mode"] for row in extract.routes))
for row in extract.routes:
    tags = row["tags"]
    selected = {
        k: v for k, v in tags.items() if k.startswith(("interval", "duration", "opening_hours"))
    }
    stops = sum(member["role"].startswith("stop") for member in row["members"])
    print(row["mode"], row["id"], tags.get("ref"), tags.get("name"), "stops", stops, selected)
