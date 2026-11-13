"""Build a dated ground timetable and the interval-based rail network."""
import csv
import io
import json
import re
from collections import defaultdict
from datetime import date
from heapq import heappop, heappush
from pathlib import Path
from zipfile import ZipFile

BASE = Path(__file__).resolve().parent.parent
INPUTS = BASE / "cao-transit-inputs"
GTFS = Path("/opt/waydex/data/ml2-handoff-2026-11-10/gtfs/mos_ground_transport.gtfs-20261110.gtfs-repair-v1.zip")
DAY = date(2026, 11, 13)
START = 8 * 3600 + 45 * 60
END = START + 5400


def load(path):
    return json.loads(path.read_bytes())


def save(path, value):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
    tmp.replace(path)


def hhmmss(value):
    h, m, s = map(int, value.split(":"))
    return h * 3600 + m * 60 + s


def ground(bounds, day=DAY, start=START, end=END, feed_path=GTFS):
    west, south, east, north = bounds
    stops, trips, boardings = {}, defaultdict(list), defaultdict(list)
    with ZipFile(feed_path) as feed:
        def records(name):
            with feed.open(name + ".txt") as stream:
                yield from csv.DictReader(io.TextIOWrapper(stream, encoding="utf-8-sig"))

        date_key = day.strftime("%Y%m%d")
        weekday = day.strftime("%A").lower()
        active = {r["service_id"] for r in records("calendar")
                  if r["start_date"] <= date_key <= r["end_date"] and r[weekday] == "1"}
        routes = {r["route_id"]: ("bus" if r["route_type"] == "3" else "tram", r["route_short_name"])
                  for r in records("routes") if r["route_type"] in {"0", "3", "5"}}
        trip_routes = {r["trip_id"]: routes[r["route_id"]] for r in records("trips")
                       if r["service_id"] in active and r["route_id"] in routes}
        for row in records("stops"):
            lon, lat = float(row["stop_lon"]), float(row["stop_lat"])
            if west <= lon <= east and south <= lat <= north:
                stops[row["stop_id"]] = (lon, lat, row["stop_name"])
        # Keep an intervening out-of-bounds event to prevent teleporting across a clipped trip.
        for row in records("stop_times"):
            trip = row["trip_id"]
            if trip not in trip_routes:
                continue
            arrival, departure = hhmmss(row["arrival_time"]), hhmmss(row["departure_time"])
            if departure < start or arrival > end:
                continue
            stop = row["stop_id"]
            trips[trip].append((int(row["stop_sequence"]), stop if stop in stops else None, arrival, departure))
    ride_trips = []
    seen = set()
    for trip, times in trips.items():
        times.sort()
        events = []
        mode, line = trip_routes[trip]
        for _, stop, arrival, departure in times + [(0, None, 0, 0)]:
            if stop is not None:
                events.append(("g" + stop, arrival, departure))
                continue
            if len(events) > 1:
                key = (mode, line, tuple(events))
                if key not in seen and all(a[2] <= b[1] for a, b in zip(events, events[1:])):
                    seen.add(key)
                    ride_trips.append([mode, line, events])
            events = []
    used = {s for _, _, trip in ride_trips for s, _, _ in trip}
    stops = {"g"+key: value for key, value in stops.items() if "g"+key in used}
    return stops, ride_trips


def track_path(graph, source, target, other_stops):
    queue = [(0, source, None)]
    parents = {}
    while queue:
        cost, node, parent = heappop(queue)
        if node in parents or node in other_stops:
            continue
        parents[node] = parent
        if node == target:
            path = [node]
            while parents[path[-1]] is not None:
                path.append(parents[path[-1]])
            return path[::-1]
        for next_node, weight in graph[node]:
            if next_node not in parents and next_node not in other_stops:
                heappush(queue, (cost + weight, next_node, node))
    raise ValueError(f"Disconnected rail track {source} → {target}")


def normalized(name):
    return re.sub(r"[^\w]+", "", name.casefold().replace("ё", "е"))


def rail():
    raw = load(INPUTS / "osm-rail-inventory.json")
    entrances = load(INPUTS / "osm-rail-entrances.json")
    times = {(r["relation"], r["from_stop"], r["to_stop"]): r["seconds"]
             for r in load(INPUTS / "pmetro-segment-match.json")}
    areas = {str(a["id"]): a for a in raw["stop_areas"]}
    area_by_stop = defaultdict(set)
    for area_id, area in areas.items():
        for m in area["members"]:
            if m["type"] == "n" and m["role"].startswith("stop"):
                area_by_stop[str(m["id"])].add(area_id)
    coordinates = raw["node_coordinates"]
    rail_paths, stop_info = [], {}
    line_nodes = defaultdict(set)
    for relation in raw["routes"]:
        mode, tags = relation["mode"], relation["tags"]
        if mode not in ("metro", "mcc"):
            continue
        line = tags["ref"]
        stops = [str(m["id"]) for m in relation["members"]
                 if m["type"] == "n" and m["role"].startswith("stop")]
        graph = defaultdict(list)
        for member in relation["members"]:
            if member["type"] != "w" or member["role"].startswith(("stop", "platform")):
                continue
            nodes = list(map(str, raw["track_ways"][str(member["id"])]))
            for a, b in zip(nodes, nodes[1:]):
                lon1, lat1 = coordinates[a]
                lon2, lat2 = coordinates[b]
                weight = (lon1-lon2)**2 * .317 + (lat1-lat2)**2
                weight = weight ** .5
                graph[a].append((b, weight))
                graph[b].append((a, weight))

        station_keys, segments = [], []
        for stop in stops:
            candidates = area_by_stop[stop]
            # A platform without an unambiguous area remains usable on the train.
            area = next(iter(candidates)) if len(candidates) == 1 else None
            station = raw["stops"][stop]
            name = station["tags"].get("name") or (areas[area]["tags"].get("name") if area else None)
            key = "r" + line + ":" + (area or normalized(name) or stop)
            station_keys.append(key)
            stop_info[key] = [station["lon"], station["lat"], name or key, area, mode]
            line_nodes[("4" if line == "4А" else line, normalized(name or ""))].add(key)
        for a, b in zip(stops, stops[1:]):
            track = track_path(graph, a, b, set(stops) - {a, b})
            seconds = times[(relation["id"], a, b)]
            assert seconds and seconds > 0
            segments.append([seconds, [coordinates[node] for node in track]])
        # 08:45–10:15 only. MCC uses the published peak interval. Other values
        # are explicit pilot assumptions except for the published Circle line.
        headway = 240 if mode == "mcc" else {"5": 90, "4": 240, "4А": 360, "12": 240,
                                                        "15": 240, "16": 300, "17": 300}.get(line, 180)
        rail_paths.append([mode, line, headway // 2, station_keys, segments])

    # For a branch, shared line 4/4A platforms inherit each other's measured transfer times.
    transfers = {}
    pmetro_refs = dict(zip(
        ("Сокольническая", "Замоскворецкая", "Арбатско-Покровская", "Филевская", "Кольцевая",
         "Калужско-Рижская", "Таганско-Краснопресненская", "Калининско-Солнцевская", "Солнцевская",
         "Серпуховско-Тимирязевская", "Люблинско-Дмитровская", "БКЛ", "Бутовская", "МЦК",
         "Некрасовская", "Троицкая", "Рублёво-Архангельская"),
        "1 2 3 4 5 6 7 8 8А 9 10 11 12 14 15 16 17".split(), strict=True,
    ))
    with ZipFile(INPUTS/"pmetro-moscow-202610.zip") as outer, ZipFile(io.BytesIO(outer.read("Moscow.pmz"))) as inner:
        table = inner.read("Metro.trp").decode("cp1251").split("[Transfers]", 1)[1].split("[AdditionalInfo]", 1)[0]
    for row in table.splitlines():
        if "=" not in row or row.startswith(";"):
            continue
        line_a, name_a, line_b, name_b, raw_time, *_ = map(str.strip, row.split("=", 1)[1].split(","))
        endpoints = [line_nodes[pmetro_refs.get(line), normalized(name)]
                     for line, name in ((line_a, name_a), (line_b, name_b))]
        if not all(endpoints):
            continue
        # More than one physical area with the same line/name is ambiguous.
        if any(len({stop_info[k][3] for k in keys}) > 1 for keys in endpoints):
            continue
        minutes, _, seconds = raw_time.partition(".")
        duration = int(minutes) * 60 + int(seconds or "0")
        if duration <= 0:
            continue
        for a in endpoints[0]:
            for b in endpoints[1]:
                if a != b:
                    transfers[a,b] = transfers[b,a] = duration
    # Older transfer table misses some confirmed interchanges on new lines.
    # Apply a modelled transition only within an explicit OSM interchange group.
    nodes_by_area = defaultdict(list)
    for node, (_, _, _, area, _) in stop_info.items():
        if area:
            nodes_by_area[area].append(node)
    for group in raw["stop_area_groups"]:
        nodes = [n for m in group["members"] if m["type"] == "r" for n in nodes_by_area[str(m["id"])]]
        for a in nodes:
            for b in nodes:
                if a != b and stop_info[a][3] != stop_info[b][3]:
                    transfers.setdefault((a,b), 360)
    return stop_info, rail_paths, entrances, [[a,b,t] for (a,b),t in transfers.items()]


if __name__ == "__main__":
    import sys
    work = Path(__file__).resolve().parent
    points = load(BASE/"cao-walk-500m/street-origins.json")
    starts = [row["point"] for row in points.values() if row["point"]]
    bounds = (min(x["lon"] for x in starts)-.115, min(x["lat"] for x in starts)-.065,
              max(x["lon"] for x in starts)+.115, max(x["lat"] for x in starts)+.065)
    stops, ground_trips = ground(bounds)
    rail_stops, rail_paths, entrances, transfers = rail()
    save(work/"network.json", dict(stops=stops, ground_trips=ground_trips, rail_stops=rail_stops,
                                   rail_paths=rail_paths, entrances=entrances, rail_transfers=transfers))
    print("Ground stops", len(stops), "trips", len(ground_trips),
          "rail patterns", len(rail_paths), "rail transfers", len(transfers), flush=True)
