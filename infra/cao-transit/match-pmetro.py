import io
import json
import re
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

refs = "1 2 3 4 5 6 7 8 8А 9 10 11 12 14 15 16 17 D1 D2 D3 D4 D4А".split()


def split_fields(line):
    return re.split(r",(?![^()]*\))", line)


def station(value):
    return re.sub(r"[^\w]+", "", value.lower().replace("ё", "е").replace(" i", " 1"))


def seconds(value):
    if not re.fullmatch(r"\d+(?:\.\d{2})?", value):
        raise ValueError(value)
    minutes, _, secs = value.partition(".")
    return int(minutes) * 60 + int(secs or 0)


with (
    zipfile.ZipFile("pmetro-moscow-202610.zip") as outer,
    zipfile.ZipFile(io.BytesIO(outer.read("Moscow.pmz"))) as inner,
):
    source = inner.read("Metro.trp").decode("cp1251")
timings = defaultdict(set)
for block in re.split(r"(?m)^\[", source):
    if not block.startswith("Line"):
        continue
    ref = refs[int(block.split("]", 1)[0][4:]) - 1]
    fields = dict(
        line.split("=", 1)
        for line in block.splitlines()
        if "=" in line and not line.startswith(";")
    )
    names = split_fields(fields["Stations"])
    times = split_fields(fields["Driving"])
    for index, value in enumerate(times):
        name, _, rest = names[index].partition("(")
        if not rest:
            targets = [names[index + 1].partition("(")[0]] if index + 1 < len(names) else []
            values = [value]
        else:
            targets = rest.removesuffix(")").split(",")
            values = value.strip("()").split(",")
        for target, time in zip(targets, values):
            if target and time and (amount := seconds(time)) > 0:
                timings[ref, station(name), station(target)].add(amount)
data = json.loads(Path("osm-rail-inventory.json").read_bytes())
extra = json.loads(Path("osm-d3-extra.json").read_bytes())
names = {k: v["tags"].get("name", "") for k, v in (data["stops"] | extra["stops"]).items()}
rows = []
stats = Counter()
missing = defaultdict(set)
for route in data["routes"]:
    ref = route["tags"].get("ref")
    key = "4" if ref == "4А" else ref
    stops = [
        str(m["id"]) for m in route["members"] if m["type"] == "n" and m["role"].startswith("stop")
    ]
    for start, end in zip(stops, stops[1:]):
        a, b = station(names.get(start, "")), station(names.get(end, ""))
        direct = timings.get((key, a, b), set())
        reverse = timings.get((key, b, a), set())
        candidates = direct or reverse
        status = "direct" if direct else "reverse" if reverse else "unmatched"
        if len(candidates) > 1:
            status = "ambiguous"
        stats[route["mode"], status] += 1
        rows.append(
            dict(
                ref=ref,
                relation=route["id"],
                from_stop=start,
                to_stop=end,
                match=status,
                seconds=next(iter(candidates)) if len(candidates) == 1 else None,
            )
        )
        if status in {"unmatched", "ambiguous"}:
            missing[ref].add(" → ".join((names.get(start, "?"), names.get(end, "?"))))
report = {
    "counts": {f"{mode}_{status}": n for (mode, status), n in stats.items()},
    "unmatched_examples": {k: sorted(v)[:8] for k, v in missing.items()},
    "status": "candidate only; reverse timings assume symmetry; named variants need review",
}
Path("pmetro-segment-match.json").write_text(json.dumps(rows, ensure_ascii=False))
Path("pmetro-match-audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps(report, ensure_ascii=False))
