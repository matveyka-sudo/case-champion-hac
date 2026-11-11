"""Novorossiysk education department (gorono.ru): municipal kindergartens.

Rule set ``gorono-kindergarten-rules-v1``. The department's page «Дошкольные
образовательные учреждения г. Новороссийска» lists its subordinate preschool
institutions (ДОУ № N); each card gives the official name and the address. The
department is the founder, so these kindergartens are municipal. Only the name
and the address are read: head names, phones, e-mails and sites are never
parsed. The «д/д» entry (a children's home) is not a kindergarten.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

SOURCE_ID = "nvr_gorono"
RULES_VERSION = "gorono-kindergarten-rules-v1"
LIST_URL = "https://gorono.ru/dou"
CARD_URL = "https://gorono.ru/dou/blank.php?nom={number}"
ENCODING = "cp1251"
SKIP = re.compile(r"^(заведующ|телефон|e-?mail|сайт|https?:|www\.)", re.I)
NAME = re.compile(r"детск\w* сад|ДОУ|дошкольн", re.I)
POSTCODE = re.compile(r"^\d{6}")


@dataclass(frozen=True)
class Kindergarten:
    number: str
    name: str
    address: str
    ownership: str

    @property
    def object_id(self) -> str:
        return f"kindergarten-gorono-{self.number}"

    def record_hash(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _segments(page: str) -> list[str]:
    text = re.sub(r"<(script|style).*?</\1>", " ", page, flags=re.S)
    parts = re.sub(r"<[^>]+>", "\n", text).split("\n")
    return [s for s in (re.sub(r"\s+", " ", html.unescape(p)).strip() for p in parts) if s]


def list_numbers(page: str) -> list[str]:
    return re.findall(r"dou/blank\.php\?nom=(\d+)", page)


def parse_card(number: str, page: str) -> Kindergarten | None:
    segments = [s for s in _segments(page) if not SKIP.search(s)]
    name = next((s for s in segments if NAME.search(s)), None)
    address = None
    for segment in segments:
        if segment.lower().startswith("адрес"):
            address = segment.split(":", 1)[-1].strip()
            break
        if POSTCODE.match(segment):
            address = segment
            break
    if name is None or not address:
        return None
    municipal = re.search(r"\bМ[АБК]?ДОУ\b|муниципальн", name, re.I)
    return Kindergarten(
        number=number,
        name=name,
        address=address,
        ownership="municipal" if municipal else "unknown",
    )


def load(raw_zip: Path) -> tuple[list[Kindergarten], dict[str, Any]]:
    with zipfile.ZipFile(raw_zip) as archive:
        listed = list_numbers(archive.read("dou-list.html").decode(ENCODING, errors="strict"))
        records: list[Kindergarten] = []
        unreadable: list[str] = []
        for number in listed:
            name = f"blank-{number}.html"
            if name not in archive.namelist():
                unreadable.append(number)
                continue
            record = parse_card(number, archive.read(name).decode(ENCODING, errors="strict"))
            if record is None:
                unreadable.append(number)
            else:
                records.append(record)
    stats = {
        "rules": RULES_VERSION,
        "listed": len(listed),
        "parsed": len(records),
        "unreadable_cards": unreadable,
        "not_municipal_by_name": [r.number for r in records if r.ownership != "municipal"],
    }
    return records, stats
