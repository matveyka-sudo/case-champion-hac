"""Novorossiysk administration (admnvrsk.ru): list of the city's healthcare institutions.

Rule set ``admnvrsk-polyclinic-rules-v1``: a table row is a city polyclinic when
its name contains «поликлиник» and not «стоматолог»; «детск» makes it a child
polyclinic, otherwise an adult one. Ownership follows the legal form in the
official name («ГБУЗ … МЗ КК» — regional). The address is the first lines of
the address cell (before contacts); a «Поликлиническое отделение … , <адрес>»
in the same cell is a separate object. Contacts are never parsed.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

SOURCE_ID = "admnvrsk_health"
RULES_VERSION = "admnvrsk-polyclinic-rules-v1"
PAGE_URL = "https://admnvrsk.ru/o-gorode/gostyam-goroda/uchrezhdenija-zdravoohranenija/"
CONTACT = re.compile(r"(электронн|e-mail|тел|приемн|приёмн|регистратур|call|горяч|факс)", re.I)
UNIT = re.compile(r"поликлиническое\s+отделение[^,]*?(?:МЗ\s*КК|\")\s*,\s*(.+)$", re.I)


@dataclass(frozen=True)
class HealthRow:
    row_number: str
    name: str
    address: str
    profile: str
    category: str
    ownership: str
    unit: str | None  # subdivision name when the row's address cell names one

    @property
    def object_id(self) -> str:
        digest = hashlib.sha256(f"{self.category}:{self.name}:{self.address}".encode()).hexdigest()
        return f"{self.category}-{digest[:12]}"

    def record_hash(self) -> str:
        payload = json.dumps(
            asdict(self), sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cells(row: str) -> list[str]:
    return re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S)


def _lines(cell: str) -> list[str]:
    parts = re.split(r"<br\s*/?>", cell)
    return [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", p))).strip() for p in parts]


def ownership(name: str) -> str:
    if re.search(r"\bГ[АБК]?УЗ\b", name):
        return "regional"
    if re.search(r"\bМ[АБК]?УЗ\b", name):
        return "municipal"
    if re.search(r"\bФГ[АБК]?УЗ\b", name):
        return "federal"
    return "private" if re.search(r"\b(ЧУЗ|ООО|АО)\b", name) else "unknown"


def parse(page: str) -> tuple[list[HealthRow], dict[str, Any]]:
    rows: list[HealthRow] = []
    skipped: dict[str, int] = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, flags=re.S):
        cells = _cells(row)
        if len(cells) < 3:
            continue
        number = _lines(cells[0])[0] if _lines(cells[0]) else ""
        name = " ".join(_lines(cells[1])).strip()
        lowered = name.lower()
        if "поликлиник" not in lowered or "стоматолог" in lowered:
            skipped["not_a_city_polyclinic"] = skipped.get("not_a_city_polyclinic", 0) + 1
            continue
        owner = ownership(name)
        if owner not in {"regional", "municipal"}:
            skipped["not_public"] = skipped.get("not_public", 0) + 1
            continue
        address_lines: list[str] = []
        for line in _lines(cells[2]):
            if not line or CONTACT.search(line):
                break
            address_lines.append(line)
        address = " ".join(address_lines)
        units: list[tuple[str | None, str]] = []
        main, _, rest = address.partition(". Поликлиническое отделение")
        units.append((None, main.strip(" .")))
        if rest:
            found = UNIT.search("Поликлиническое отделение" + rest)
            if found:
                region = ", ".join(main.split(",")[:3])  # «индекс, край, город»
                units.append(
                    (
                        "Поликлиническое отделение" + rest.split(",")[0],
                        f"{region}, {found.group(1).strip()}",
                    )
                )
        category = "child_polyclinic" if "детск" in lowered else "adult_polyclinic"
        profile = " ".join(_lines(cells[3])) if len(cells) > 3 else ""
        for unit, unit_address in units:
            rows.append(
                HealthRow(
                    row_number=number,
                    name=name,
                    address=unit_address,
                    profile=profile,
                    category=category,
                    ownership=owner,
                    unit=unit,
                )
            )
    stats = {"rules": RULES_VERSION, "objects": len(rows), "skipped_rows": skipped}
    return rows, stats


def load(raw_html: Path) -> tuple[list[HealthRow], dict[str, Any]]:
    return parse(raw_html.read_bytes().decode("utf-8", errors="strict"))
