"""Route a new address or point on the ЦАО walking and transit networks."""

import json
import os
from functools import lru_cache
from pathlib import Path

from a2_api.adapters import address_access as address
from a2_api.db.repositories.address_catalog import candidate_rows
from a2_engine.shared.scoring import category_index
from shapely.geometry import Point, shape
from shapely.prepared import prep

from links import ACCESS_RADIUS_M, MAX_LINK_SECONDS, metres
from network import DAY, ground
from route import PENALTY, build_connections, export_path, route_origin


@lru_cache(maxsize=1)
def inputs():
    root = Path(os.environ["WAYDEX_CAO_ROUTING_DIR"])
    net = json.loads((root / "network.json").read_text())
    links = json.loads((root / "links.json").read_text())
    data = json.loads(Path("/preview/data-500m.json").read_text())
    return net, links, prep(shape(data["boundary"]))


@lru_cache(maxsize=1)
def schedules():
    net, _, boundary = inputs()
    west, south, east, north = boundary.context.bounds
    bounds = west - .115, south - .065, east + .115, north + .065
    stops, trips = ground(bounds, DAY, 0, 26 * 3600, os.environ["WAYDEX_CAO_GTFS"])
    return {**net, "stops": stops, "ground_trips": trips}


def nearby(client, point, locations, *, reverse=False):
    origin = (point.lon, point.lat)
    options = [(key, position) for key, position in locations.items()
               if metres(origin, position) <= ACCESS_RADIUS_M]
    result = []
    for offset in range(0, len(options), 40):
        batch = options[offset:offset + 40]
        place = {"lon": point.lon, "lat": point.lat, "search_cutoff": 45}
        nearby_locations = [{"lon": lon, "lat": lat, "search_cutoff": 45}
                            for _, (lon, lat) in batch]
        matrix = address._router_post(client, "/sources_to_targets", {
            "sources": nearby_locations if reverse else [place],
            "targets": [place] if reverse else nearby_locations,
        })
        if matrix is None:
            matches = address._router_post(client, "/locate", {"locations": [place, *nearby_locations]})
            if isinstance(matches, list) and matches[0].get("edges"):
                filtered = {key: position for (key, position), match in zip(batch, matches[1:], strict=True)
                            if match.get("edges")}
                if len(filtered) < len(batch):
                    result.extend(nearby(client, point, filtered, reverse=reverse))
            continue
        rows = matrix["sources_to_targets"]
        entries = [row[0] for row in rows] if reverse else rows[0]
        for (key, _), item in zip(batch, entries, strict=True):
            seconds = item.get("time")
            if seconds is not None and seconds <= MAX_LINK_SECONDS:
                result.append([key, seconds])
    return result


def walk_path(client, origin, destination):
    route = address._route(client, origin, destination)
    return [address.Point(lon=lon, lat=lat) for lon, lat in route[1]] if route else []


def transit_path(client, steps, positions):
    path = []
    for step in steps:
        if step["mode"] == "wait":
            continue
        if step["mode"] != "walk":
            path.extend(address.Point(lon=lon, lat=lat) for lon, lat in step["shape"])
            continue
        outdoor = [positions[key] for key in (step["a"], *step["via"], step["b"]) if key in positions]
        for (lon_a, lat_a), (lon_b, lat_b) in zip(outdoor, outdoor[1:]):
            if (lon_a, lat_a) == (lon_b, lat_b):
                continue
            path.extend(walk_path(
                client, address.Point(lon=lon_a, lat=lat_a),
                address.Destination(id=step["b"], name="", location="approach", line="", lon=lon_b, lat=lat_b),
            ))
    return path


def calculate(db, client, origin, categories, departure):
    _, links, boundary = inputs()
    if departure.date() != DAY or not boundary.covers(Point(origin.lon, origin.lat)):
        raise address.TransitAccessUnavailable(
            "Расчёт ОТ по точке доступен в ЦАО на 13 ноября 2026 года."
        )
    net = schedules()
    locations = {**links["positions"]}
    stop_set = set(links["transfers"]) | set(links["entrances"]) | {key for rows in links["access"].values() for key, _ in rows}
    street = {key: value for key, value in locations.items() if key in stop_set}
    destinations = {
        category: [address.Destination.model_validate(dict(row))
                   for row in candidate_rows(db, origin, category)
                   if metres((origin.lon, origin.lat), (row["lon"], row["lat"])) <= 4000]
        for category in categories
    }
    for category, options in destinations.items():
        near = [item for item in options if metres((origin.lon, origin.lat), (item.lon, item.lat)) <= 2000]
        destinations[category] = (near or options)[:4]

    targets = {item.id: item for items in destinations.values() for item in items}
    origin_key = "live-origin"
    raw_access = nearby(client, origin, street)
    raw_egress = {key: links["egress"][key] if key in links["egress"]
                  else nearby(client, item, street, reverse=True) for key, item in targets.items()}
    access, transfers, egress = build_connections(net, {
        **links, "access": {origin_key: raw_access}, "egress": raw_egress,
    })
    start = departure.hour * 3600 + departure.minute * 60 + departure.second
    alternatives = route_origin(origin_key, [(key,) for key in targets], access, transfers,
                                egress, net, start, start + 5400)
    locations.update({key: (item.lon, item.lat) for key, item in targets.items()})
    locations[origin_key] = (origin.lon, origin.lat)
    results = []
    for category, options in destinations.items():
        row = address.CategoryResult(category=category, unavailable_reason="no_route" if options else "no_destination")
        results.append(row)
        times = address._matrix_times(client, origin, options) or {}
        walking = [(seconds, 0, seconds, options[i].id, None) for i, seconds in times.items()]
        transit = [(seconds + (n * PENALTY), 1, seconds, key, path)
                   for key in (item.id for item in options)
                   for seconds, n, path in alternatives.get(key, ())]
        if not (walking or transit):
            continue
        _, _, seconds, key, path = min(walking + transit, key=lambda option: option[:4])
        row.seconds = seconds
        row.index = category_index(seconds, origin.h_seconds).value
        row.destination = targets[key]
        row.unavailable_reason = None
        if path is None:
            row.path = walk_path(client, origin, row.destination)
        else:
            steps = export_path(path, net)
            row.transit_lines = [step["line"] for step in steps if "line" in step]
            row.path = transit_path(client, steps, locations)
    return results
