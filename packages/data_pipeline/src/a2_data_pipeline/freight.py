"""Urban freight: physical sites and freight nodes (rule set ``freight-osm-rules-v2``).

PRODUCT_SPEC §4: access of a physical production site or warehouse to a suitable
port, freight station or terminal; equal weights of sites without volumes; no
supply chains reconstructed from legal addresses.

Nodes:
- ``port``: inside the city of a registered seaport (Rosmorport record with its
  registration number), an OSM port area (``landuse=harbour|port``,
  ``industrial=port``, ``man_made=container_terminal``), an area whose name has
  the word «порт» («Калининградский морской торговый порт»; «аэропорт» is not),
  or a feature named after a stevedoring operator of that record. Without the
  seaport record a port area is quarantined (``SEAPORT_RECORD_MISSING``). A
  feature found by an operator name alone needs a published operator list
  (v2: Rosmorport hides the list in an HTML comment, so such features stay
  quarantined with ``OPERATOR_LIST_UNPUBLISHED``).
- ``pipeline_terminal``: an oil storage or oil terminal area whose name refers to
  a trunk pipeline operator (КТК / Каспийск / Шесхарис / ПНБ).
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

RULES_VERSION = "freight-osm-rules-v2"
SITE_MIN_AREA_M2 = 5_000.0
BUILDING_MIN_AREA_M2 = 1_000.0
PIPELINE_NAME = re.compile(r"КТК|Каспийск|Шесхарис|\bПНБ\b", re.I)
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
PORT_NAME = re.compile(r"(?<![а-яё])порт(?![а-яё])", re.I)
COMMENT = re.compile(r"<!--.*?-->", re.S)
SERVICE_ITEMS = '<div class="li-three">'
CAPACITY = re.compile(r"^[-–—]\s*(наливные|сухие|контейнерные|контейнеры)\s*:\s*(.*)$")
# a value on the next line has a number or says «нет»; another label is not a value
CAPACITY_VALUE = re.compile(r"\d|^нет\b", re.I)
CAPACITY_LABEL = re.compile(r"^[-–—•]|:$")
CAPACITY_KEYS = {
    "наливные": "liquid_capacity",
    "сухие": "dry_capacity",
    "контейнерные": "container_capacity",
    "контейнеры": "container_capacity",
}
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


@dataclass(frozen=True)
class SeaportRecord:
    """One seaport section of a Rosmorport branch page.

    ``facts`` holds only text the page shows. The stevedore list may sit in an
    HTML comment: then it is read but ``operators_published`` is False, and a
    node confirmed by that list alone is not accepted.
    """

    port_name: str
    registration_number: str | None
    operators: tuple[PortOperator, ...]
    operators_published: bool
    facts: dict[str, Any]


def _quoted(name: str) -> str:
    found = re.search(r"«(.+)»", name)
    return found.group(1) if found else name


def operator_keys(name: str) -> frozenset[str]:
    """Distinctive words of an operator: «КТ НУТЭП» → {нутэп}; «Зерновой терминал «КСК»» → {кск}."""

    words = re.findall(r"[А-ЯЁA-Zа-яёa-z]+", _quoted(name).replace("«", " ").replace("»", " "))
    keys = {w.lower() for w in words if w.lower() not in OPERATOR_STOP and len(w) >= 3}
    return frozenset(keys)


def _lines(fragment: str) -> list[str]:
    lines = []
    for part in re.sub(r"<[^>]+>", "\n", fragment).split("\n"):
        text = re.sub(r"\s+", " ", html.unescape(part)).strip()
        if text:
            lines.append(text)
    return lines


def _service_list(section: str, title: str) -> tuple[list[str], bool]:
    """Items of one service list and whether the page shows them (not an HTML comment)."""

    at = section.find(f"<a>{title}</a>")
    start = section.find(SERVICE_ITEMS, at) if at >= 0 else -1
    if start < 0:
        return [], True
    start += len(SERVICE_ITEMS)
    block = section[start : section.find("</div>", start)]
    items = []
    for part in re.split(r"<br\s*/?>", block.replace("<!--", " ").replace("-->", " ")):
        text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", part))).strip(" -")
        if text:
            items.append(text)
    return items, "<!--" not in block


def parse_seaport(page: str, port_name: str) -> SeaportRecord:
    """Registration number, capacities and stevedores of one seaport on a Rosmorport page."""

    start = page.find(f"Морской порт {port_name}")
    if start < 0:
        raise ValueError(f"seaport {port_name} not found")
    end = page.find("Морской порт ", start + 20)
    section = page[start : end if end > 0 else len(page)]
    visible = _lines(COMMENT.sub(" ", section))
    names, published = _service_list(section, "Стивидорные услуги")
    registration: str | None = None
    facts: dict[str, Any] = {}
    for index, line in enumerate(visible):
        if line.startswith("Регистрационный номер") and registration is None:
            registration = next(
                (v for v in visible[index + 1 :] if not v.startswith("Регистрационный номер")),
                None,
            )
        found = CAPACITY.match(line)
        if found:
            value = found.group(2).strip()
            if not value and index + 1 < len(visible):
                following = visible[index + 1]
                if CAPACITY_VALUE.search(following) and not CAPACITY_LABEL.search(following):
                    value = following
            if value:  # an empty label is unknown, not a capacity
                facts.setdefault(CAPACITY_KEYS[found.group(1)], value)
    facts |= {
        "registration_number": registration,
        "operators_published": published,
        "mentions_ktk_berths": any("КТК" in line for line in visible),
    }
    operators = tuple(PortOperator(name=n, keys=operator_keys(n)) for n in names)
    return SeaportRecord(port_name, registration, operators, published, facts)


def load_seaport(raw_html: Path, port_name: str) -> SeaportRecord:
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
    seaport: SeaportRecord | None,
) -> tuple[list[NodeCandidate], list[SiteCandidate], dict[str, int]]:
    """Nodes and sites of ``area_utm`` (city boundary) from extracted OSM freight rows."""

    shapely.prepare(area_utm)
    operators = list(seaport.operators) if seaport else []
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
        port_named = bool(name and PORT_NAME.search(name))
        operator = _link_operator(name, operators)
        if geometry.geom_type != "Point" and (port_area or port_named or operator):
            reasons: list[str] = []
            if seaport is None:
                reasons.append("SEAPORT_RECORD_MISSING")
            elif not (port_area or port_named) and not seaport.operators_published:
                reasons.append("OPERATOR_LIST_UNPUBLISHED")
            nodes.append(
                NodeCandidate(
                    "port",
                    name,
                    [row["osm_ref"]],
                    geometry,
                    operator.name if operator else None,
                    reasons,
                    rule=(
                        "port_area"
                        if port_area
                        else ("port_name" if port_named else "operator_name")
                    ),
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
