"""Moscow district indices and address walks from the address-access catalogue."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from a2_api.adapters import address_access as access
from a2_api.assistant.consultant.facts import whole
from a2_api.db.repositories.address_catalog import catalogue_path

ADDRESS_LABELS = {
    "hospital": "больница",
    "school": "школа",
    "kindergarten": "детский сад",
    "college": "колледж",
    "university": "вуз",
    "metro": "метро",
    "mcd": "МЦД",
    "mck": "МЦК",
}
ADDRESS_NEAREST = {
    "hospital": "ближайшей больницы",
    "school": "ближайшей школы",
    "kindergarten": "ближайшего детского сада",
    "college": "ближайшего колледжа",
    "university": "ближайшего вуза",
    "metro": "ближайшего метро",
    "mcd": "ближайшей станции МЦД",
    "mck": "ближайшей станции МЦК",
}


@dataclass(frozen=True)
class DistrictFact:
    district_id: str
    name: str
    index: float | None
    categories: dict[str, float | None]
    population: float | None
    municipal: bool
    okrug: bool = False

    @property
    def value(self) -> float | None:
        if self.index is not None:
            return self.index
        known = [v for v in self.categories.values() if v is not None]
        return sum(known) / len(known) if known else None

    def line(self) -> str:
        value = self.value
        text = f"{self.name}: {'нет данных' if value is None else whole(value)}"
        known = {k: v for k, v in self.categories.items() if v is not None}
        if known:
            text += (
                " ("
                + ", ".join(f"{ADDRESS_LABELS.get(k, k)} {whole(v)}" for k, v in known.items())
                + ")"
            )
        return text


_cache: dict[Path, tuple[float, list[DistrictFact]]] = {}


def districts() -> list[DistrictFact]:
    """All district reports, cached by file time; empty when the catalogue is absent."""
    path = catalogue_path().with_name("districts.json")
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return []
    cached = _cache.get(path)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    try:
        reports: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    facts = []
    for district_id, report in reports.items():
        if not isinstance(report, dict):
            continue
        name = (report.get("district") or {}).get("name") or district_id
        categories = {
            str(k): (float(v) if isinstance(v, (int, float)) else None)
            for k, v in (report.get("categories") or {}).items()
        }
        index = report.get("index")
        facts.append(
            DistrictFact(
                district_id=district_id,
                name=str(name),
                index=float(index) if isinstance(index, (int, float)) else None,
                categories=categories,
                population=report.get("population"),
                municipal=not district_id.startswith("moscow"),
                okrug="административный округ" in str(name).lower(),
            )
        )
    facts.sort(key=lambda item: item.name)
    _cache[path] = (stamp, facts)
    return facts


_STOP = {
    "ул",
    "улица",
    "д",
    "дом",
    "г",
    "город",
    "от",
    "с",
    "со",
    "до",
    "у",
    "в",
    "во",
    "на",
    "из",
    "к",
    "по",
    "адресу",
    "адрес",
    "около",
    "возле",
    "рядом",
    "для",
    "мой",
    "моего",
    "моем",
    "моём",
    "живу",
    "дома",
    "сколько",
    "идти",
    "минут",
    "мин",
    "ближайшей",
    "ближайшего",
    "за",
    "и",
    "а",
    "или",
    "как",
    "где",
    "это",
    "мне",
    "я",
}
_HOUSE = re.compile(r"^\d+[а-яa-z]?(?:[/к-]\d+[а-яa-z]?)?$")


def address_query(question: str) -> str | None:
    """'сколько идти до школы от ул. Тверская, д. 7' -> 'тверская 7'."""
    words = re.findall(r"[0-9a-zа-яё/\-]+", question.lower().replace("ё", "е"))
    for position, word in enumerate(words):
        if not _HOUSE.match(word) or position == 0:
            continue
        before: list[str] = []
        for previous in reversed(words[max(0, position - 4) : position]):
            if previous in _STOP or previous.isdigit():
                if before:
                    break
                continue
            before.insert(0, previous)
        if before and not any(
            w in {"минут", "мин", "км", "метров"} for w in words[position + 1 : position + 2]
        ):
            return " ".join(before + [word])
    return None


def walk_from_address(question: str, categories: list[str]) -> access.AddressResult | None:
    """Pedestrian times from an address named in the question; None when not found."""
    query = address_query(question)
    if query is None:
        return None
    try:
        found = access.address_search(query)
    except Exception:
        return None
    if not found:
        return None
    try:
        request = access.AddressRequest.model_validate(
            {"address_id": found[0].id, "categories": categories or list(access.CATEGORIES)}
        )
        return access.address_result(request)
    except Exception:
        return None


def address_text(result: access.AddressResult) -> str:
    lines = [f"Пешая доступность от адреса «{result.address.label}»:"]
    for item in result.categories:
        label = ADDRESS_LABELS.get(item.category, item.category)
        if item.seconds is None:
            lines.append(f"- {label}: нет данных")
            continue
        text = f"- {label}: {whole(item.seconds / 60)} мин пешком"
        if item.destination is not None:
            text += f" ({item.destination.name})"
        if item.index is not None:
            text += f", индекс {whole(item.index)}"
        lines.append(text)
    if result.index is not None:
        lines.append(f"Общий индекс адреса: {whole(result.index)} из 100.")
    return "\n".join(lines)
