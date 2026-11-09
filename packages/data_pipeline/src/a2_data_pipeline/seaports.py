"""Directory of Russian seaports as intercity freight nodes (rule set ``ru-seaports-rules-v1``).

TZ «Грузовые связи»: a port, a freight station, a terminal and a logistics centre
are separate node kinds; the choice of «main» nodes needs a verifiable basis
(function, confirmed volumes or capacity), and the node list of every territory
is stored explicitly.

Sources:

- Rosmorport: the visible list «Перечень морских портов Российской Федерации, в
  которых осуществляет деятельность ФГУП «Росморпорт»» (basin, branch, port) and
  the branch pages with a section per port: registration number in the Register
  of seaports and capacities as published;
- the territory directory (cities) and the national OSM pass (city outlines by
  ``wikidata`` tag, port areas ``landuse=harbour|port``, ``industrial=port``).

Rules:

- the basis of a node is function: a registered seaport where Rosmorport works;
  capacities are kept as published text with the number read from it in
  thousands of tonnes or TEU per year («млн» ×1000, a bare number ÷1000) — the
  text wins over plausibility; «нет» is kept as stated, an empty label is unknown;
- the port belongs to the directory city of the same name (the Register names a
  port after its place; «Большой порт» / «Пассажирский порт» Санкт-Петербург are
  Saint Petersburg); a name shared by several cities or no city is not assigned;
- the location is the representative point of the largest OSM port area inside
  the city outline; other port areas there are listed with it;
- a port is accepted with a registration number, a city and a location;
  otherwise it is kept quarantined with its reasons;
- the stevedore list is hidden in the page markup on most branch pages: only its
  size is kept, the names are not used (see ``freight``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
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
from .freight import COMMENT, _lines, parse_seaport
from .manifest import Identifier, load_dataset_manifest
from .osm import file_sha256
from .paths import data_dir, processed_dir, raw_dir

RULES_VERSION = "ru-seaports-rules-v1"
LIST_PAGE = "services_seaports.html"
LIST_TITLE = "Перечень морских портов Российской Федерации"
PORT_HEADING = re.compile(r'item-info-only-title "><a>Морской порт ([^<]+)</a>')
CITY_PREFIXES = ("большой порт ", "пассажирский порт ")
TOTAL = re.compile(r"Пропускная способность грузовых терминалов всего\s*:?\s*(.*)$")
NUMBER = re.compile(r"(\d[\d\s]*(?:[.,]\d+)?)\s*(млн|тыс)?\.?\s*(.*)", re.I)
# values are stored in thousands: «млн» ×1000, «тыс.» ×1, a bare number ÷1000
SCALE = {"млн": 1000.0, "тыс": 1.0, None: 0.001}
TRANSLIT = {
    **dict(zip("абвгдеёзийклмнопрстуфыэ", "abvgdeeziyklmnoprstufye", strict=True)),
    **{"ж": "zh", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ю": "yu", "я": "ya"},
    **{"ъ": "", "ь": ""},
}
Status = Literal["accepted", "quarantined"]


class Capacity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    value: float | None
    unit: Literal["thousand_tonnes_per_year", "thousand_teu_per_year"] | None
    stated_none: bool


class Seaport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: Identifier
    kind: Literal["port"]
    name: str
    basin: str
    branch: str
    registration_number: str | None
    capacities: dict[str, Capacity]
    operator_list_published: bool | None
    operator_list_size: int | None
    territory_ids: list[Identifier]
    assignment: Literal["city_name", "ambiguous_name", "no_directory_city"]
    lon: float | None = Field(default=None, ge=-180, le=180)
    lat: float | None = Field(default=None, ge=-90, le=90)
    location_basis: Literal["osm_port_area_in_city"] | None
    osm_port_areas: list[str]
    port_area_km2: float | None
    disputed: bool
    status: Status
    quarantine_reasons: list[str]


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", name.lower().replace("ё", "е")).strip()


def slug(name: str) -> str:
    text = "".join(TRANSLIT.get(c, c) for c in _norm(name))
    return re.sub(r"[^a-z0-9]+", "-", text.replace("-", " ")).strip("-")


def roster(page: str) -> list[tuple[str, str, str]]:
    """(basin, branch, port) rows of the visible national list."""

    lines = _lines(COMMENT.sub(" ", re.sub(r"<(script|style).*?</\1>", " ", page, flags=re.S)))
    start = next(i for i, line in enumerate(lines) if line.startswith(LIST_TITLE))
    rows: list[tuple[str, str, str]] = []
    basin = branch = ""
    for line in lines[start + 1 :]:
        if "БАССЕЙН" in line and line == line.upper():
            basin = line
        elif "филиал" in line.lower():
            branch = line
        elif basin and branch and line[:1].isupper() and len(line) < 40:
            if line == "Морские порты":  # the site menu after the list
                break
            rows.append((basin, branch, line))
        else:
            break
    return rows


def capacity(text: str) -> Capacity:
    clean = text.strip().rstrip(";").strip()
    if re.match(r"^нет\b", clean, re.I):
        return Capacity(text=text, value=None, unit=None, stated_none=True)
    found = NUMBER.search(clean)
    value: float | None = None
    unit: Literal["thousand_tonnes_per_year", "thousand_teu_per_year"] | None = None
    if found:
        rest = found.group(3).lower()
        if "тонн" in rest:
            unit = "thousand_tonnes_per_year"
        elif re.search(r"teu|дфэ|двадцатифутов", rest):
            unit = "thousand_teu_per_year"
        if unit:
            number = float(re.sub(r"\s", "", found.group(1)).replace(",", "."))
            scale = SCALE[found.group(2).lower() if found.group(2) else None]
            value = round(number * scale, 6)
    return Capacity(text=text, value=value, unit=unit, stated_none=False)


def _total(page: str, port: str) -> str | None:
    start = page.find(f"Морской порт {port}")
    end = page.find("Морской порт ", start + 20)
    lines = _lines(COMMENT.sub(" ", page[start : end if end > 0 else len(page)]))
    for index, line in enumerate(lines):
        found = TOTAL.search(line.lstrip("• "))
        if found:
            value = found.group(1).strip()
            following = lines[index + 1] if index + 1 < len(lines) else ""
            if not value and re.search(r"\d", following) and not following.endswith(":"):
                value = following
            return value or None
    return None


def port_records(raw_zip: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Roster rows joined with their branch page sections."""

    with zipfile.ZipFile(raw_zip) as archive:
        pages = {n: archive.read(n).decode("utf-8", errors="strict") for n in archive.namelist()}
    rows = roster(pages[LIST_PAGE])
    sections: dict[str, tuple[str, str]] = {}
    for name, page in pages.items():
        if name == LIST_PAGE:
            continue
        for port in PORT_HEADING.findall(page):
            sections[_norm(port)] = (port, page)
    records = []
    for basin, branch, port in rows:
        found = sections.get(_norm(port))
        record: dict[str, Any] = {"basin": basin, "branch": branch, "port": port, "section": None}
        if found:
            heading, page = found
            seaport = parse_seaport(page, heading)
            caps: dict[str, Any] = {
                key: seaport.facts[key]
                for key in ("liquid_capacity", "dry_capacity", "container_capacity")
                if key in seaport.facts
            }
            total = _total(page, heading)
            if total:
                caps["total_capacity"] = total
            record["section"] = {
                "registration_number": seaport.registration_number,
                "capacities": caps,
                "operators_published": seaport.operators_published,
                "operators": len(seaport.operators),
            }
        records.append(record)
    listed = {_norm(r[2]) for r in rows}
    report = {
        "listed": len(rows),
        "with_section": sum(1 for r in records if r["section"]),
        "sections_not_listed": sorted(s[0] for k, s in sections.items() if k not in listed),
    }
    return records, report


def build_ports(  # noqa: C901
    raw_zip: Path, national_dir: Path, directory: list[territories.TerritoryEntry]
) -> tuple[list[Seaport], dict[str, Any]]:
    from pyproj import Geod
    from shapely import wkb as shapely_wkb  # type: ignore[import-untyped]
    from shapely.ops import unary_union  # type: ignore[import-untyped]

    from .osm_national import load_table

    records, report = port_records(raw_zip)
    cities = [e for e in directory if e.level == "city"]
    by_name: dict[str, list[territories.TerritoryEntry]] = {}
    for entry in cities:
        by_name.setdefault(_norm(entry.name), []).append(entry)
    outlines: dict[str, list[Any]] = {}
    for row in load_table(national_dir, "city_boundaries"):
        outlines.setdefault(row["city_wikidata"], []).append(shapely_wkb.loads(row["wkb"]))
    areas = [
        (row["osm_ref"], shapely_wkb.loads(row["wkb"]))
        for row in load_table(national_dir, "port_areas")
    ]
    geod = Geod(ellps="WGS84")
    ports: list[Seaport] = []
    for record in records:
        name = _norm(record["port"])
        for prefix in CITY_PREFIXES:
            name = name.removeprefix(prefix)
        matches = by_name.get(name, [])
        assignment: Literal["city_name", "ambiguous_name", "no_directory_city"] = (
            "city_name"
            if len(matches) == 1
            else "ambiguous_name"
            if matches
            else "no_directory_city"
        )
        city: territories.TerritoryEntry | None = matches[0] if len(matches) == 1 else None
        reasons: list[str] = []
        section = record["section"]
        if section is None:
            reasons.append("NO_PORT_SECTION")
        elif not section["registration_number"]:
            reasons.append("NO_REGISTRATION_NUMBER")
        if city is None:
            reasons.append("CITY_NOT_ASSIGNED")
        inside: list[tuple[str, Any, float]] = []
        if city is not None:
            outline = outlines.get(city.wikidata_id)
            if not outline:
                reasons.append("NO_CITY_OUTLINE")
            else:
                shape = unary_union(outline)
                for ref, polygon in areas:
                    if shape.contains(polygon.representative_point()):
                        inside.append((ref, polygon, abs(geod.geometry_area_perimeter(polygon)[0])))
                if not inside:
                    reasons.append("NO_PORT_AREA_IN_CITY")
        inside.sort(key=lambda item: (-item[2], item[0]))
        point = inside[0][1].representative_point() if inside else None
        caps = {k: capacity(v) for k, v in (section or {}).get("capacities", {}).items()}
        ports.append(
            Seaport(
                node_id=f"seaport-{slug(record['port'])}",
                kind="port",
                name=f"Морской порт {record['port']}",
                basin=record["basin"],
                branch=record["branch"],
                registration_number=section["registration_number"] if section else None,
                capacities=caps,
                operator_list_published=section["operators_published"] if section else None,
                operator_list_size=section["operators"] if section else None,
                territory_ids=[city.territory_id] if city else [],
                assignment=assignment,
                lon=round(point.x, 6) if point is not None else None,
                lat=round(point.y, 6) if point is not None else None,
                location_basis="osm_port_area_in_city" if point is not None else None,
                osm_port_areas=[ref for ref, _, _ in inside],
                port_area_km2=round(sum(a for _, _, a in inside) / 1e6, 3) if inside else None,
                disputed=bool(city and city.disputed),
                status="quarantined" if reasons else "accepted",
                quarantine_reasons=reasons,
            )
        )
    report["rules"] = RULES_VERSION
    report["status"] = dict(Counter(p.status for p in ports))
    report["quarantine_reasons"] = dict(Counter(r for p in ports for r in p.quarantine_reasons))
    report["not_assigned"] = sorted(p.name for p in ports if not p.territory_ids)
    report["coverage"] = {
        "port": {"status": "partial", "reason": "ROSMORPORT_SEAPORTS_ONLY"},
        "rail_terminal": {"status": "unknown", "reason": "FREIGHT_OPERATIONS_SOURCE_UNAVAILABLE"},
        "logistics_terminal": {"status": "not_collected", "reason": "NOT_COLLECTED"},
        "airport": {"status": "not_collected", "reason": "NOT_COLLECTED"},
        "pipeline_terminal": {"status": "not_collected", "reason": "NOT_COLLECTED"},
    }
    return ports, report


def _checks(ports: list[Seaport], report: dict[str, Any]) -> list[dict[str, Any]]:
    ids = Counter(p.node_id for p in ports)
    by_id = {p.node_id: p for p in ports}
    pilots = {
        "kaliningrad": by_id.get("seaport-kaliningrad"),
        "novorossiysk": by_id.get("seaport-novorossiysk"),
    }
    return [
        {
            "check_id": "node_ids_unique",
            "status": "pass" if all(c == 1 for c in ids.values()) else "fail",
            "severity": "fail",
            "details": {"duplicates": sorted(i for i, c in ids.items() if c > 1)},
        },
        {
            "check_id": "roster_read",
            "status": "pass" if report["listed"] >= 40 else "fail",
            "severity": "fail",
            "details": {"listed": report["listed"], "with_section": report["with_section"]},
        },
        {
            "check_id": "pilot_ports_accepted",
            "status": "pass"
            if all(p is not None and p.status == "accepted" for p in pilots.values())
            else "warn",
            "severity": "warn",
            "details": {k: (p.status if p else None) for k, p in pilots.items()},
        },
        {
            "check_id": "ports_quarantined",
            "status": "pass" if not report["quarantine_reasons"] else "warn",
            "severity": "warn",
            "details": report["quarantine_reasons"],
        },
        {
            "check_id": "operator_lists_published",
            "status": "pass"
            if all(p.operator_list_published is not False for p in ports)
            else "warn",
            "severity": "warn",
            "details": {
                "hidden": sorted(p.name for p in ports if p.operator_list_published is False)
            },
        },
    ]


def publish(
    rosmorport_dataset_id: str,
    osm_dataset_id: str,
    territory_directory_id: str,
    directory_id: str,
) -> Path:
    rmp = load_dataset_manifest(rosmorport_dataset_id)
    osm = load_dataset_manifest(osm_dataset_id)
    raw_zip = raw_dir() / rmp.raw_path
    if file_sha256(raw_zip) != rmp.checksum.value:
        raise ValueError(f"{rosmorport_dataset_id}: raw sha256 mismatch")
    national_dir = processed_dir() / osm_dataset_id / "national"
    meta = json.loads((national_dir / "extract_meta.json").read_text())
    if meta["source"]["sha256"] != osm.checksum.value:
        raise ValueError(f"{national_dir}: not extracted from {osm_dataset_id}")
    directory = territories.load_directory(territory_directory_id)
    ports, report = build_ports(raw_zip, national_dir, directory)
    checks = _checks(ports, report)
    failed = [c["check_id"] for c in checks if c["status"] == "fail"]
    if failed:
        raise ValueError(f"seaport directory QA failed: {failed}")
    base = data_dir() / "directories"
    target = base / directory_id
    if target.exists():
        raise FileExistsError(f"{directory_id} is published; directories are immutable")
    staging = base / f".staging-{directory_id}-{uuid.uuid4().hex}"
    staging.mkdir(parents=True)
    try:
        rows = [p.model_dump(mode="json") for p in ports]
        payload = (json.dumps(rows, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        qa = {"directory_id": directory_id, "result": "pass", "checks": checks, "summary": report}
        qa_bytes = (json.dumps(qa, ensure_ascii=False, indent=2) + "\n").encode()
        (staging / "seaports.json").write_bytes(payload)
        (staging / "qa_report.json").write_bytes(qa_bytes)
        manifest = {
            "format": "a2-ml2-seaport-directory",
            "directory_id": directory_id,
            "created_at": datetime.now(UTC).isoformat(),
            "rules": RULES_VERSION,
            "territory_directory": {
                "directory_id": territory_directory_id,
                "manifest_sha256": file_sha256(base / territory_directory_id / "manifest.json"),
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
                for m in (rmp, osm)
            ],
            "osm_national_rules": meta["rules"],
            "coverage": report["coverage"],
            "files": {
                "seaports": {
                    "path": "seaports.json",
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


def load_seaports(directory_id: str, root: Path | None = None) -> list[Seaport]:
    """Seaports of a published directory; hashes and rows are verified on every load."""

    directory = (root or data_dir() / "directories") / directory_id
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    for ref in manifest["files"].values():
        if file_sha256(directory / ref["path"]) != ref["sha256"]:
            raise ValueError(f"{directory_id}/{ref['path']}: sha256 mismatch")
    rows = json.loads((directory / "seaports.json").read_text(encoding="utf-8"))
    if len(rows) != manifest["files"]["seaports"]["rows"]:
        raise ValueError(f"{directory_id}: row count mismatch")
    return [Seaport.model_validate(row) for row in rows]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rosmorport_dataset_id")
    parser.add_argument("osm_dataset_id")
    parser.add_argument("territory_directory_id")
    parser.add_argument("directory_id")
    args = parser.parse_args()
    print(
        publish(
            args.rosmorport_dataset_id,
            args.osm_dataset_id,
            args.territory_directory_id,
            args.directory_id,
        )
    )


__all__ = ["Seaport", "build_ports", "load_seaports", "port_records", "publish"]

if __name__ == "__main__":
    main()
