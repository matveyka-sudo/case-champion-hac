"""Directory of intercity passenger nodes of Russia (rule set ``ru-intercity-nodes-rules-v2``).

PRODUCT_SPEC §3: an intercity trip starts and ends at nodes; TZ §«Пассажирские
связи»: with several stations or airports the result names the node pair, so the
node list of every territory is stored explicitly.

Sources:

- Wikidata (CC0), five SPARQL answers in one raw archive: railway stations and
  halts with ESR (P2815) or Express/UIC (P722) codes, bus stations, airports
  with IATA/ICAO codes and «place served» (P931), items of excluded classes, and
  the current «located in» (P131) graph above them;
- OurAirports (public domain) ``airports.csv``: the ``scheduled_service`` flag;
- the accepted territory directory: city ids, regions and the disputed flag;
- v2: the national OSM pass (``osm_national``): bus stations and city outlines.

Rules:

- a station belongs to the nearest directory city on its current P131 graph;
  the walk stops at federal subjects, so a station of a village never becomes a
  node of the town that runs the municipal district; ties are left out;
- an airport belongs to every directory city it serves (P931), else to the city
  it lies in: Khrabrovo serves Kaliningrad from outside the city (PRODUCT_SPEC §2);
- a railway node is a station or halt with an ESR or Express code: the code is
  what a timetable names; a station building (вокзал) without a code is part of a
  coded station, not a second node;
- a bus node is an item of the class «автостанция» itself: its subclass
  «конечная остановка автобуса» holds urban termini;
- freight and marshalling stations, former stations, closed objects (P576/P3999,
  an unknown date included) and military aerodromes are left out; a military
  aerodrome also typed as a civil airport is joint-use and stays (Khrabrovo);
  an airport needs an IATA or ICAO code;
- nodes without coordinates are left out and reported;
- a node is a place where trips may start; departures are confirmed only by a
  timetable (Yandex Rasp, carriers): the directory makes no service claim, the
  OurAirports flag is kept as reported, not as a schedule;
- the codes (ESR, Express, IATA, ICAO) are kept so a provider directory can be
  joined by code, never by name;
- v2 adds OSM bus stations: ``amenity=bus_station`` named «автовокзал» or
  «автостанция» (the urban rule ``osm-category-rules-v1``; other names are urban
  termini) inside a city outline (the OSM outline whose ``wikidata`` tag is the
  city); OSM objects of one city closer than 150 m are one station; an OSM
  station with the ``wikidata`` tag of a Wikidata bus node, or within 300 m of
  one in the same city, is that node (its OSM refs are added); a Wikidata
  «автостанция» named «конечная» or «автобусное кольцо» is an urban terminus.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import shutil
import uuid
import zipfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from . import territories
from .manifest import Identifier, load_dataset_manifest
from .paths import data_dir, processed_dir, raw_dir

SOURCE_ID = "wikidata"
RULES_VERSION = "ru-intercity-nodes-rules-v2"
BUS_NAME = re.compile(r"автовокзал|автостанц|автобусн\w*\s+вокзал", re.I)
# v2: a Wikidata «автостанция» named a terminus or a loop is an urban terminus
URBAN_TERMINUS = re.compile(r"конечн|автобусное\s+кольцо", re.I)
OSM_MERGE_M = 150.0
SAME_STATION_M = 300.0
NodeKind = Literal["rail_station", "bus_station", "airport"]

RAIL_CLASS_IDS = (
    "Q55488",  # железнодорожная станция
    "Q55485",  # тупиковая станция
    "Q1339195",  # железнодорожный вокзал
    "Q18543139",  # главный вокзал
    "Q55678",  # остановочный пункт
    "Q22808404",  # надземная станция
    "Q4312270",  # наземная станция
    "Q55491",  # подземная станция
    "Q11670533",  # приподнятая станция
    "Q1402443",  # приграничная станция
    "Q1335652",  # станция при аэропорте
    "Q11606300",  # конечная остановка
    "Q55490",  # проходная станция
    "Q114782406",  # станция смены колеи
)
RAIL_EXCLUDED_IDS = (
    "Q55493",  # грузовая станция
    "Q519608",  # сортировочная станция
    "Q4663385",  # бывшая станция
    "Q65464941",  # бывший остановочный пункт
)
BUS_CLASS_ID = "Q494829"  # автостанция; its subclass «конечная остановка автобуса» is urban
AIR_CLASS_IDS = (
    "Q1248784",  # аэропорт
    "Q644371",  # международный аэропорт
    "Q106643740",  # аэропорт федерального значения
    "Q94993988",  # коммерческий аэродром
    "Q62447",  # аэродром
)
MILITARY_AERODROME_ID = "Q695850"
# a joint-use aerodrome (Khrabrovo, Bratsk) is also typed as a civil airport
CIVIL_AIRPORT_IDS = frozenset(AIR_CLASS_IDS) - {"Q62447"}


def _values(ids: tuple[str, ...]) -> str:
    return " ".join(f"wd:{q}" for q in ids)


# filters run in Python: inside SPARQL they push the public endpoint past its 60 s limit
_COMMON = """OPTIONAL {{ ?n rdfs:label ?label FILTER(LANG(?label) = "ru") }}
  OPTIONAL {{ ?n wdt:P625 ?coord }}
  OPTIONAL {{ ?n wdt:P576|wdt:P3999 ?gone }}"""
QUERIES = {
    # anchored on the codes: the railway station class is too large to scan in 60 s
    "rail": f"""SELECT ?n ?cls ?label ?coord ?gone ?esr ?uic WHERE {{
  {{ ?n wdt:P2815 ?code }} UNION {{ ?n wdt:P722 ?code }}
  ?n wdt:P17 wd:Q159 ; wdt:P31 ?cls .
  {_COMMON.format()}
  OPTIONAL {{ ?n wdt:P2815 ?esr }}
  OPTIONAL {{ ?n wdt:P722 ?uic }} }}""",
    "bus": f"""SELECT ?n ?cls ?label ?coord ?gone WHERE {{
  ?cls wdt:P279* wd:{BUS_CLASS_ID} .
  ?n wdt:P31 ?cls ; wdt:P17 wd:Q159 .
  {_COMMON.format()} }}""",
    "air": f"""SELECT ?n ?cls ?label ?coord ?gone ?iata ?icao ?served WHERE {{
  VALUES ?cls {{ {_values(AIR_CLASS_IDS)} }}
  ?n wdt:P31 ?cls ; wdt:P17 wd:Q159 .
  {_COMMON.format()}
  OPTIONAL {{ ?n wdt:P238 ?iata }}
  OPTIONAL {{ ?n wdt:P239 ?icao }}
  OPTIONAL {{ ?n wdt:P931 ?served }} }}""",
    # items of a freight, marshalling, former or military class: left out in Python
    "excluded": f"""SELECT DISTINCT ?n ?bad WHERE {{
  VALUES ?bad {{ {_values((*RAIL_EXCLUDED_IDS, MILITARY_AERODROME_ID))} }}
  ?n wdt:P31 ?bad ; wdt:P17 wd:Q159 . }}""",
    # only current «located in» statements above every candidate node
    "admin": f"""SELECT DISTINCT ?x ?y WHERE {{
  {{ VALUES ?cls {{ {_values(RAIL_CLASS_IDS)} }} ?n wdt:P31 ?cls }}
  UNION {{ ?n wdt:P31/wdt:P279* wd:{BUS_CLASS_ID} }}
  UNION {{ VALUES ?cls {{ {_values(AIR_CLASS_IDS)} }} ?n wdt:P31 ?cls }}
  ?n wdt:P17 wd:Q159 .
  ?n wdt:P131* ?x .
  ?x p:P131 ?st . ?st ps:P131 ?y ; wikibase:rank ?rank .
  FILTER(?rank != wikibase:DeprecatedRank)
  FILTER NOT EXISTS {{ ?st pq:P582 ?end }} }}""",
}
KIND_BY_QUERY: dict[str, NodeKind] = {
    "rail": "rail_station",
    "bus": "bus_station",
    "air": "airport",
}
# Wikidata holds few Russian bus stations; railway and airport items are imported widely
COVERAGE = {
    "rail_station": ("partial", "COMMUNITY_DIRECTORY_PASSENGER_ROLE_UNVERIFIED"),
    "bus_station": ("partial", "WIKIDATA_BUS_STATIONS_SPARSE"),
    "airport": ("partial", "COMMUNITY_DIRECTORY_SERVICE_UNVERIFIED"),
    "ferry_terminal": ("not_collected", "NOT_COLLECTED"),
}


class IntercityNode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: Identifier
    kind: NodeKind
    name: str
    wikidata_id: str | None
    wikidata_classes: list[str]
    lon: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)
    territory_ids: list[Identifier] = Field(min_length=1)
    assignment_basis: Literal["located_in", "served_place", "osm_outline"]
    esr_codes: list[str]
    express_codes: list[str]
    iata: str | None
    icao: str | None
    ourairports_ident: str | None
    ourairports_scheduled_service: bool | None
    disputed: bool
    osm_refs: list[str] = Field(default_factory=list)


def _qid(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


def _point(value: str) -> tuple[float, float]:
    lon, lat = value.removeprefix("Point(").rstrip(")").split()
    return round(float(lon), 6), round(float(lat), 6)


def _nearest_cities(
    start: str, parents: dict[str, set[str]], cities: set[str], stops: set[str]
) -> list[str]:
    """Directory cities at the smallest P131 distance; the walk stops at cities and subjects."""

    seen = {start}
    frontier = [start]
    while frontier:
        found = sorted(q for q in frontier if q in cities)
        if found:
            return found
        nxt: list[str] = []
        for node in frontier:
            if node in stops and node != start:
                continue
            for parent in sorted(parents.get(node, ())):
                if parent not in seen:
                    seen.add(parent)
                    nxt.append(parent)
        frontier = nxt
    return []


def _metres(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def _osm_bus_stations(  # noqa: C901
    nodes: list[IntercityNode],
    national_dir: Path,
    directory: list[territories.TerritoryEntry],
    report: dict[str, Any],
) -> list[IntercityNode]:
    """Nodes with OSM bus stations added or merged (rules in the module docstring)."""

    from shapely import wkb as shapely_wkb  # type: ignore[import-untyped]
    from shapely.geometry import Point  # type: ignore[import-untyped]
    from shapely.ops import unary_union  # type: ignore[import-untyped]
    from shapely.prepared import prep  # type: ignore[import-untyped]

    from .osm_national import load_table

    city_by_qid = {e.wikidata_id: e for e in directory if e.level == "city"}
    shapes: dict[str, list[Any]] = {}
    for row in load_table(national_dir, "city_boundaries"):
        shapes.setdefault(row["city_wikidata"], []).append(shapely_wkb.loads(row["wkb"]))
    outlines = [
        (city_by_qid[q], unary_union(parts))
        for q, parts in sorted(shapes.items())
        if q in city_by_qid
    ]
    prepared = [(city, shape.bounds, prep(shape)) for city, shape in outlines]
    stations = load_table(national_dir, "bus_stations")
    named = [s for s in stations if s["name"] and BUS_NAME.search(s["name"])]
    by_city: dict[str, list[dict[str, Any]]] = {}
    outside = ambiguous = 0
    for station in sorted(named, key=lambda s: s["osm_ref"]):
        point = Point(station["lon"], station["lat"])
        hits = [
            city
            for city, (w, south, e, n), shape in prepared
            if w <= station["lon"] <= e and south <= station["lat"] <= n and shape.contains(point)
        ]
        if len(hits) == 1:
            by_city.setdefault(hits[0].territory_id, []).append(station)
        elif hits:
            ambiguous += 1
        else:
            outside += 1
    city_entry = {e.territory_id: e for e in directory}
    result = list(nodes)
    index = {n.node_id: i for i, n in enumerate(result)}
    wikidata_bus = [n for n in nodes if n.kind == "bus_station"]
    used_qids = {n.wikidata_id for n in nodes if n.wikidata_id}
    merged: list[str] = []
    added = 0
    for territory_id in sorted(by_city):
        groups: list[list[dict[str, Any]]] = []
        for station in by_city[territory_id]:
            here = (station["lon"], station["lat"])
            group = next(
                (g for g in groups if _metres(here, (g[0]["lon"], g[0]["lat"])) < OSM_MERGE_M),
                None,
            )
            if group is None:
                groups.append([station])
            else:
                group.append(station)
        for group in groups:
            first = group[0]
            refs = [s["osm_ref"] for s in group]
            here = (first["lon"], first["lat"])
            tags = {s["wikidata"] for s in group if s["wikidata"]}
            target = next(
                (n for n in wikidata_bus if n.wikidata_id in tags),
                None,
            ) or next(
                (
                    n
                    for n in wikidata_bus
                    if territory_id in n.territory_ids
                    and _metres(here, (n.lon, n.lat)) < SAME_STATION_M
                ),
                None,
            )
            if target is not None:
                current = result[index[target.node_id]]
                result[index[target.node_id]] = current.model_copy(
                    update={"osm_refs": sorted({*current.osm_refs, *refs})}
                )
                merged.append(f"{first['name']} → {target.name}")
                continue
            city = city_entry[territory_id]
            tag = sorted(tags)[0] if tags else None
            result.append(
                IntercityNode(
                    node_id=f"node-osm-{first['osm_ref'].replace('/', '-')}",
                    kind="bus_station",
                    name=first["name"],
                    wikidata_id=tag if tag not in used_qids else None,
                    wikidata_classes=[],
                    lon=round(first["lon"], 6),
                    lat=round(first["lat"], 6),
                    territory_ids=[territory_id],
                    assignment_basis="osm_outline",
                    esr_codes=[],
                    express_codes=[],
                    iata=None,
                    icao=None,
                    ourairports_ident=None,
                    ourairports_scheduled_service=None,
                    disputed=city.disputed,
                    osm_refs=refs,
                )
            )
            added += 1
    report["osm_bus_stations"] = {
        "amenity_bus_station": len(stations),
        "named_bus_station": len(named),
        "outside_city_outlines": outside,
        "in_several_outlines": ambiguous,
        "added": added,
        "merged_with_wikidata": sorted(merged),
        "cities_with_outline": len(outlines),
    }
    return result


def _ourairports(path: Path) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    by_iata: dict[str, dict[str, str]] = {}
    by_icao: dict[str, dict[str, str]] = {}
    with path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["iso_country"] != "RU" or row["type"] == "closed":
                continue
            if row["iata_code"]:
                by_iata.setdefault(row["iata_code"], row)
            for code in {row["icao_code"], row["gps_code"], row["ident"]} - {""}:
                by_icao.setdefault(code, row)
    return by_iata, by_icao


def build_nodes(  # noqa: C901
    raw_zip: Path,
    ourairports_csv: Path,
    directory: list[territories.TerritoryEntry],
    national_dir: Path | None = None,
) -> tuple[list[IntercityNode], dict[str, Any]]:
    with zipfile.ZipFile(raw_zip) as archive:
        rows = {name: territories._bindings(archive, name) for name in QUERIES}
    excluded: dict[str, set[str]] = {}
    for row in rows["excluded"]:
        excluded.setdefault(_qid(row["n"]["value"]), set()).add(_qid(row["bad"]["value"]))
    city_by_qid = {e.wikidata_id: e for e in directory if e.level == "city"}
    region_qids = {e.wikidata_id for e in directory if e.level == "region"}
    parents: dict[str, set[str]] = {}
    for row in rows["admin"]:
        parents.setdefault(_qid(row["x"]["value"]), set()).add(_qid(row["y"]["value"]))

    items: dict[str, dict[str, Any]] = {}
    urban_termini: set[str] = set()
    for query, query_kind in KIND_BY_QUERY.items():
        for row in rows[query]:
            qid = _qid(row["n"]["value"])
            item = items.setdefault(
                qid,
                {
                    "kind": None,
                    "kinds": set(),
                    "name": None,
                    "classes": set(),
                    "coord": None,
                    "esr": set(),
                    "uic": set(),
                    "iata": set(),
                    "icao": set(),
                    "served": set(),
                },
            )
            cls = _qid(row["cls"]["value"])
            if query_kind == "rail_station" and cls not in RAIL_CLASS_IDS:
                continue  # the code-anchored answer lists every class of the item
            if query_kind == "bus_station" and cls != BUS_CLASS_ID:
                urban_termini.add(qid)
                continue
            item["kinds"].add(query_kind)
            item["classes"].add(cls)
            if "label" in row and item["name"] is None:
                item["name"] = row["label"]["value"]
            item["gone"] = item.get("gone", False) or "gone" in row
            if "coord" in row and item["coord"] is None:
                item["coord"] = _point(row["coord"]["value"])
            for key, field in (("esr", "esr"), ("uic", "uic"), ("iata", "iata"), ("icao", "icao")):
                if key in row:
                    item[field].add(row[key]["value"].strip())
            if "served" in row:
                item["served"].add(_qid(row["served"]["value"]))

    by_iata, by_icao = _ourairports(ourairports_csv)
    report: dict[str, Any] = {"rules": RULES_VERSION, "candidates": dict(Counter())}
    dropped: dict[str, list[str]] = {
        "excluded_class": [],
        "closed": [],
        "no_russian_label": [],
        "several_kinds": [],
        "no_coordinates": [],
        "airport_without_code": [],
        "outside_directory_cities": [],
        "ambiguous_city": [],
    }
    candidates: Counter[str] = Counter()
    nodes: list[IntercityNode] = []
    for qid in sorted(items, key=lambda q: int(q[1:])):
        item = items[qid]
        if not item["kinds"]:
            continue  # a coded item of no railway class (a line, a junction)
        item["kind"] = sorted(item["kinds"])[0]
        candidates[item["kind"]] += 1
        label = f"{item['name']} ({qid})"
        bad = excluded.get(qid, set())
        if bad - {MILITARY_AERODROME_ID} or (bad and not item["classes"] & CIVIL_AIRPORT_IDS):
            dropped["excluded_class"].append(label)
            continue
        if item["gone"]:
            dropped["closed"].append(label)
            continue
        if item["name"] is None:
            dropped["no_russian_label"].append(qid)
            continue
        if national_dir is not None and item["kind"] == "bus_station":
            if URBAN_TERMINUS.search(item["name"]):
                dropped.setdefault("urban_terminus_name", []).append(label)
                continue
        if len(item["kinds"]) > 1:  # e.g. a station building typed as a bus station too
            dropped["several_kinds"].append(label)
            continue
        if item["coord"] is None:
            dropped["no_coordinates"].append(label)
            continue
        kind: NodeKind = item["kind"]
        if kind == "airport" and not (item["iata"] or item["icao"]):
            dropped["airport_without_code"].append(label)
            continue
        served = sorted(q for q in item["served"] if q in city_by_qid) if kind == "airport" else []
        basis: Literal["located_in", "served_place"] = "served_place" if served else "located_in"
        if not served:
            served = _nearest_cities(qid, parents, set(city_by_qid), region_qids)
            if len(served) > 1:
                dropped["ambiguous_city"].append(label)
                continue
        if not served:
            dropped["outside_directory_cities"].append(label)
            continue
        iata = sorted(item["iata"])[0] if item["iata"] else None
        icao = sorted(item["icao"])[0] if item["icao"] else None
        airport_row = None
        if kind == "airport":
            airport_row = (by_iata.get(iata) if iata else None) or (
                by_icao.get(icao) if icao else None
            )
        city_entries = [city_by_qid[q] for q in served]
        nodes.append(
            IntercityNode(
                node_id=f"node-wd-{qid.lower()}",
                kind=kind,
                name=item["name"],
                wikidata_id=qid,
                wikidata_classes=sorted(item["classes"]),
                lon=item["coord"][0],
                lat=item["coord"][1],
                territory_ids=sorted(e.territory_id for e in city_entries),
                assignment_basis=basis,
                esr_codes=sorted(item["esr"]),
                express_codes=sorted(item["uic"]),
                iata=iata,
                icao=icao,
                ourairports_ident=airport_row["ident"] if airport_row else None,
                ourairports_scheduled_service=(
                    airport_row["scheduled_service"] == "yes" if airport_row else None
                ),
                disputed=any(e.disputed for e in city_entries),
            )
        )
    report["candidates"] = dict(sorted(candidates.items()))
    if national_dir is not None:
        nodes = _osm_bus_stations(nodes, national_dir, directory, report)
    report["urban_bus_termini_left_out"] = len(
        urban_termini - {q for q in items if items[q]["kinds"]}
    )
    report["nodes_of_several_cities"] = sorted(
        f"{n.name}: {', '.join(n.territory_ids)}" for n in nodes if len(n.territory_ids) > 1
    )
    report["nodes"] = dict(sorted(Counter(n.kind for n in nodes).items()))
    report["dropped_counts"] = {k: len(v) for k, v in dropped.items()}
    # villages and stations along the line are expected outside cities: counts only
    report["dropped"] = {
        k: v
        for k, v in dropped.items()
        if k not in {"outside_directory_cities", "closed", "excluded_class"}
    }
    matched = {n.ourairports_ident for n in nodes if n.ourairports_ident}
    report["ourairports_scheduled_unmatched"] = sorted(
        f"{row['name']} ({row['iata_code'] or row['ident']})"
        for row in {r["ident"]: r for r in by_icao.values()}.values()
        if row["scheduled_service"] == "yes" and row["ident"] not in matched
    )
    report["airports_without_ourairports"] = sorted(
        f"{n.name} ({n.iata or n.icao})"
        for n in nodes
        if n.kind == "airport" and not n.ourairports_ident
    )
    esr = Counter(code for n in nodes for code in n.esr_codes)
    report["esr_codes_shared"] = sorted(code for code, count in esr.items() if count > 1)
    cities_with = {
        kind: len({t for n in nodes if n.kind == kind for t in n.territory_ids})
        for kind in KIND_BY_QUERY.values()
    }
    report["cities_with_nodes"] = cities_with
    report["cities_in_directory"] = len(city_by_qid)
    coverage = dict(COVERAGE)
    if national_dir is not None:
        coverage["bus_station"] = ("partial", "OSM_NAMED_BUS_STATIONS_IN_CITY_OUTLINES")
    report["coverage"] = {
        kind: {"status": status, "reason": reason} for kind, (status, reason) in coverage.items()
    }
    return nodes, report


PILOT_EXPECTED = {
    "kaliningrad": {"rail_station", "airport"},
    "moscow": {"rail_station", "airport"},
    "novorossiysk": {"rail_station"},
}


def _checks(nodes: list[IntercityNode], report: dict[str, Any]) -> list[dict[str, Any]]:
    ids = Counter(n.node_id for n in nodes)
    kinds_by_city: dict[str, set[str]] = {}
    for node in nodes:
        for territory_id in node.territory_ids:
            kinds_by_city.setdefault(territory_id, set()).add(node.kind)
    pilots_missing = {
        city: sorted(expected - kinds_by_city.get(city, set()))
        for city, expected in PILOT_EXPECTED.items()
        if expected - kinds_by_city.get(city, set())
    }
    unmatched = report["ourairports_scheduled_unmatched"]
    return [
        {
            "check_id": "node_ids_unique",
            "status": "pass" if all(c == 1 for c in ids.values()) else "fail",
            "severity": "fail",
            "details": {"duplicates": sorted(i for i, c in ids.items() if c > 1)},
        },
        {
            "check_id": "nodes_in_directory_cities",
            "status": "pass" if nodes else "fail",
            "severity": "fail",
            "details": {"nodes": report["nodes"], "cities_with_nodes": report["cities_with_nodes"]},
        },
        {
            "check_id": "pilot_cities_have_nodes",
            "status": "pass" if not pilots_missing else "warn",
            "severity": "warn",
            "details": {"missing_kinds": pilots_missing},
        },
        {
            "check_id": "scheduled_airports_matched",
            "status": "pass" if not unmatched else "warn",
            "severity": "warn",
            "details": {"ourairports_scheduled_without_node": unmatched},
        },
        {
            "check_id": "esr_codes_unique",
            "status": "pass" if not report["esr_codes_shared"] else "warn",
            "severity": "warn",
            "details": {"shared": report["esr_codes_shared"]},
        },
        {
            "check_id": "dropped_nodes",
            "status": "pass" if not any(report["dropped"].values()) else "warn",
            "severity": "warn",
            "details": report["dropped_counts"],
        },
        {
            "check_id": "bus_station_coverage",
            "status": "warn",
            "severity": "warn",
            "details": {
                "cities_with_bus_station": report["cities_with_nodes"]["bus_station"],
                "reason": report["coverage"]["bus_station"]["reason"],
            },
        },
    ]


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def publish(
    dataset_id: str,
    ourairports_dataset_id: str,
    territory_directory_id: str,
    directory_id: str,
    osm_dataset_id: str | None = None,
) -> Path:
    raw_manifest = load_dataset_manifest(dataset_id)
    airports_manifest = load_dataset_manifest(ourairports_dataset_id)
    sources = [raw_manifest, airports_manifest]
    national_dir = None
    if osm_dataset_id is not None:
        osm_manifest = load_dataset_manifest(osm_dataset_id)
        national_dir = processed_dir() / osm_dataset_id / "national"
        meta = json.loads((national_dir / "extract_meta.json").read_text())
        if meta["source"]["sha256"] != osm_manifest.checksum.value:
            raise ValueError(f"{national_dir}: not extracted from {osm_dataset_id}")
        sources.append(osm_manifest)
    raw_zip = raw_dir() / raw_manifest.raw_path
    airports_csv = raw_dir() / airports_manifest.raw_path
    for source, path in ((raw_manifest, raw_zip), (airports_manifest, airports_csv)):
        if _digest(path) != source.checksum.value:
            raise ValueError(f"{source.dataset_id}: raw sha256 mismatch")
    directory = territories.load_directory(territory_directory_id)
    nodes, report = build_nodes(raw_zip, airports_csv, directory, national_dir)
    checks = _checks(nodes, report)
    failed = [c["check_id"] for c in checks if c["status"] == "fail"]
    if failed:
        raise ValueError(f"intercity node directory QA failed: {failed}")
    base = data_dir() / "directories"
    target = base / directory_id
    if target.exists():
        raise FileExistsError(f"{directory_id} is published; directories are immutable")
    staging = base / f".staging-{directory_id}-{uuid.uuid4().hex}"
    staging.mkdir(parents=True)
    try:
        # an empty ``osm_refs`` is left out: a v1 rebuild keeps its bytes
        rows = [n.model_dump(mode="json", exclude_defaults=True) for n in nodes]
        payload = (json.dumps(rows, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        qa = {"directory_id": directory_id, "result": "pass", "checks": checks, "summary": report}
        qa_bytes = (json.dumps(qa, ensure_ascii=False, indent=2) + "\n").encode()
        (staging / "nodes.json").write_bytes(payload)
        (staging / "qa_report.json").write_bytes(qa_bytes)
        territory_manifest = base / territory_directory_id / "manifest.json"
        manifest = {
            "format": "a2-ml2-intercity-node-directory",
            "directory_id": directory_id,
            "created_at": datetime.now(UTC).isoformat(),
            "rules": RULES_VERSION,
            "territory_directory": {
                "directory_id": territory_directory_id,
                "manifest_sha256": _digest(territory_manifest),
            },
            "sources": [
                {
                    "source_id": m.source_id,
                    "dataset_id": m.dataset_id,
                    "url": str(m.source_url),
                    "retrieved_at": m.retrieved_at.isoformat(),
                    "raw_sha256": m.checksum.value,
                    "license": m.license.name,
                }
                for m in sources
            ],
            "coverage": report["coverage"],
            "files": {
                "nodes": {
                    "path": "nodes.json",
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "rows": len(rows),
                },
                "qa_report": {
                    "path": "qa_report.json",
                    "sha256": hashlib.sha256(qa_bytes).hexdigest(),
                    "rows": len(checks),
                },
            },
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.rename(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return target


def load_nodes(directory_id: str, root: Path | None = None) -> list[IntercityNode]:
    """Nodes of a published directory; hashes and rows are verified on every load."""

    directory = (root or data_dir() / "directories") / directory_id
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    for ref in manifest["files"].values():
        if _digest(directory / ref["path"]) != ref["sha256"]:
            raise ValueError(f"{directory_id}/{ref['path']}: sha256 mismatch")
    rows = json.loads((directory / "nodes.json").read_text(encoding="utf-8"))
    if len(rows) != manifest["files"]["nodes"]["rows"]:
        raise ValueError(f"{directory_id}: row count mismatch")
    return [IntercityNode.model_validate(row) for row in rows]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    fetch_cmd = sub.add_parser("fetch")
    fetch_cmd.add_argument("raw_name")
    build_cmd = sub.add_parser("build")
    build_cmd.add_argument("dataset_id")
    build_cmd.add_argument("ourairports_dataset_id")
    build_cmd.add_argument("territory_directory_id")
    build_cmd.add_argument("directory_id")
    build_cmd.add_argument("--osm-dataset-id")
    args = parser.parse_args()
    if args.command == "fetch":
        print(territories.fetch(raw_dir() / SOURCE_ID / args.raw_name, QUERIES))
        return
    print(
        publish(
            args.dataset_id,
            args.ourairports_dataset_id,
            args.territory_directory_id,
            args.directory_id,
            args.osm_dataset_id,
        )
    )


__all__ = ["IntercityNode", "build_nodes", "load_nodes", "publish"]

if __name__ == "__main__":
    main()
