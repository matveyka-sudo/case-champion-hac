"""Moscow OMS fund (MGFOMS): directory of medical organisations, city polyclinics.

Rule set ``mgfoms-polyclinic-rules-v1``. The directory page embeds every
organisation and filial of the Moscow OMS system (``var allItems``). An entry is
a city polyclinic when:

- its types include «ПОЛИКЛИНИКА С ПРИКРЕПЛЕНИЕМ» (residents attach to it);
- the fund's affiliation is «ДЗМ» — the Moscow Department of Health, so the
  owner is the city of Moscow, a federal subject (regional ownership);
  departmental («ВЕДОМСТВЕННЫЕ») and self-governing («САМОСТОЯТЕЛЬНО
  ХОЗЯЙСТВУЮЩИЕ») organisations are not city polyclinics;
- it is not a women's consultation.

«ДЕТСКОЕ» population makes it a child polyclinic, «ВЗРОСЛОЕ» an adult one; both
give both categories. The location is the fund's coordinate; entries of one
category at one coordinate are one object. Head names, phones and e-mails are
in the page but never read.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

SOURCE_ID = "mgfoms_med_org"
RULES_VERSION = "mgfoms-polyclinic-rules-v1"
ATTACHED = "ПОЛИКЛИНИКА С ПРИКРЕПЛЕНИЕМ"
CITY_AFFILIATION = "ДЗМ"
ITEMS_MARKER = "var allItems = "
WOMEN = re.compile(r"^\W*женская\s+консультация", re.I)
DEPARTMENT = re.compile(r"\s+департамента\s+здравоохранения", re.I)
POPULATION = (("adult_polyclinic", "ВЗРОСЛОЕ"), ("child_polyclinic", "ДЕТСКОЕ"))


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip()


@dataclass(frozen=True)
class MedEntry:
    item_id: str  # directory element; one MO/filial code can have several buildings
    mo_code: str
    filial_code: str | None
    title: str
    affiliation: str | None
    types: tuple[str, ...]
    population: tuple[str, ...]
    address: str
    lat: float
    lon: float

    @property
    def record_id(self) -> str:
        return f"{self.mo_code}:{self.filial_code or 'head'}:{self.item_id}"

    @property
    def name(self) -> str:
        """Institution name from the first quote to «Департамента здравоохранения»."""

        start = self.title.find('"')
        text = self.title[start + 1 :] if start >= 0 else self.title
        cut = DEPARTMENT.search(text)
        text = text[: cut.start()] if cut else text.split('"')[0]
        text = re.sub(r"\s+", " ", text.replace('"', " ")).strip()
        text = re.sub(r"(?<![А-ЯЁA-Zа-яёa-z])[NН№]\s*(?=\d)", "№ ", text)
        if text.isupper():
            text = text[:1] + text[1:].lower()
        return f"{text}, филиал {self.filial_code}" if self.filial_code else text

    def record_hash(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Polyclinic:
    category: str
    ownership: str
    lat: float
    lon: float
    entries: tuple[MedEntry, ...]

    @property
    def object_id(self) -> str:
        key = ";".join(sorted(e.record_id for e in self.entries))
        digest = hashlib.sha256(f"{self.category}:{key}".encode()).hexdigest()
        return f"{self.category}-mgfoms-{digest[:12]}"

    @property
    def name(self) -> str:
        return min((e.name for e in self.entries), key=lambda n: (len(n), n))

    @property
    def address(self) -> str:
        return self.entries[0].address

    @property
    def record_id(self) -> str:
        return ";".join(sorted(e.record_id for e in self.entries))

    def record_hash(self) -> str:
        joined = ";".join(sorted(e.record_hash() for e in self.entries))
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def read_entries(page: str) -> list[MedEntry]:
    start = page.find(ITEMS_MARKER)
    if start < 0:
        raise ValueError("MGFOMS directory: allItems array not found")
    items, _ = json.JSONDecoder().raw_decode(page[start + len(ITEMS_MARKER) :])
    entries = []
    for item in items:
        if not item.get("MAP_LAT") or not item.get("MAP_LNG"):
            continue
        entries.append(
            MedEntry(
                item_id=str(item["ID"]),
                mo_code=str(item["MO_CODE"]),
                filial_code=_clean(item.get("FILIAL_CODE")).upper() or None,
                title=_clean(item.get("TITLE")),
                affiliation=item.get("PRINADL_NAME"),
                types=tuple(sorted(item.get("TIP_NAMES") or ())),
                population=tuple(sorted(item.get("NASEL_NAMES") or ())),
                address=_clean(item.get("ADDRESS")),
                lat=round(float(item["MAP_LAT"]), 7),
                lon=round(float(item["MAP_LNG"]), 7),
            )
        )
    return entries


def polyclinics(entries: list[MedEntry]) -> tuple[list[Polyclinic], dict[str, Any]]:
    skipped: dict[str, int] = {}

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    groups: dict[tuple[str, float, float], list[MedEntry]] = {}
    for entry in entries:
        if ATTACHED not in entry.types:
            skip("not_attached_polyclinic")
            continue
        if entry.affiliation != CITY_AFFILIATION:
            skip("not_city_owned")
            continue
        if WOMEN.search(entry.title):
            skip("womens_consultation")
            continue
        categories = [c for c, tag in POPULATION if tag in entry.population]
        if not categories:
            skip("no_population")
            continue
        for category in categories:
            groups.setdefault((category, entry.lat, entry.lon), []).append(entry)
    objects = [
        Polyclinic(
            category=category,
            ownership="regional",
            lat=lat,
            lon=lon,
            entries=tuple(sorted(members, key=lambda e: e.record_id)),
        )
        for (category, lat, lon), members in sorted(groups.items())
    ]
    stats = {
        "rules": RULES_VERSION,
        "entries": len(entries),
        "objects": len(objects),
        "by_category": {c: sum(1 for o in objects if o.category == c) for c, _ in POPULATION},
        "skipped_entries": skipped,
    }
    return objects, stats


def load(raw_html: Path) -> tuple[list[Polyclinic], dict[str, Any]]:
    return polyclinics(read_entries(raw_html.read_bytes().decode("utf-8", errors="strict")))
