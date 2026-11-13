"""Compute walk access/egress/transfers with the same Valhalla pedestrian profile."""
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from itertools import islice
from math import hypot
from pathlib import Path
import json
import sqlite3
import subprocess
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from network import BASE, load, save

WORK = Path(__file__).resolve().parent
WALK = BASE / "cao-walk-500m"
BUNDLE = BASE / "source/data/bundles/moscow-urban-2026-11-10.4"
CONTAINER = "waydex-moscow-real-valhalla-1"
MAX_LINK_SECONDS = 18 * 60
MAX_TRANSFER_SECONDS = 9 * 60
ACCESS_RADIUS_M = 1150
TRANSFER_RADIUS_M = 550
# Modelled vestibule-to-platform time. Paid internal interchanges use their own timing.
PLATFORM_SECONDS = 90


def metres(a, b):
    return hypot((a[0] - b[0]) * 62500, (a[1] - b[1]) * 111200)


def valhalla():
    address = subprocess.check_output(["sudo", "-n", "docker", "inspect", "-f",
                                      "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", CONTAINER],
                                      text=True).strip()
    endpoint = "http://" + address + ":8002"
    with urlopen(endpoint + "/status", timeout=10) as stream:
        status = json.load(stream)
    if (status["version"], status["tileset_last_modified"]) != ("3.5.1", 1794334628):
        raise ValueError("Pedestrian graph differs from the saved walk matrix")
    return endpoint


def send(endpoint, route, body):
    headers = {"Content-Type": "application/json"}
    body.update(costing="pedestrian", costing_options={"pedestrian":{"walking_speed":5,"type":"foot"}})
    with urlopen(Request(endpoint + route, json.dumps(body).encode(), headers), timeout=35) as stream:
        return json.load(stream)


def location(position):
    return dict(lon=position[0], lat=position[1], search_cutoff=45, radius=35)


def matrix_task(endpoint, task):
    key, origin, targets, reverse = task
    singleton = [location(origin)]
    others = [location(p) for _, p in targets]
    try:
        reply = send(endpoint, "/sources_to_targets",
                     {"sources":others if reverse else singleton, "targets":singleton if reverse else others,
                      "units":"kilometers", "verbose":True})
    except HTTPError as exc:
        if exc.code not in (400,404):
            raise
        if len(targets) == 1:
            return key, []
        mid = len(targets) // 2
        _, left = matrix_task(endpoint,(key,origin,targets[:mid],reverse))
        _, right = matrix_task(endpoint,(key,origin,targets[mid:],reverse))
        return key, left + right
    # Valhalla reports the actual correlated coordinates. Reject large snaps.
    sources, dests = reply["sources"], reply["targets"]
    def xy(row):
        return row["lon"], row["lat"]
    raw = [row[0] for row in reply["sources_to_targets"]] if reverse else reply["sources_to_targets"][0]
    fixed = dests[0] if reverse else sources[0]
    movable = sources if reverse else dests
    if metres(origin, xy(fixed)) > 45:
        return key, []
    pairs = []
    for (target, point), item, snapped in zip(targets, raw, movable, strict=True):
        seconds = item.get("time")
        gap = metres(point, xy(snapped))
        if seconds is not None and seconds >= 0 and gap <= 45:
            pairs.append([target, seconds + round(.72 * (gap + metres(origin, xy(fixed))))])
    return key, pairs


def collect(endpoint, tasks, filename):
    if filename.exists():
        return load(filename)
    checkpoint = filename.with_suffix(".checkpoint.json")
    prior = load(checkpoint) if checkpoint.exists() else {"done":0,"pairs":{}}
    results = defaultdict(list, prior["pairs"])
    done = prior["done"]
    with ThreadPoolExecutor(max_workers=3) as pool:
        for i, (key, pairs) in enumerate(pool.map(lambda task: matrix_task(endpoint, task), tasks[done:]), done+1):
            results[key].extend(pairs)
            if i % 100 == 0:
                save(checkpoint, {"done":i,"pairs":results})
            if i % 400 == 0:
                print(filename.name, i, "/", len(tasks), flush=True)
    save(filename, results)
    return results


def pieces(key, origin, pairs, reverse=False):
    iterator = iter(pairs)
    while batch := list(islice(iterator, 48)):
        yield key, origin, batch, reverse


def main():
    network = load(WORK/"network.json")
    origins = load(WALK/"street-origins.json")
    origins = {key:[row["point"]["lon"],row["point"]["lat"]]
               for key,row in origins.items() if row["point"]}
    with sqlite3.connect(f'file:{WALK/"walk.sqlite3"}?mode=ro',uri=True) as db:
        objects = {row[0] for row in db.execute("select distinct destination_id from routes where status='ok'")}
    connectors = {r["entity_id"]:r for r in load(BUNDLE/"walk_connectors.json")
                  if r["traversable"] is True and r["entity_id"] in objects}
    nodes = {r["access_node_id"]:[r["lon"],r["lat"]] for r in load(BUNDLE/"access_nodes.json")}
    destinations = {key: nodes[row["access_node_id"]] for key,row in connectors.items()}
    stops = network["stops"]
    positions = {stop: p[:2] for stop,p in stops.items()}
    rail_by_area = defaultdict(list)
    for stop, (_,_,_,area,_) in network["rail_stops"].items():
        if area:
            rail_by_area[int(area)].append(stop)
    for entry in network["entrances"]:
        tag = entry["tags"].get("entrance")
        rail_stops = {stop for area in entry["area_ids"] for stop in rail_by_area[area]}
        if not rail_stops:
            continue
        key = "e" + str(entry["id"])
        positions[key] = entry["coordinates"]
        entry["nodes"] = sorted(rail_stops)
        entry["in"] = tag not in ("exit","exit_only")
        entry["out"] = tag not in ("entrance","entry","entry_only")
    # Public walk paths connect actual stop points and street-level entrances.
    usable = {key: coord for key,coord in positions.items()
              if min(metres(coord, pos) for pos in origins.values()) <= 5300}
    endpoint = valhalla()
    access = []
    for key, point in origins.items():
        access.extend(pieces(key,point,((s,p) for s,p in usable.items() if metres(point,p)<=ACCESS_RADIUS_M)))
    egress = []
    for key, point in destinations.items():
        egress.extend(pieces(key,point,((s,p) for s,p in usable.items() if metres(point,p)<=ACCESS_RADIUS_M),True))
    trans = []
    for key, point in usable.items():
        trans.extend(pieces(key,point,((s,p) for s,p in usable.items() if s!=key
                          and (key.startswith("g") or s.startswith("g")) and metres(point,p)<=TRANSFER_RADIUS_M)))
    print("Matrix tasks",len(access),len(egress),len(trans),"street nodes",len(usable),flush=True)
    a=collect(endpoint,access,WORK/"access.json")
    e=collect(endpoint,egress,WORK/"egress.json")
    t=collect(endpoint,trans,WORK/"transfers.json")
    transfers = {key:[[other,seconds] for other,seconds in rows if seconds <= MAX_TRANSFER_SECONDS]
                 for key,rows in t.items()}
    access = {key:[[other,seconds] for other,seconds in rows if seconds <= MAX_LINK_SECONDS]
              for key,rows in a.items()}
    egress = {key:[[other,seconds+round(connectors[key]["length_m"]*.72)] for other,seconds in rows
                   if seconds <= MAX_LINK_SECONDS] for key,rows in e.items()}
    save(WORK/"links.json",dict(access=access,egress=egress,transfers=transfers,positions=positions,
                              entrances={ "e"+str(row["id"]):row for row in network["entrances"] if "nodes" in row},
                              origins=origins, destinations=destinations))
    print("Links ready",len(access),len(egress),len(transfers),flush=True)


if __name__ == "__main__":
    main()
