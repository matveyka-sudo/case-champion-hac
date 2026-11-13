import json
from collections import Counter, defaultdict
from heapq import heappop, heappush
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

data = json.loads(Path("osm-rail-inventory.json").read_bytes())
coord = data["node_coordinates"]
ways = data["track_ways"]
stops = data["stops"]


def distance(a, b):
    lon1, lat1 = map(radians, a)
    lon2, lat2 = map(radians, b)
    term = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 12742000 * asin(sqrt(min(1, term)))


def adjacent_distance(graph, origin, destination, forbidden):
    queue = [(0.0, origin)]
    seen = set(forbidden)
    while queue:
        dist, node = heappop(queue)
        if node in seen:
            continue
        if node == destination:
            return round(dist, 1)
        seen.add(node)
        for other, length in graph[node]:
            if other not in seen:
                heappush(queue, (dist + length, other))
    return None


rows = []
counts = Counter()
for route in data["routes"]:
    graph = defaultdict(list)
    order = [
        str(m["id"]) for m in route["members"] if m["type"] == "n" and m["role"].startswith("stop")
    ]
    for member in route["members"]:
        if member["type"] != "w" or member["role"].startswith(("stop", "platform")):
            continue
        nodes = ways.get(str(member["id"]), [])
        for a, b in zip(nodes, nodes[1:]):
            a, b = str(a), str(b)
            length = distance(coord[a], coord[b])
            graph[a].append((b, length))
            graph[b].append((a, length))
    positions = set(order)
    for a, b in zip(order, order[1:]):
        length = adjacent_distance(graph, a, b, positions - {a, b})
        counts[(route["mode"], bool(length))] += 1
        rows.append(
            dict(
                mode=route["mode"],
                pattern_id=route["id"],
                ref=route["tags"].get("ref"),
                from_stop=a,
                to_stop=b,
                length_m=length,
            )
        )
temp = Path("osm-rail-segments.json.tmp")
temp.write_text(json.dumps(rows, ensure_ascii=False))
temp.replace("osm-rail-segments.json")
for (mode, ok), count in sorted(counts.items()):
    print(mode, "linked" if ok else "missing", count)
print("unlinked routes", sorted({(r["ref"], r["pattern_id"]) for r in rows if not r["length_m"]}))
