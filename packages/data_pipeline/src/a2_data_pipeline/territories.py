"""Directory of Russian federal subjects and cities (rule set ``ru-territories-rules-v1``).

PRODUCT_SPEC §1: every city of Russia is available in the directory; a pair is
computed on demand, and a regional passenger result goes through capitals.

Source: Wikidata (CC0), three SPARQL answers stored as one raw archive.

- subjects: instances of «субъект Российской Федерации» (Q43263) with the
  ISO 3166-2 code, capital and time zone;
- cities: instances of «город России» (Q7930989) in Russia with coordinates and
  the latest population statement;
- the «located in» (P131) graph above the cities.

Rules:

- a city belongs to the nearest federal subject on its P131 graph (an
  autonomous okrug wins over the oblast it lies in); a federal city is its own
  subject; a city without a subject or coordinates is left out and reported;
- the time zone is the subject's IANA zone; when Wikidata has only a UTC offset
  for the subject, the zone is the representative IANA zone of that offset
  (current Russian zones of one offset share their rules) — reported per subject;
  a city takes its subject's zone (Yakutia spans several zones: reported);
- six subjects claimed by Russia and recognised by most states as part of
  Ukraine are kept with ``disputed=true``: showing them is a product decision.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import time
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .manifest import Identifier
from .paths import data_dir, raw_dir

SOURCE_ID = "wikidata"
RULES_VERSION = "ru-territories-rules-v1"
SPARQL_URL = "https://query.wikidata.org/sparql"
QUERIES = {
    "subjects": """SELECT ?s ?sLabel ?iso ?capital ?tz ?iana ?offset WHERE {
  ?s wdt:P31 wd:Q43263 .
  OPTIONAL { ?s wdt:P300 ?iso }
  OPTIONAL { ?s wdt:P36 ?capital }
  OPTIONAL { ?s wdt:P421 ?tz . OPTIONAL { ?tz wdt:P6687 ?iana } OPTIONAL { ?tz wdt:P2907 ?offset } }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "ru" } }""",
    "cities": """SELECT ?c ?cLabel ?coord ?pop ?popDate ?iana ?offset WHERE {
  VALUES ?cls { CITY_CLASSES }
  ?c wdt:P31 ?cls ; wdt:P17 wd:Q159 .
  OPTIONAL { ?c wdt:P625 ?coord }
  OPTIONAL { ?c p:P1082 ?ps . ?ps ps:P1082 ?pop . OPTIONAL { ?ps pq:P585 ?popDate } }
  OPTIONAL { ?c wdt:P421 ?tz . OPTIONAL { ?tz wdt:P6687 ?iana } OPTIONAL { ?tz wdt:P2907 ?offset } }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "ru" } }""",
    # only current «located in» statements: no end time, not deprecated
    "admin": """SELECT DISTINCT ?x ?y WHERE {
  VALUES ?cls { CITY_CLASSES }
  ?c wdt:P31 ?cls ; wdt:P17 wd:Q159 .
  ?c wdt:P131* ?x .
  ?x p:P131 ?st . ?st ps:P131 ?y ; wikibase:rank ?rank .
  FILTER(?rank != wikibase:DeprecatedRank)
  FILTER NOT EXISTS { ?st pq:P582 ?end } }""",
    # every truthy «located in» statement: a fallback for cities whose current chain breaks
    "admin_any": """SELECT DISTINCT ?x ?y WHERE {
  VALUES ?cls { CITY_CLASSES }
  ?c wdt:P31 ?cls ; wdt:P17 wd:Q159 .
  ?c wdt:P131* ?x . ?x wdt:P131 ?y . }""",
}
# Wikidata has no single class for a city of Russia: Kaliningrad is «город» (Q7930989),
# Yekaterinburg «город России» (Q106389302), Krasnodar only «мегаполис» (Q1637706).
# Direct classes only: the subclass closure brings villages and estates in.
CITY_CLASS_IDS = (
    "Q7930989",  # город
    "Q106389302",  # город России
    "Q515",  # город (city)
    "Q1549591",  # большой город
    "Q1637706",  # мегаполис
    "Q200250",  # метрополис
    "Q174844",  # мегагород
    "Q183342",  # город федерального значения в России
)
# federal cities carry no time zone in Wikidata; tzdata zones
FEDERAL_CITY_ZONES = {
    "Москва": "Europe/Moscow",
    "Санкт-Петербург": "Europe/Moscow",
    "Севастополь": "Europe/Simferopol",
}
QUERIES = {
    name: text.replace("CITY_CLASSES", " ".join(f"wd:{q}" for q in CITY_CLASS_IDS))
    for name, text in QUERIES.items()
}
# representative IANA zone per current UTC offset of Russian zones
ZONE_BY_OFFSET = {
    2: "Europe/Kaliningrad",
    3: "Europe/Moscow",
    4: "Europe/Samara",
    5: "Asia/Yekaterinburg",
    6: "Asia/Omsk",
    7: "Asia/Krasnoyarsk",
    8: "Asia/Irkutsk",
    9: "Asia/Yakutsk",
    10: "Asia/Vladivostok",
    11: "Asia/Magadan",
    12: "Asia/Kamchatka",
}
MULTI_ZONE_SUBJECTS = {"Якутия"}
DISPUTED = {
    "Республика Крым",
    "Севастополь",
    "Донецкая Народная Республика",
    "Луганская Народная Республика",
    "Запорожская область",
    "Херсонская область",
}
# pilot city ids already used by the urban bundles: (city, subject) -> id
PILOT_IDS = {
    ("Москва", "Москва"): "moscow",
    ("Калининград", "Калининградская область"): "kaliningrad",
    ("Новороссийск", "Краснодарский край"): "novorossiysk",
}


class TerritoryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    territory_id: Identifier
    level: Literal["city", "region"]
    name: str
    timezone: str
    timezone_basis: Literal[
        "subject_iana",
        "subject_offset",
        "capital_zone",
        "city_iana",
        "city_offset",
        "subject_zone",
        "tzdata_federal_city",
    ]
    parent_region_id: Identifier | None
    capital_city_id: Identifier | None
    wikidata_id: str
    iso_code: str | None
    lon: float | None = Field(default=None, ge=-180, le=180)
    lat: float | None = Field(default=None, ge=-90, le=90)
    population: int | None
    population_date: str | None
    disputed: bool


def _qid(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


def _bindings(archive: zipfile.ZipFile, name: str) -> list[dict[str, Any]]:
    payload = json.loads(archive.read(f"{name}.json"))
    rows: list[dict[str, Any]] = payload["results"]["bindings"]
    return rows


def fetch(target: Path) -> Path:
    """Run the three queries once and store the answers with their text (raw is immutable)."""

    import httpx

    if target.exists():
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    files: dict[str, bytes] = {}
    headers = {
        "Accept": "application/sparql-results+json",
        "User-Agent": "a2-data-pipeline/0.1 (hackathon research; contact via repository)",
    }
    for name, query in QUERIES.items():
        for attempt in range(4):
            response = httpx.get(SPARQL_URL, params={"query": query}, headers=headers, timeout=600)
            if response.status_code != 429 or attempt == 3:
                break
            # the public endpoint rate-limits: wait as long as it asks
            time.sleep(int(response.headers.get("Retry-After", "60")) + 1)
        response.raise_for_status()
        files[f"{name}.json"] = response.content
        time.sleep(5)
        files[f"{name}.rq"] = query.encode("utf-8")
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, files[name])
    return target


def _nearest_subject(start: str, parents: dict[str, set[str]], subjects: set[str]) -> list[str]:
    """Federal subjects at the smallest P131 distance from ``start`` (itself counts)."""

    seen = {start}
    frontier = [start]
    while frontier:
        found = sorted(q for q in frontier if q in subjects)
        if found:
            return found
        nxt: list[str] = []
        for node in frontier:
            for parent in sorted(parents.get(node, ())):
                if parent not in seen:
                    seen.add(parent)
                    nxt.append(parent)
        frontier = nxt
    return []


def build_entries(raw_zip: Path) -> tuple[list[TerritoryEntry], dict[str, Any]]:  # noqa: C901
    with zipfile.ZipFile(raw_zip) as archive:
        subject_rows = _bindings(archive, "subjects")
        city_rows = _bindings(archive, "cities")
        admin_rows = _bindings(archive, "admin")
        fallback_rows = _bindings(archive, "admin_any")
    subjects: dict[str, dict[str, Any]] = {}
    for row in subject_rows:
        qid = _qid(row["s"]["value"])
        entry = subjects.setdefault(
            qid,
            {
                "name": row["sLabel"]["value"],
                "iso": set(),
                "capital": set(),
                "iana": set(),
                "offset": set(),
            },
        )
        if "iso" in row:
            entry["iso"].add(row["iso"]["value"])
        if "capital" in row:
            entry["capital"].add(_qid(row["capital"]["value"]))
        if "iana" in row:
            entry["iana"].add(row["iana"]["value"])
        if "offset" in row:
            entry["offset"].add(round(float(row["offset"]["value"])))
    parents: dict[str, set[str]] = {}
    for row in admin_rows:
        parents.setdefault(_qid(row["x"]["value"]), set()).add(_qid(row["y"]["value"]))
    any_parents: dict[str, set[str]] = {}
    for row in fallback_rows:
        any_parents.setdefault(_qid(row["x"]["value"]), set()).add(_qid(row["y"]["value"]))
    cities: dict[str, dict[str, Any]] = {}
    for row in city_rows:
        qid = _qid(row["c"]["value"])
        entry = cities.setdefault(
            qid,
            {
                "name": row["cLabel"]["value"],
                "coord": None,
                "pops": [],
                "iana": set(),
                "offset": set(),
            },
        )
        if "iana" in row:
            entry["iana"].add(row["iana"]["value"])
        if "offset" in row:
            entry["offset"].add(round(float(row["offset"]["value"])))
        if "coord" in row and entry["coord"] is None:
            lon, lat = row["coord"]["value"].removeprefix("Point(").rstrip(")").split()
            entry["coord"] = (round(float(lon), 6), round(float(lat), 6))
        value = row.get("pop", {}).get("value", "")
        if value.replace(".", "", 1).isdigit():  # «unknown value» is a blank node, not a number
            entry["pops"].append((row.get("popDate", {}).get("value", "")[:10], int(float(value))))

    report: dict[str, Any] = {"rules": RULES_VERSION}

    def own_zone(item: dict[str, Any], prefix: str) -> tuple[str, str] | None:
        if len(item["iana"]) == 1:
            return next(iter(item["iana"])), f"{prefix}_iana"
        if len(item["offset"]) == 1 and next(iter(item["offset"])) in ZONE_BY_OFFSET:
            return ZONE_BY_OFFSET[next(iter(item["offset"]))], f"{prefix}_offset"
        return None

    zones: dict[str, tuple[str, str]] = {}
    no_zone: list[str] = []
    for qid, subject in subjects.items():
        zone = own_zone(subject, "subject")
        if zone is None and qid in cities:  # a federal city: the zone sits on the city item
            city_zone = own_zone(cities[qid], "city")
            zone = (
                (city_zone[0], "subject_iana" if city_zone[1] == "city_iana" else "subject_offset")
                if city_zone
                else None
            )
        if zone is None and subject["name"] in FEDERAL_CITY_ZONES:
            zone = (FEDERAL_CITY_ZONES[subject["name"]], "tzdata_federal_city")
        if zone is None:  # several zones (Yakutia): the capital's zone
            capital_zone = next(
                (own_zone(cities[c], "city") for c in sorted(subject["capital"]) if c in cities),
                None,
            )
            zone = (capital_zone[0], "capital_zone") if capital_zone else None
        if zone is None:
            no_zone.append(subject["name"])
        else:
            zones[qid] = zone
    report["subjects_zone_from_offset"] = sorted(
        subjects[q]["name"] for q, (_, basis) in zones.items() if basis == "subject_offset"
    )
    report["subjects_without_zone"] = sorted(no_zone)

    def region_id(qid: str) -> str:
        iso = sorted(i for i in subjects[qid]["iso"] if i.startswith("RU-"))
        return f"region-{iso[0].lower()}" if iso else f"region-{qid.lower()}"

    city_ids: dict[str, str] = {}
    city_entries: list[TerritoryEntry] = []
    dropped: dict[str, list[str]] = {
        "no_subject": [],
        "ambiguous_subject": [],
        "no_coordinates": [],
        "no_zone": [],
    }
    ambiguous: list[dict[str, Any]] = []
    fallback: list[str] = []
    for qid in sorted(cities, key=lambda q: int(q[1:])):
        city = cities[qid]
        nearest = _nearest_subject(qid, parents, set(subjects))
        if not nearest:  # the current chain breaks: any «located in» statement; ties left out
            nearest = _nearest_subject(qid, any_parents, set(subjects))
            if len(nearest) > 1:
                ambiguous.append(
                    {"city": city["name"], "subjects": [subjects[s]["name"] for s in nearest]}
                )
                dropped["ambiguous_subject"].append(city["name"])
                continue
            if nearest:
                fallback.append(city["name"])
        if not nearest:
            dropped["no_subject"].append(city["name"])
            continue
        if len(nearest) > 1:
            ambiguous.append(
                {"city": city["name"], "subjects": [subjects[s]["name"] for s in nearest]}
            )
        subject_qid = nearest[0]
        if city["coord"] is None:
            dropped["no_coordinates"].append(city["name"])
            continue
        if subject_qid not in zones:
            dropped["no_zone"].append(city["name"])
            continue
        subject_name = subjects[subject_qid]["name"]
        territory_id = PILOT_IDS.get((city["name"], subject_name), f"city-{qid.lower()}")
        city_ids[qid] = territory_id
        dated = sorted(city["pops"], key=lambda p: (p[0] != "", p[0], p[1]))
        population = dated[-1] if dated else None
        city_zone = own_zone(city, "city")
        city_tz, basis = city_zone if city_zone else (zones[subject_qid][0], "subject_zone")
        city_entries.append(
            TerritoryEntry(
                territory_id=territory_id,
                level="city",
                name=city["name"],
                timezone=city_tz,
                timezone_basis=basis,  # type: ignore[arg-type]
                parent_region_id=region_id(subject_qid),
                capital_city_id=None,
                wikidata_id=qid,
                iso_code=None,
                lon=city["coord"][0],
                lat=city["coord"][1],
                population=population[1] if population else None,
                population_date=(population[0] or None) if population else None,
                disputed=subject_name in DISPUTED,
            )
        )
    region_entries: list[TerritoryEntry] = []
    capitals_missing: list[str] = []
    for qid in sorted(subjects, key=lambda q: region_id(q)):
        subject = subjects[qid]
        if qid not in zones:
            continue
        capital = next((city_ids[c] for c in sorted(subject["capital"]) if c in city_ids), None)
        if qid in city_ids:  # a federal city is its own capital
            capital = city_ids[qid]
        if capital is None:
            capitals_missing.append(subject["name"])
        iso = sorted(i for i in subject["iso"] if i.startswith("RU-"))
        region_entries.append(
            TerritoryEntry(
                territory_id=region_id(qid),
                level="region",
                name=subject["name"],
                timezone=zones[qid][0],
                timezone_basis=zones[qid][1],  # type: ignore[arg-type]
                parent_region_id=None,
                capital_city_id=capital,
                wikidata_id=qid,
                iso_code=iso[0] if iso else None,
                population=None,
                population_date=None,
                disputed=subject["name"] in DISPUTED,
            )
        )
    entries = region_entries + sorted(city_entries, key=lambda e: e.territory_id)
    report |= {
        "subjects": len(subjects),
        "regions": len(region_entries),
        "cities_in_source": len(cities),
        "cities": len(city_entries),
        "dropped_cities": {k: sorted(v) for k, v in dropped.items()},
        "ambiguous_subject_ties": ambiguous,
        "subject_from_any_statement": sorted(fallback),
        "capitals_missing": sorted(capitals_missing),
        "disputed": sorted(e.name for e in region_entries if e.disputed),
        "multi_zone_subjects": sorted(MULTI_ZONE_SUBJECTS & {e.name for e in region_entries}),
        "cities_without_population": sum(1 for e in city_entries if e.population is None),
    }
    return entries, report


def _checks(entries: list[TerritoryEntry], report: dict[str, Any]) -> list[dict[str, Any]]:
    ids = [e.territory_id for e in entries]
    regions = {e.territory_id for e in entries if e.level == "region"}
    cities = {e.territory_id for e in entries if e.level == "city"}
    found_disputed = set(report["disputed"])

    def check(
        check_id: str, ok: bool, detail: str, severity: str = "fail", **values: Any
    ) -> dict[str, Any]:
        return {
            "check_id": check_id,
            "status": "pass" if ok else severity,
            "detail": detail,
            **values,
        }

    return [
        check("territory_ids_unique", len(ids) == len(set(ids)), "Territory ids are unique"),
        check(
            "cities_have_regions",
            all(e.parent_region_id in regions for e in entries if e.level == "city"),
            "Every city has a parent subject of the directory",
        ),
        check(
            "capitals_are_cities",
            all(
                e.capital_city_id in cities
                for e in entries
                if e.level == "region" and e.capital_city_id
            ),
            "Every listed capital is a city of the directory",
        ),
        check(
            "regions_have_capitals",
            not report["capitals_missing"],
            "Every subject has a capital among the cities (regional results go through capitals)",
            severity="warn",
            missing=report["capitals_missing"],
        ),
        check(
            "subjects_have_zones",
            not report["subjects_without_zone"],
            "Every federal subject has a time zone",
            missing=report["subjects_without_zone"],
        ),
        check(
            "city_count_plausible",
            1000 <= report["cities"] <= 1200,
            "About 1 100 cities (Rosstat: 1 117 on 01.01.2021)",
            severity="warn",
            cities=report["cities"],
        ),
        check(
            "dropped_cities",
            not any(report["dropped_cities"].values()),
            "No city of the source is left out",
            severity="warn",
            dropped=report["dropped_cities"],
        ),
        check(
            "disputed_marked",
            found_disputed == DISPUTED,
            "The six disputed subjects are present and marked",
            severity="warn",
            found=sorted(found_disputed),
        ),
        check(
            "pilot_cities_present",
            {"moscow", "kaliningrad", "novorossiysk"} <= cities,
            "The pilot cities keep their urban bundle ids",
        ),
    ]


def publish(raw_zip: Path, directory_id: str, retrieved_at: datetime, raw_sha256: str) -> Path:
    entries, report = build_entries(raw_zip)
    checks = _checks(entries, report)
    failed = [c["check_id"] for c in checks if c["status"] == "fail"]
    if failed:
        raise ValueError(f"territory directory QA failed: {failed}")
    base = data_dir() / "directories"
    target = base / directory_id
    if target.exists():
        raise FileExistsError(f"{directory_id} is published; directories are immutable")
    staging = base / f".staging-{directory_id}-{uuid.uuid4().hex}"
    staging.mkdir(parents=True)
    try:
        rows = [e.model_dump(mode="json") for e in entries]
        payload = (json.dumps(rows, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        qa = {"directory_id": directory_id, "result": "pass", "checks": checks, "summary": report}
        qa_bytes = (json.dumps(qa, ensure_ascii=False, indent=2) + "\n").encode()
        (staging / "territories.json").write_bytes(payload)
        (staging / "qa_report.json").write_bytes(qa_bytes)
        manifest = {
            "format": "a2-ml2-territory-directory",
            "directory_id": directory_id,
            "created_at": datetime.now(UTC).isoformat(),
            "rules": RULES_VERSION,
            "source": {
                "source_id": SOURCE_ID,
                "url": SPARQL_URL,
                "retrieved_at": retrieved_at.isoformat(),
                "raw_sha256": raw_sha256,
                "license": "CC0-1.0",
            },
            "files": {
                "territories": {
                    "path": "territories.json",
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


def load_directory(directory_id: str, root: Path | None = None) -> list[TerritoryEntry]:
    """Territories of a published directory; hashes and rows are verified on every load."""

    directory = (root or data_dir() / "directories") / directory_id
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    for ref in manifest["files"].values():
        content = (directory / ref["path"]).read_bytes()
        if hashlib.sha256(content).hexdigest() != ref["sha256"]:
            raise ValueError(f"{directory_id}/{ref['path']}: sha256 mismatch")
    rows = json.loads((directory / "territories.json").read_text(encoding="utf-8"))
    if len(rows) != manifest["files"]["territories"]["rows"]:
        raise ValueError(f"{directory_id}: row count mismatch")
    return [TerritoryEntry.model_validate(row) for row in rows]


def territory_options(entries: list[TerritoryEntry]) -> list[dict[str, Any]]:
    """BACKEND ``TerritoryOption`` rows (``a2_contracts.v2.api``)."""

    return [
        {
            "territory_id": e.territory_id,
            "level": e.level,
            "name": e.name,
            "timezone": e.timezone,
            "parent_region_id": e.parent_region_id,
            "capital_city_id": e.capital_city_id,
        }
        for e in entries
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    fetch_cmd = sub.add_parser("fetch")
    fetch_cmd.add_argument("raw_name")
    build_cmd = sub.add_parser("build")
    build_cmd.add_argument("dataset_id")
    build_cmd.add_argument("directory_id")
    args = parser.parse_args()
    if args.command == "fetch":
        print(fetch(raw_dir() / SOURCE_ID / args.raw_name))
        return
    from .manifest import load_dataset_manifest

    manifest = load_dataset_manifest(args.dataset_id)
    print(
        publish(
            raw_dir() / manifest.raw_path,
            args.directory_id,
            manifest.retrieved_at,
            manifest.checksum.value,
        )
    )


__all__ = ["TerritoryEntry", "build_entries", "load_directory", "territory_options"]

if __name__ == "__main__":
    main()
