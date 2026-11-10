"""OSM candidates for the nine destination categories, rule set ``osm-category-rules-v1``.

OSM proves neither ownership nor service. Medical and education candidates stay
quarantined until an official registry confirms public ownership
(PRODUCT_SPEC §2). Rules are explicit so that every inclusion or exclusion can
be traced to a tag and a reason.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import shapely  # type: ignore[import-untyped]

RULES_VERSION = "osm-category-rules-v1"

POLYCLINIC = re.compile(r"поликлинн?ик", re.IGNORECASE)
CHILD = re.compile(r"детск", re.IGNORECASE)
NOT_GENERAL_SCHOOL = re.compile(
    r"музык|художеств|искусств|спорт|сдюсш|дюсш|танц|бальн|язык|автошкол|роллер|клуб|студи"
    r"|творчеств|раннего развит|шахмат|плаван|вокал|дизайн|програм|робот|english|school of",
    re.IGNORECASE,
)
BUS_TERMINAL = re.compile(r"автовокзал|автостанц", re.IGNORECASE)
RAIL_TERMINAL = re.compile(r"вокзал", re.IGNORECASE)
PUBLIC_HINTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bФГ[АБК]?[ОУ]{1,2}\b|федеральн", re.IGNORECASE), "federal"),
    (re.compile(r"\bГ[АБК]УЗ\b|\bГ[АБК]П?ОУ\b|\bГ[АБК]ДОУ\b|\bГ[АБК]У\b|\bГП КО\b"), "regional"),
    (re.compile(r"\bМ[АБК]Д?ОУ\b|\bМ[АБК]У\b|муниципальн", re.IGNORECASE), "municipal"),
)
OWNERSHIP_CATEGORIES = {
    "adult_polyclinic",
    "child_polyclinic",
    "kindergarten",
    "school",
    "college_university",
}


@dataclass
class Candidate:
    category: str
    osm_refs: list[str]
    name: str | None
    tags: dict[str, Any]
    point: shapely.Point  # EPSG:4326
    geometry: shapely.Geometry
    ownership_hint: str = "unknown"
    notes: list[str] = field(default_factory=list)


def _name(tags: dict[str, Any]) -> str | None:
    for key in ("name", "official_name", "short_name"):
        value = tags.get(key)
        if value:
            return str(value)
    return None


def _text(tags: dict[str, Any]) -> str:
    return " ".join(
        str(tags.get(key, "")) for key in ("name", "official_name", "short_name", "operator")
    )


def classify(tags: dict[str, Any], city_name: str | None = None) -> tuple[str | None, str]:
    """Return (category or None, rule) for one OSM feature.

    ``city_name`` enables the rail rule for main passenger stations named after
    the city (Russian naming «<Город>-<Суффикс>», e.g. «Калининград-Северный»).
    """

    if tags.get("disused") == "yes":
        return None, "disused"
    text = _text(tags)
    amenity = tags.get("amenity")
    healthcare = tags.get("healthcare")
    medical = amenity in {"clinic", "doctors", "hospital"} or healthcare in {
        "clinic",
        "centre",
        "hospital",
    }
    if medical:
        if POLYCLINIC.search(text):
            return (
                "child_polyclinic" if CHILD.search(text) else "adult_polyclinic"
            ), "medical+name:поликлиника"
        return None, "medical_not_polyclinic"
    if amenity == "kindergarten":
        return "kindergarten", "amenity=kindergarten"
    if amenity == "school":
        if NOT_GENERAL_SCHOOL.search(text):
            return None, "school_additional_education"
        return "school", "amenity=school"
    if amenity in {"college", "university"}:
        return "college_university", f"amenity={amenity}"
    if amenity == "bus_station":
        if BUS_TERMINAL.search(text):
            return "bus_terminal", "amenity=bus_station+name:автовокзал"
        return None, "bus_station_not_terminal"
    if tags.get("railway") == "station" or tags.get("building") == "train_station":
        if tags.get("station") in {"subway", "light_rail", "funicular", "monorail"}:
            return None, "urban_rail_station"
        if RAIL_TERMINAL.search(text):
            return "rail_terminal", "station+name:вокзал"
        passenger = tags.get("public_transport") == "station" and tags.get("train") == "yes"
        name = str(tags.get("name") or "")
        if passenger and city_name and name.startswith(f"{city_name}-"):
            return "rail_terminal", "passenger_station+name:<город>-"
        if passenger and city_name and name == city_name:
            return "rail_terminal", "passenger_station+name:<город>"
        return None, "rail_station_not_terminal"
    if tags.get("aeroway") == "aerodrome":
        if tags.get("iata"):
            return "airport", "aerodrome+iata"
        return None, "aerodrome_without_iata"
    if tags.get("shop") == "mall":
        return "shopping_center", "shop=mall"
    return None, "not_a_category"


def ownership_hint(tags: dict[str, Any]) -> str:
    text = _text(tags)
    for pattern, ownership in PUBLIC_HINTS:
        if pattern.search(text):
            return ownership
    return "unknown"


def _normal(name: str | None) -> str:
    return re.sub(r"[^0-9a-zа-яё]+", "", (name or "").lower().replace("поликлинник", "поликлиник"))


def build_candidates(
    rows: list[dict[str, Any]], city_name: str | None = None
) -> tuple[list[Candidate], dict[str, int]]:
    """Classify POI rows and merge duplicates of one object within a category.

    Duplicates: a node inside an area of the same category, or two features of
    the same category within 30 m with the same normalised name, or rail
    terminal features within 300 m (station node and its station building).
    """

    import shapely

    rule_counts: dict[str, int] = {}
    raw: list[Candidate] = []
    terminals: list[tuple[str, shapely.Geometry]] = []
    for row in rows:
        tags = json.loads(row["tags"])
        if tags.get("aeroway") == "terminal":
            terminals.append((row["osm_ref"], shapely.from_wkb(row["wkb"])))
        category, rule = classify(tags, city_name)
        rule_counts[rule] = rule_counts.get(rule, 0) + 1
        if category is None:
            continue
        geometry = shapely.from_wkb(row["wkb"])
        point = geometry if geometry.geom_type == "Point" else geometry.representative_point()
        raw.append(
            Candidate(
                category=category,
                osm_refs=[row["osm_ref"]],
                name=_name(tags),
                tags=tags,
                point=point,
                geometry=geometry,
                ownership_hint=ownership_hint(tags),
            )
        )
    raw.sort(key=lambda c: (c.geometry.geom_type == "Point", c.osm_refs[0]))
    merged: list[Candidate] = []
    for candidate in raw:
        target = None
        for existing in merged:
            if existing.category != candidate.category:
                continue
            if existing.geometry.geom_type != "Point" and existing.geometry.covers(candidate.point):
                target = existing
                break
            distance = _distance_m(existing.point, candidate.point)
            same_name = bool(_normal(existing.name)) and _normal(existing.name) == _normal(
                candidate.name
            )
            if (distance <= 30 and same_name) or (
                candidate.category == "rail_terminal" and distance <= 300
            ):
                target = existing
                break
        if target is None:
            merged.append(candidate)
            continue
        target.osm_refs.append(candidate.osm_refs[0])
        target.notes.append(f"merged {candidate.osm_refs[0]}")
        if target.name is None:
            target.name = candidate.name
        if target.ownership_hint == "unknown":
            target.ownership_hint = candidate.ownership_hint
    rule_counts["merged_duplicates"] = len(raw) - len(merged)
    rule_counts["airport_point_at_terminal"] = _airports_at_terminals(merged, terminals)
    return merged, rule_counts


def _airports_at_terminals(
    candidates: list[Candidate], terminals: list[tuple[str, shapely.Geometry]]
) -> int:
    """Place an airport at its largest passenger terminal inside the aerodrome polygon.

    The centre of an airfield is not where passengers arrive; without a mapped
    terminal the aerodrome's representative point stays and is noted.
    """

    moved = 0
    for candidate in candidates:
        if candidate.category != "airport" or candidate.geometry.geom_type == "Point":
            continue
        inside = [
            (geometry.area, ref, geometry)
            for ref, geometry in terminals
            if candidate.geometry.covers(geometry.representative_point())
        ]
        if not inside:
            candidate.notes.append("no terminal mapped: point inside aerodrome")
            continue
        _, ref, geometry = max(inside, key=lambda item: (item[0], item[1]))
        candidate.point = geometry.representative_point()
        candidate.osm_refs.append(ref)
        candidate.notes.append(f"point at terminal {ref}")
        moved += 1
    return moved


def _distance_m(a: shapely.Point, b: shapely.Point) -> float:
    from .geo import GEOD

    _, _, distance = GEOD.inv(a.x, a.y, b.x, b.y)
    return float(distance)
