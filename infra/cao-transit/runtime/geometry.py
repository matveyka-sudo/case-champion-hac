"""Materialize selected walking legs and verify the export."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError
from links import WORK, WALK, valhalla, send, location
from network import load, save


def main():
    raw = load(WORK/"transit-raw.json")
    links = load(WORK/"links.json")
    stations = load(WORK/"network.json")["rail_stops"]
    positions = {**links["positions"], **links["origins"], **links["destinations"]}
    work = set()
    for row in raw["points"].values():
        for candidate in row.values():
            for step in candidate["legs"]:
                if step["mode"] != "walk":
                    continue
                a, b, via = step["a"], step["b"], step["via"]
                # Only outdoor paths have a route in the pedestrian graph.
                outdoor = [x for x in [a,*via,b] if x in positions]
                if len(outdoor) >= 2 and outdoor[0] != outdoor[-1]:
                    work.add((outdoor[0],outdoor[-1]))
    endpoint = valhalla()
    def route(pair):
        try:
            trip = send(endpoint,"/route",{"locations":[location(positions[key]) for key in pair],
                                         "units":"kilometers"})["trip"]
        except HTTPError as exc:
            if exc.code in (400,404):
                return pair, None
            raise
        return pair, [leg["shape"] for leg in trip["legs"]]
    with ThreadPoolExecutor(max_workers=3) as pool:
        lines = dict(pool.map(route, sorted(work)))
    modes = Counter()
    recommends = Counter()
    missing = 0
    for row in raw["points"].values():
        for candidate in row.values():
            recommends[candidate["recommend"]]+=1
            modes.update({step["mode"] for step in candidate["legs"]})
            for step in candidate["legs"]:
                if step["mode"]!="walk":
                    continue
                outdoor = [x for x in [step["a"],*step["via"],step["b"]] if x in positions]
                line = lines.get((outdoor[0],outdoor[-1])) if len(outdoor)>=2 else None
                if line:
                    step["encoded_shape"] = line
                else:
                    a = positions.get(step["a"]) or stations[step["a"]][:2]
                    b = positions.get(step["b"]) or stations[step["b"]][:2]
                    step["shape"] = [a,b]
                    step["geometry"]="schematic"
                    missing+=1
    raw["qa"]=dict(modes=dict(modes),recommendations=dict(recommends),schematic_walk_legs=missing)
    save(WORK/"transit-500m.json",raw)
    print(raw["qa"],flush=True)


if __name__=="__main__":
    main()
