"""Territorial register of medical organisations (ТРМО) of the Kaliningrad OMS fund.

One row is a structural subdivision with its address of care. The register gives
the organisation's legal form (ОКОПФ) and form of ownership (ОКФС), so public
ownership is read from an official register, not inferred from OSM.

Rule set ``trmo-polyclinic-rules-v1`` (PRODUCT_SPEC §2):

- the row is not excluded from the register;
- care is outpatient («Амбулаторно») and includes primary physician care
  («первичная врачебная медико-санитарная помощь»);
- the subdivision name contains «поликлин» and not «консультатив»;
- ownership is regional (ОКФС 13) or municipal (ОКФС 14); federal departmental
  polyclinics (ОКФС 12, e.g. МВД) serve their staff and are not city polyclinics;
- «детск» in the name → child polyclinic; otherwise adult; a polyclinic without
  «детск» whose profiles include both therapy and paediatrics is in both.

Subdivisions of one category at one address are one physical object.
Requires ``openpyxl`` for reading (see README).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

SOURCE_ID = "tfoms39_trmo"
RULES_VERSION = "trmo-polyclinic-rules-v1"
PUBLIC_OKFS = {"13": "regional", "14": "municipal"}
COLUMNS = {
    "org_code": 4,
    "org_name": 5,
    "org_short": 6,
    "inn": 7,
    "ogrn": 9,
    "okopf_code": 12,
    "okopf_name": 13,
    "okfs_code": 14,
    "okfs_name": 15,
    "address": 21,
    "address_guid": 22,
    "unit_code": 23,
    "unit_name": 25,
    "unit_oid": 26,
    "care_types": 27,
    "conditions": 28,
    "profiles": 29,
    "included": 30,
    "excluded": 31,
}


@dataclass(frozen=True)
class Unit:
    org_code: str
    org_name: str
    org_short: str
    inn: str
    ogrn: str
    okopf_code: str
    okopf_name: str
    okfs_code: str
    okfs_name: str
    address: str
    address_guid: str
    unit_code: str
    unit_name: str
    unit_oid: str
    care_types: str
    conditions: str
    profiles: str
    included: str
    excluded: str


def _text(value: Any) -> str:
    return "" if value is None else re.sub(r"\s+", " ", str(value)).strip()


def read_units(path: Path) -> list[Unit]:
    from openpyxl import load_workbook  # type: ignore[import-untyped]

    sheet = load_workbook(path, read_only=True, data_only=True).worksheets[0]
    units = []
    for row in sheet.iter_rows(min_row=4, values_only=True):
        if not row or row[0] is None:
            continue
        values = {name: _text(row[index]) for name, index in COLUMNS.items()}
        units.append(Unit(**values))
    return units


def categories(unit: Unit) -> tuple[set[str], str | None]:
    """(categories, rejection reason) under ``trmo-polyclinic-rules-v1``."""

    name = unit.unit_name.lower()
    if unit.excluded:
        return set(), "EXCLUDED_FROM_REGISTER"
    if "поликлин" not in name or "консультатив" in name:
        return set(), "NOT_A_POLYCLINIC"
    if "амбулаторно" not in unit.conditions.lower():
        return set(), "NOT_OUTPATIENT"
    if "первичная врачебная медико-санитарная помощь" not in unit.care_types.lower():
        return set(), "NO_PRIMARY_PHYSICIAN_CARE"
    if unit.okfs_code not in PUBLIC_OKFS:
        return set(), "NOT_REGIONAL_OR_MUNICIPAL"
    profiles = unit.profiles.lower()
    if "детск" in name:
        return {"child_polyclinic"}, None
    found = {"adult_polyclinic"}
    if "терап" in profiles and "педиатр" in profiles:
        found.add("child_polyclinic")
    return found, None


@dataclass(frozen=True)
class MedicalObject:
    """One physical polyclinic of one category: subdivisions sharing an address."""

    object_id: str
    category: str
    ownership: str
    address: str
    address_guid: str
    units: tuple[Unit, ...]

    @property
    def name(self) -> str:
        return self.units[0].unit_name

    def record_hash(self) -> str:
        payload = json.dumps(
            [asdict(u) for u in self.units],
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def polyclinics(units: list[Unit]) -> tuple[list[MedicalObject], dict[str, Any]]:
    grouped: dict[tuple[str, str], list[Unit]] = {}
    rejected: dict[str, int] = {}
    for unit in units:
        found, reason = categories(unit)
        if reason:
            rejected[reason] = rejected.get(reason, 0) + 1
        for category in found:
            group = (category, unit.address_guid or re.sub(r"\W+", "", unit.address.lower()))
            grouped.setdefault(group, []).append(unit)
    objects = []
    for (category, key), members in sorted(grouped.items()):
        members.sort(key=lambda u: (u.org_code, u.unit_code, u.unit_name))
        ownership = {PUBLIC_OKFS[u.okfs_code] for u in members}
        digest = hashlib.sha256(f"{category}:{key}".encode()).hexdigest()[:12]
        objects.append(
            MedicalObject(
                object_id=f"{category}-{digest}",
                category=category,
                ownership="regional" if "regional" in ownership else "municipal",
                address=members[0].address,
                address_guid=members[0].address_guid,
                units=tuple(members),
            )
        )
    stats = {
        "rules": RULES_VERSION,
        "rows": len(units),
        "rejected": dict(sorted(rejected.items())),
        "objects": {
            c: sum(1 for o in objects if o.category == c)
            for c in ("adult_polyclinic", "child_polyclinic")
        },
    }
    return objects, stats
