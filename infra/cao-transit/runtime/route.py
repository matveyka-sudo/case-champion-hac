"""Route the saved ЦАО origin/destination pairs with a transfer penalty."""
from collections import Counter, defaultdict
import json
import sqlite3

from links import PLATFORM_SECONDS, WORK, WALK
from network import DAY, END, START, load, save

MAX_BOARDINGS = 4
PENALTY = 4 * 60
PENALTIES = (120, PENALTY, 360)
WAIT_SOURCE = {"bus":"gtfs", "tram":"gtfs", "metro":"half_headway", "mcc":"half_headway"}


def build_connections(net, links):
    def onboard(key):
        if key.startswith("e"):
            entry = links["entrances"][key]
            if entry["in"]:
                for stop in entry["nodes"]:
                    yield stop, PLATFORM_SECONDS
        else:
            yield key, 0

    def access_rows(pairs):
        for street, seconds in pairs:
            for stop, extra in onboard(street):
                yield stop, seconds + extra, [street] if extra else []

    access = {key:list(access_rows(pairs)) for key,pairs in links["access"].items()}
    transfers = defaultdict(list)
    for a, b, seconds in net["rail_transfers"]:
        transfers[a].append((b, seconds, []))
    for key, pairs in links["transfers"].items():
        for target, seconds in pairs:
            for stop, extra, via in access_rows([(target, seconds)]):
                if key.startswith("e"):
                    if target.startswith("g"):
                        for origin in links["entrances"][key]["nodes"]:
                            if links["entrances"][key]["out"]:
                                transfers[origin].append((stop, extra+PLATFORM_SECONDS, [key,*via]))
                else:
                    transfers[key].append((stop, extra, via))
    egress = {}
    for key, pairs in links["egress"].items():
        arrivals = defaultdict(list)
        for street, seconds in pairs:
            if street.startswith("e"):
                entry = links["entrances"][street]
                if entry["out"]:
                    for stop in entry["nodes"]:
                        arrivals[stop].append((seconds + PLATFORM_SECONDS, [street]))
            else:
                arrivals[street].append((seconds, []))
        egress[key] = {stop:min(options) for stop,options in arrivals.items()}
    return access, transfers, egress


def update(results, stop, at, record, end):
    if at <= end and (stop not in results or at < results[stop][0]):
        results[stop] = (at, record)


def scan_ground(trips, reached, result, end):
    for ti, (mode, line, events) in enumerate(trips):
        boarded = None
        for i, (stop, arrival, departure) in enumerate(events):
            if boarded is not None:
                si, entry = boarded
                update(result, stop, arrival,
                       (entry[1], ("ground", ti, si, i, entry[0], events[si][2], arrival)), end)
            if boarded is None and departure <= end:
                entry = reached.get(stop)
                if entry is not None and entry[0] <= departure:
                    boarded = (i, entry)


def scan_rail(trips, reached, result, end):
    for ti, (_, _, wait, stops, segments) in enumerate(trips):
        ring = stops[0] == stops[-1]
        n = len(segments)
        boarded = None
        at = end + 1
        for i in range(n * (2 if ring else 1) + 1):
            index = i % n if ring else i
            stop = stops[index]
            if boarded is not None and i - boarded[0] > n:
                boarded = None
            if boarded is not None:
                start, entry, departure = boarded
                update(result, stop, at,
                       (entry[1], ("rail", ti, start, i, entry[0], departure, at)), end)
            if i < n and (entry := reached.get(stop)) is not None:
                candidate = entry[0] + wait
                if boarded is None or candidate < at:
                    boarded = (i, entry, candidate)
                    at = candidate
            if boarded is not None and i < n * (2 if ring else 1):
                at += segments[index][0]


def route_origin(point, point_pairs, access, transfers, egress, net, start=START, end=END):
    reached = {}
    for stop, duration, via in access.get(point, ()):
        update(reached, stop, start+duration, (None, ("walk", point, stop, start, start+duration, via)), end)
    alternatives = defaultdict(list)
    for boardings in range(1, MAX_BOARDINGS+1):
        if not reached:
            break
        arrivals = {}
        scan_ground(net["ground_trips"], reached, arrivals, end)
        scan_rail(net["rail_paths"], reached, arrivals, end)
        if not arrivals:
            break
        for target, *_ in point_pairs:
            best = None
            for stop, (seconds, via) in egress.get(target, {}).items():
                arrival = arrivals.get(stop)
                if arrival is None:
                    continue
                at = arrival[0] + seconds
                if at <= end and (best is None or at < best[0]):
                    best = (at, (arrival[1], ("walk", stop, target, arrival[0], at, via)))
            if best is not None:
                alternatives[target].append((best[0]-start, boardings-1, best[1]))
        reached = dict(arrivals)
        for origin, (at, record) in arrivals.items():
            for stop, seconds, via in transfers.get(origin, ()):
                update(reached, stop, at+seconds, (record, ("walk",origin,stop,at,at+seconds,via)), end)
    return alternatives


def export_path(record, net):
    out = []
    while record is not None:
        record, step = record
        kind, *payload = step
        if kind == "walk":
            a,b,start,end,via = payload
            if end > start:
                out.append(dict(mode="walk", a=a, b=b, seconds=end-start, via=via))
            continue
        ti, start, end, ready, depart, arrive = payload
        if kind == "ground":
            mode, line, trip = net["ground_trips"][ti]
            stops = [row[0] for row in trip[start:end+1]]
            geometry = [net["stops"][stop][:2] for stop in stops]
        else:
            mode, line, _, stops, segments = net["rail_paths"][ti]
            size = len(segments)
            geometry = [point for i in range(start,end) for point in segments[i%size][1]]
            stops = [stops[i % size if stops[0] == stops[-1] else i] for i in range(start, end + 1)]
        out.append(dict(mode=mode, a=stops[0], b=stops[-1], line=line,
                        seconds=arrive-depart, shape=geometry,
                        geometry="schematic" if kind=="ground" else "actual"))
        if depart > ready:
            out.append(dict(mode="wait", a=stops[0], b=stops[0], seconds=depart-ready,
                            waiting_for=mode, source=WAIT_SOURCE[mode]))
    return out[::-1]


def main():
    net, links = (load(WORK/name) for name in ("network.json", "links.json"))
    access, transfers, egress = build_connections(net, links)
    with sqlite3.connect(f'file:{WALK/"walk.sqlite3"}?mode=ro',uri=True) as db:
        pairs = defaultdict(list)
        for point, destination, category, name, seconds, shape in db.execute(
                "select point_id,destination_id,category,name,travel_s,shape from routes where status='ok' order by point_id,destination_id"):
            pairs[point].append((destination, category, name, seconds, json.loads(shape)))
    results, sensitivity = {}, Counter()
    for i, (point, point_pairs) in enumerate(pairs.items(), 1):
        variants = route_origin(point, point_pairs, access, transfers, egress, net)
        by_category = defaultdict(list)
        for obj, category, name, walk, shape in point_pairs:
            for seconds, n, path in variants.get(obj, ()):
                by_category[category].append((seconds, n, obj, name, walk, shape, path))
        entries = {}
        for category, candidates in by_category.items():
            def best(penalty):
                return min(candidates, key=lambda x:(x[0]+x[1]*penalty,x[1],x[0],x[2]))
            seconds,n,obj,name,walk,shape,path = best(PENALTY)
            walk_best = min(x[3] for x in point_pairs if x[1]==category)
            sensitivity["evaluated"] += 1
            sensitivity["route_changes_2"] += best(120)[:3] != best(PENALTY)[:3]
            sensitivity["route_changes_6"] += best(360)[:3] != best(PENALTY)[:3]
            legs = export_path(path, net)
            assert sum(item["seconds"] for item in legs) == seconds
            assert sum(item["mode"] not in {"wait","walk"} for item in legs) == n + 1
            entries[category] = dict(destination_id=obj, name=name, transit_seconds=seconds, walk_seconds=walk,
                    walk_shape=shape, transfers=n, cost_seconds=seconds+n*PENALTY,
                    recommend="public_transport" if seconds+n*PENALTY<walk else "walk",
                    beats_best_category_walk=seconds+n*PENALTY<walk_best, legs=legs)
        results[point] = entries
        if i % 20 == 0 or i == len(pairs):
            print("Routing",i,"/",len(pairs),flush=True)
    save(WORK/"transit-raw.json",dict(date=DAY.isoformat(),start_seconds=START, horizon_seconds=END-START,
         transfer_penalty_seconds=PENALTY, max_transfers=MAX_BOARDINGS-1, pairs=sum(map(len,pairs.values())),
         sensitivity=dict(sensitivity),points=results))
    print("Complete",len(results),dict(sensitivity),flush=True)


if __name__ == "__main__":
    main()
