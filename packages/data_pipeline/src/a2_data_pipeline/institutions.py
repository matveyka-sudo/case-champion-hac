"""Join official institution rosters with OSM candidates (rule set ``registry-osm-match-v1``).

The roster is the explicit directory of public objects; OSM only adds precise
geometry and evidence. A candidate joins a roster building of the same category
when the institution numbers do not contradict each other and it is close:

- both names carry a number and the numbers are equal: within 300 m;
- the OSM object is unnamed or its name is only a type («Школа», «Детский сад»):
  within 60 m; a proper name without a number («Фикус») never matches;
- different numbers never match, whatever the distance.

Each candidate goes to its nearest qualifying building; a building may absorb
several candidates (duplicates of one object). Candidates without a building
stay quarantined: they may be private, regional or simply not confirmed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import shapely  # type: ignore[import-untyped]

from .categories import Candidate
from .geo import to_crs
from .klgd import Building

MATCH_RULES_VERSION = "registry-osm-match-v1"
SAME_NUMBER_M = 300.0
NO_NUMBER_M = 60.0
KIND_TO_CATEGORY = {"school": "school", "kindergarten": "kindergarten"}
GENERIC_WORDS = re.compile(
    r"(школа|сош|оош|гимназия|лицей|общеобразовательная|средняя|основная|начальная|детский|сад|"
    r"д/с|ясли|корпус|корп|здание|маоу|мбоу|мадоу|мбдоу|[\s\d№.,\-–—«»\"'()/]+)",
    re.IGNORECASE,
)
NUMBER_AFTER_SIGN = re.compile(r"№\s*(\d{1,3})(?!\d)")
BARE_NUMBER = re.compile(r"(?<![\d.,/-])(\d{1,3})(?![\d.,/-])")


def institution_numbers(*texts: str | None) -> set[str]:
    """Institution numbers in names: «№ 7» first, otherwise a bare number («лицей 35»)."""

    numbers: set[str] = set()
    for text in texts:
        if not text:
            continue
        signed = NUMBER_AFTER_SIGN.findall(text)
        numbers.update(str(int(n)) for n in (signed or BARE_NUMBER.findall(text)))
    return numbers


def generic_name(name: str | None) -> bool:
    """True for an unnamed object or a name made only of type words."""

    return not GENERIC_WORDS.sub("", name or "").strip()


def candidate_numbers(candidate: Candidate) -> set[str]:
    tags = candidate.tags
    return institution_numbers(
        candidate.name, tags.get("name"), tags.get("official_name"), tags.get("short_name")
    )


@dataclass
class RosterMatch:
    building: Building
    point_utm: shapely.Point
    candidates: list[Candidate] = field(default_factory=list)
    distances_m: list[float] = field(default_factory=list)


def match_roster(
    buildings: list[Building], candidates: list[Candidate], crs: str
) -> tuple[list[RosterMatch], list[Candidate], dict[str, Any]]:
    matches = [
        RosterMatch(building=b, point_utm=to_crs(shapely.Point(b.lon, b.lat), "EPSG:4326", crs))
        for b in buildings
    ]
    unmatched: list[Candidate] = []
    rejected_by_number = 0
    for candidate in candidates:
        category = candidate.category
        geometry = to_crs(candidate.geometry, "EPSG:4326", crs)
        numbers = candidate_numbers(candidate)
        best: tuple[float, int] | None = None
        for index, match in enumerate(matches):
            if KIND_TO_CATEGORY[match.building.kind] != category:
                continue
            distance = float(geometry.distance(match.point_utm))
            registry_numbers = institution_numbers(match.building.name)
            if numbers and registry_numbers:
                if not numbers & registry_numbers:
                    if distance <= SAME_NUMBER_M:
                        rejected_by_number += 1
                    continue
                limit = SAME_NUMBER_M
            elif not numbers and generic_name(candidate.name):
                limit = NO_NUMBER_M
            else:
                continue
            if distance <= limit and (best is None or (distance, index) < best):
                best = (distance, index)
        if best is None:
            unmatched.append(candidate)
            continue
        target = matches[best[1]]
        target.candidates.append(candidate)
        target.distances_m.append(best[0])
    distances = sorted(d for m in matches for d in m.distances_m)
    stats = {
        "rules": MATCH_RULES_VERSION,
        "buildings": len(matches),
        "buildings_with_osm": sum(1 for m in matches if m.candidates),
        "osm_candidates_matched": len(distances),
        "osm_candidates_unmatched": len(unmatched),
        "near_but_other_number": rejected_by_number,
        "distance_m": {
            "median": round(distances[len(distances) // 2], 1) if distances else None,
            "max": round(distances[-1], 1) if distances else None,
        },
    }
    return matches, unmatched, stats


MEDICAL_GENERIC_WORDS = re.compile(
    r"(поликлин\w*|отделени\w*|детск\w*|взросл\w*|городск\w*|больниц\w*|филиал\w*|"
    r"гбуз|гауз|ко\b|[\s\d№.,\-–—«»\"'()/]+)",
    re.IGNORECASE,
)
MEDICAL_SAME_NUMBER_M = 150.0


def match_points(
    objects: list[tuple[str, str, str, shapely.Point]],
    candidates: list[Candidate],
    crs: str,
) -> tuple[dict[str, list[Candidate]], list[Candidate]]:
    """Attach OSM candidates to located registry objects ``(object_id, category, name, point_utm)``.

    Same rules as for rosters, with medical type words: equal numbers within 150 m,
    an unnamed or type-only OSM name within 60 m, otherwise no match.
    """

    attached: dict[str, list[Candidate]] = {object_id: [] for object_id, *_ in objects}
    unmatched: list[Candidate] = []
    for candidate in candidates:
        geometry = to_crs(candidate.geometry, "EPSG:4326", crs)
        numbers = candidate_numbers(candidate)
        generic = not MEDICAL_GENERIC_WORDS.sub("", candidate.name or "").strip()
        best: tuple[float, str] | None = None
        for object_id, category, name, point in objects:
            if category != candidate.category:
                continue
            distance = float(geometry.distance(point))
            registry_numbers = institution_numbers(name)
            if numbers and registry_numbers and numbers & registry_numbers:
                limit = MEDICAL_SAME_NUMBER_M
            elif generic and not numbers:
                limit = NO_NUMBER_M
            else:
                continue
            if distance <= limit and (best is None or (distance, object_id) < best):
                best = (distance, object_id)
        if best is None:
            unmatched.append(candidate)
        else:
            attached[best[1]].append(candidate)
    return attached, unmatched
