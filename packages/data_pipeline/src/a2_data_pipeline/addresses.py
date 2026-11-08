"""Locate Russian postal addresses on OSM address data (rule set ``osm-address-match-v1``).

A registry address such as «236008, Калининградская область, г. Калининград,
А.Невского ул, д. 117-123» is reduced to a settlement, a street key and a house:

- settlement: the component before the street («г. Калининград» → калининград);
- street: lower case, ё→е, without street-type words («ул», «пр-кт», «наб» ...)
  and initials («А.»); its tokens must all occur in the OSM ``addr:street``
  tokens («улица Александра Невского» ⊇ {«невского»}); without a typed
  component the component before the house is the street;
- house: the first number with an optional letter («117-123» → 117, «1а» → 1а);
  numbers must be equal, letters too when both sides have one.

Settlement check: the city's own addresses must lie inside the city boundary;
another settlement needs an equal OSM ``addr:city`` (a street name alone is not
enough: «ул. Ленина» exists in many towns). Without an exact house, the nearest number of the
same parity on the same street (at most ±4) is used and reported as
``nearest_number``. Several OSM objects with one address collapse to the
largest building, then the smallest OSM reference.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq  # type: ignore[import-untyped]
import shapely  # type: ignore[import-untyped]

ADDRESS_RULES_VERSION = "osm-address-match-v1"
MAX_NUMBER_GAP = 4
SETTLEMENT_MIN_ADDRESSES = 50
STREET_TYPES = {
    "ул",
    "улица",
    "пр",
    "пр-кт",
    "проспект",
    "пр-т",
    "пер",
    "переулок",
    "наб",
    "набережная",
    "б-р",
    "бульвар",
    "бул",
    "пл",
    "площадь",
    "ш",
    "шоссе",
    "проезд",
    "пр-д",
    "аллея",
    "ал",
    "тупик",
    "туп",
    "линия",
    "мкр",
    "микрорайон",
    "кв-л",
    "квартал",
    "тракт",
    "спуск",
    "просек",
}
HOUSE_WORDS = re.compile(
    r"^(дом|д|здание|зд|владение|влд|строение|стр|корп|к|литера|лит)\.?\s*", re.I
)
HOUSE_COMPONENT = re.compile(r"^(дом|д|здание|зд|владение|влд)\.?\s*\d|^\d+[а-я]?(\s|$|-|/)", re.I)
ADMIN = re.compile(r"область|обл\b|район|р-н|округ|г\.о\.|муниципальн|^\d{6}$|россия", re.I)
SETTLEMENT_PREFIX = re.compile(
    r"^(г|город|п|пос|поселок|пгт|рп|с|село|д|деревня|ст|станица|х|хутор)\.?\s+", re.I
)


def _clean(text: str) -> str:
    return text.lower().replace("ё", "е")


def street_tokens(street: str) -> frozenset[str]:
    text = _clean(street)
    text = re.sub(r"\b[а-я]\.\s*", " ", text)  # initials «а.невского» → «невского»
    tokens = re.findall(r"[а-я0-9]+(?:-[а-я0-9]+)*", text)
    return frozenset(t for t in tokens if t not in STREET_TYPES and len(t) > 1)


def settlement_name(component: str) -> str | None:
    text = _clean(component).strip()
    if ADMIN.search(text):
        return None
    text = SETTLEMENT_PREFIX.sub("", text).strip(" .")
    return text or None


@dataclass(frozen=True)
class HouseKey:
    number: int
    letter: str | None

    def matches(self, other: HouseKey) -> bool:
        if self.number != other.number:
            return False
        return self.letter is None or other.letter is None or self.letter == other.letter


def house_key(text: str) -> HouseKey | None:
    found = re.search(r"(\d+)\s*([а-я])?(?![а-я])", _clean(HOUSE_WORDS.sub("", text.strip())))
    if not found:
        return None
    return HouseKey(number=int(found.group(1)), letter=found.group(2))


@dataclass(frozen=True)
class ParsedAddress:
    settlement: str | None
    street: frozenset[str]
    house: HouseKey


def _typed(part: str) -> bool:
    words = re.findall(r"[а-я-]+\.?", _clean(part))
    return any(w.rstrip(".") in STREET_TYPES for w in words) and not ADMIN.search(part)


def parse_address(address: str) -> ParsedAddress | None:
    parts = [p.strip() for p in address.split(",") if p.strip()]
    street_index = next((i for i, p in enumerate(parts) if _typed(p)), None)
    if street_index is None:
        house_index = next(
            (i for i, p in enumerate(parts) if i > 0 and HOUSE_COMPONENT.search(p.strip())), None
        )
        if house_index is None or house_index < 1 or ADMIN.search(parts[house_index - 1]):
            return None
        street_index = house_index - 1
    street = parts[street_index]
    same = re.search(r"(?:\bд\.?|\bдом|\bзд\.?)\s*\d", street, re.I)
    house = house_key(street[same.start() :]) if same else None
    if house is None and street_index + 1 < len(parts):
        house = house_key(parts[street_index + 1])
    tokens = street_tokens(street[: same.start()] if same else street)
    if not tokens or house is None:
        return None
    settlement = None
    for part in reversed(parts[:street_index]):
        settlement = settlement_name(part)
        if settlement:
            break
    return ParsedAddress(settlement=settlement, street=tokens, house=house)


@dataclass(frozen=True)
class AddressHit:
    osm_ref: str
    point: shapely.Point  # EPSG:4326
    is_building: bool
    method: str  # exact | nearest_number


@dataclass(frozen=True)
class _Entry:
    street: frozenset[str]
    house: HouseKey
    hit: AddressHit
    area: float
    in_city: bool
    city_tag: str | None


class AddressIndex:
    def __init__(
        self,
        osm_dir: Path,
        area_4326: shapely.Geometry,
        city_4326: shapely.Geometry,
        city_name: str,
    ) -> None:
        rows: list[dict[str, Any]] = pq.read_table(osm_dir / "addresses.parquet").to_pylist()
        shapely.prepare(area_4326)
        shapely.prepare(city_4326)
        self.city_name = settlement_name(city_name) or _clean(city_name)
        self.by_token: dict[str, list[_Entry]] = {}
        counts: dict[str, int] = {}
        for row in rows:
            geometry = shapely.from_wkb(row["wkb"])
            point = geometry if geometry.geom_type == "Point" else geometry.representative_point()
            if not area_4326.covers(point):
                continue
            house = house_key(str(row["housenumber"]))
            tokens = street_tokens(str(row["street"]))
            if house is None or not tokens:
                continue
            entry = _Entry(
                street=tokens,
                house=house,
                hit=AddressHit(row["osm_ref"], point, bool(row["is_building"]), "exact"),
                area=float(geometry.area),
                in_city=bool(city_4326.covers(point)),
                city_tag=settlement_name(str(row["city"])) if row["city"] else None,
            )
            if entry.city_tag:
                counts[entry.city_tag] = counts.get(entry.city_tag, 0) + 1
            for token in tokens:
                self.by_token.setdefault(token, []).append(entry)

        # settlements that substantially lie in the search area (for QA of misses)
        self.settlements = {self.city_name} | {
            name for name, count in counts.items() if count >= SETTLEMENT_MIN_ADDRESSES
        }

    def _settlement_ok(self, entry: _Entry, settlement: str | None) -> bool:
        if settlement is None:
            return False
        if settlement == self.city_name:
            return entry.in_city
        return entry.city_tag == settlement and not entry.in_city

    def locate(self, address: str) -> AddressHit | None:
        parsed = parse_address(address)
        if parsed is None:
            return None
        anchor = min(parsed.street, key=lambda t: len(self.by_token.get(t, [])))
        street = [
            e
            for e in self.by_token.get(anchor, [])
            if parsed.street <= e.street and self._settlement_ok(e, parsed.settlement)
        ]
        exact = [e for e in street if parsed.house.matches(e.house)]
        if exact:
            best = max(exact, key=lambda e: (e.hit.is_building, e.area, -_ref_order(e.hit.osm_ref)))
            return best.hit
        target = parsed.house.number
        near = [
            e
            for e in street
            if e.house.number % 2 == target % 2
            and 0 < abs(e.house.number - target) <= MAX_NUMBER_GAP
        ]
        if not near:
            return None
        best = min(
            near,
            key=lambda e: (
                abs(e.house.number - target),
                not e.hit.is_building,
                _ref_order(e.hit.osm_ref),
            ),
        )
        return AddressHit(best.hit.osm_ref, best.hit.point, best.hit.is_building, "nearest_number")


def _ref_order(ref: str) -> int:
    kind, number = ref.split("/")
    return int(number) * 3 + {"node": 0, "way": 1, "relation": 2}[kind]
