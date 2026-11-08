"""Urban freight: physical sites and freight nodes (rule set ``freight-osm-rules-v1``).

PRODUCT_SPEC §4: access of a physical production site or warehouse to a suitable
port, freight station or terminal; equal weights of sites without volumes; no
supply chains reconstructed from legal addresses.

Nodes:
- ``port``: an OSM port area (``landuse=harbour|port``, ``industrial=port``,
  ``man_made=container_terminal``) or a feature named after a stevedoring
  operator of the official seaport record (Rosmorport). Linked operators give
  the evidence; a port area without an operator link is accepted as a port area
  of the registered seaport only when the seaport record is loaded.
- ``pipeline_terminal``: an oil storage or oil terminal area whose name refers to
  a pipeline operator (КТК / Каспийск / Шесхарис / перевалочн).
- ``rail_terminal``: mainline railway stations of the city; freight operations
  are not confirmed by an open register, so they stay quarantined.

Sites: ``landuse=industrial`` areas from 0.5 ha and standalone warehouse or
industrial buildings from 1000 m² outside them; depots, waste-water plants,
utilities and areas inside node polygons are not production sites.
Compatibility of a cargo profile with a node is not confirmed by any open
operator data: the bundle states it as unknown.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import shapely  # type: ignore[import-untyped]

from .geo import to_crs

RULES_VERSION = "freight-osm-rules-v1"
SITE_MIN_AREA_M2 = 5_000.0
BUILDING_MIN_AREA_M2 = 1_000.0
PIPELINE_NAME = re.compile(r"КТК|Каспийск|Шесхарис|перевалочн|\bПНБ\b", re.I)
# explicit operator names make an industrial area a pipeline terminal even when
# OSM mislabels the product (e.g. «ПНБ Грушовая» tagged industrial=gas)
PIPELINE_EXPLICIT = re.compile(r"КТК|Каспийск|Шесхарис|\bПНБ\b", re.I)
# the seaport record names the CPC (КТК) offshore berths of the port
CPC_NAME = re.compile(r"КТК|Каспийск", re.I)
NOT_MAINLINE = {"subway", "light_rail", "monorail", "funicular"}
NOT_PRODUCTION = re.compile(
    r"депо|очистн|водозабор|водонасос|насосн|подстанц|котельн|кладбищ|гараж|автобаз|"
    r"аэропорт|нефтебаза",
    re.I,
)
NOT_PRODUCTION_INDUSTRIAL = {"depot", "wastewater", "water", "electrical", "heating_station"}
PORT_AREA = (("landuse", "harbour"), ("landuse", "port"), ("industrial", "port"))
OPERATOR_STOP = {
    "новороссийский",
    "новороссийская",
    "морской",
    "терминал",
    "ооо",
    "ао",
    "пао",
    "оао",
    "кт",
    "комплекс",
    "завод",
    "зерновой",
    "порт",
}


@dataclass(frozen=True)
class PortOperator:
    name: str
    keys: frozenset[str]


def _quoted(name: str) -> str:
    found = re.search(r"«(.+)»", name)
    return found.group(1) if found else name


def operator_keys(name: str) -> frozenset[str]:
    """Distinctive words of an operator: «КТ НУТЭП» → {нутэп}; «Зерновой терминал «КСК»» → {кск}."""

    words = re.findall(r"[А-ЯЁA-Zа-яёa-z]+", _quoted(name).replace("«", " ").replace("»", " "))
    keys = {w.lower() for w in words if w.lower() not in OPERATOR_STOP and len(w) >= 3}
    return frozenset(keys)


def parse_seaport(page: str, port_name: str) -> tuple[list[PortOperator], dict[str, Any]]:
    """Stevedoring operators and capacities of one seaport on the Rosmorport page."""

    start = page.find(f"Морской порт {port_name}")
    if start < 0:
        raise ValueError(f"seaport {port_name} not found")
    end = page.find("Морской порт ", start + 20)
    section = page[start : end if end > 0 else len(page)]
    lines = [
        re.sub(r"\s+", " ", html.unescape(line)).strip()
        for line in re.sub(r"<[^>]+>", "\n", section).split("\n")
    ]
    lines = [line for line in lines if line and line != "-->"]
    operators: list[PortOperator] = []
    if "Стивидорные услуги" in lines:
        for line in lines[lines.index("Стивидорные услуги") + 1 :]:
            if not re.match(r"^(ПАО|ООО|АО|ОАО|ЗАО)\b", line):
                break
            name = line.rstrip("-> ").strip()
            operators.append(PortOperator(name=name, keys=operator_keys(name)))
    facts: dict[str, Any] = {}
    for index, line in enumerate(lines):
        if line.startswith("Регистрационный номер") and index + 1 < len(lines):
            facts["registration_number"] = lines[index + 1]
        if line.startswith("- наливные:") and index + 1 < len(lines):
            facts["liquid_capacity"] = lines[index + 1]
        if line.startswith("- сухие:") and index + 1 < len(lines):
            facts["dry_capacity"] = lines[index + 1]
        if line.startswith("- контейнерные:") and index + 1 < len(lines):
            facts["container_capacity"] = lines[index + 1]
    facts["mentions_ktk_berths"] = any("КТК" in line for line in lines)
    return operators, facts


def load_seaport(raw_html: Path, port_name: str) -> tuple[list[PortOperator], dict[str, Any]]:
    return parse_seaport(raw_html.read_bytes().decode("utf-8", errors="strict"), port_name)


@dataclass
class NodeCandidate:
    kind: str
    name: str | None
    osm_refs: list[str]
    geometry: shapely.Geometry  # EPSG:4326
    operator: str | None
    reasons: list[str] = field(default_factory=list)
    rule: str = ""


@dataclass
class SiteCandidate:
    kind: str  # industrial | warehouse
    name: str | None
    osm_ref: str
    geometry: shapely.Geometry  # EPSG:4326
    area_m2: float


def is_rail_station(tags: dict[str, Any]) -> bool:
    """A mainline railway station: not metro, light rail or a disused station."""

    return (
        tags.get("railway") == "station"
        and tags.get("station") not in NOT_MAINLINE
        and tags.get("disused") != "yes"
    )


def _tags(row: dict[str, Any]) -> dict[str, Any]:
    tags: dict[str, Any] = json.loads(row["tags"])
    return tags


def _link_operator(name: str | None, operators: list[PortOperator]) -> PortOperator | None:
    if not name:
        return None
    words = {w.lower() for w in re.findall(r"[А-ЯЁA-Zа-яёa-z]+", name)}
    found = [op for op in operators if op.keys and op.keys <= words]
    return found[0] if len(found) == 1 else None


def classify(
    rows: list[dict[str, Any]],
    stations: list[dict[str, Any]],
    area_utm: shapely.Geometry,
    crs: str,
    operators: list[PortOperator] | None,
) -> tuple[list[NodeCandidate], list[SiteCandidate], dict[str, int]]:
    """Nodes and sites of ``area_utm`` (city boundary) from extracted OSM freight rows."""

    shapely.prepare(area_utm)
    counts: dict[str, int] = {}
    nodes: list[NodeCandidate] = []
    industrial: list[tuple[dict[str, Any], shapely.Geometry, shapely.Geometry]] = []
    buildings: list[tuple[dict[str, Any], shapely.Geometry, shapely.Geometry]] = []

    def count(key: str) -> None:
        counts[key] = counts.get(key, 0) + 1

    for row in rows:
        tags = _tags(row)
        geometry = shapely.from_wkb(row["wkb"])
        utm = to_crs(geometry, "EPSG:4326", crs)
        if not area_utm.covers(utm.representative_point()) or tags.get("disused") == "yes":
            continue
        name = tags.get("name")
        oil = tags.get("industrial") in {"oil", "oil_storage"}
        explicit = (
            bool(name and PIPELINE_EXPLICIT.search(name)) and tags.get("landuse") == "industrial"
        )
        if (
            geometry.geom_type != "Point"
            and name
            and ((oil and PIPELINE_NAME.search(name)) or explicit)
        ):
            nodes.append(
                NodeCandidate(
                    "pipeline_terminal",
                    name,
                    [row["osm_ref"]],
                    geometry,
                    tags.get("operator"),
                    rule="oil+pipeline_name",
                )
            )
            count("pipeline_terminal")
            continue
        port_area = (
            any(tags.get(k) == v for k, v in PORT_AREA)
            or tags.get("man_made") == "container_terminal"
        )
        operator = _link_operator(name, operators or [])
        if geometry.geom_type != "Point" and (port_area or operator):
            reasons = [] if (operator or operators) else ["SEAPORT_RECORD_MISSING"]
            nodes.append(
                NodeCandidate(
                    "port",
                    name,
                    [row["osm_ref"]],
                    geometry,
                    operator.name if operator else None,
                    reasons,
                    rule="operator_name" if operator else "port_area",
                )
            )
            count("port")
            continue
        if geometry.geom_type == "Point":
            continue
        if tags.get("landuse") == "industrial":
            industrial.append((row, geometry, utm))
        elif tags.get("building") in {"warehouse", "industrial"}:
            buildings.append((row, geometry, utm))
    node_union = (
        shapely.union_all([to_crs(n.geometry, "EPSG:4326", crs) for n in nodes]) if nodes else None
    )
    sites: list[SiteCandidate] = []
    industrial_union = shapely.union_all([u for _, _, u in industrial]) if industrial else None
    if industrial_union is not None:
        shapely.prepare(industrial_union)
    for row, geometry, utm in industrial:
        tags = _tags(row)
        name = tags.get("name")
        if (name and NOT_PRODUCTION.search(name)) or tags.get(
            "industrial"
        ) in NOT_PRODUCTION_INDUSTRIAL:
            count("site_not_production")
            continue
        if node_union is not None and node_union.covers(utm.representative_point()):
            count("site_inside_node")
            continue
        if utm.area < SITE_MIN_AREA_M2:
            count("site_too_small")
            continue
        sites.append(SiteCandidate("industrial", name, row["osm_ref"], geometry, float(utm.area)))
    for row, geometry, utm in buildings:
        if utm.area < BUILDING_MIN_AREA_M2:
            count("building_too_small")
            continue
        point = utm.representative_point()
        if industrial_union is not None and industrial_union.covers(point):
            count("building_inside_industrial_site")
            continue
        if node_union is not None and node_union.covers(point):
            count("site_inside_node")
            continue
        tags = _tags(row)
        sites.append(
            SiteCandidate(
                "warehouse" if tags.get("building") == "warehouse" else "industrial",
                tags.get("name"),
                row["osm_ref"],
                geometry,
                float(utm.area),
            )
        )
    for row in stations:
        geometry = shapely.from_wkb(row["wkb"])
        utm = to_crs(geometry, "EPSG:4326", crs)
        if not area_utm.covers(utm.representative_point()):
            continue
        tags = _tags(row)
        nodes.append(
            NodeCandidate(
                "rail_terminal",
                tags.get("name"),
                [row["osm_ref"]],
                geometry,
                tags.get("operator"),
                ["FREIGHT_OPERATIONS_UNVERIFIED"],
                rule="railway_station",
            )
        )
        count("rail_terminal")
    counts["sites"] = len(sites)
    return nodes, sites, counts
