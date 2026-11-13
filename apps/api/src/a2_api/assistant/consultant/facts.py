"""Plain facts about a saved analysis for the consultant: names, minutes and indices."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any

from a2_contracts.v2.assistant import ExplanationSelection
from a2_contracts.v2.results import AnalysisResult, Metric, Score

CATEGORY_LABELS = {
    "adult_polyclinic": "взрослые поликлиники",
    "child_polyclinic": "детские поликлиники",
    "kindergarten": "детские сады",
    "school": "школы",
    "college_university": "колледжи и вузы",
    "rail_terminal": "ж/д вокзалы",
    "bus_terminal": "автовокзалы",
    "airport": "аэропорты",
    "shopping_center": "торговые центры",
}
NEAREST_LABELS = {
    "adult_polyclinic": "ближайшей взрослой поликлиники",
    "child_polyclinic": "ближайшей детской поликлиники",
    "kindergarten": "ближайшего детского сада",
    "school": "ближайшей школы",
    "college_university": "ближайшего колледжа или вуза",
    "rail_terminal": "ближайшего ж/д вокзала",
    "bus_terminal": "ближайшего автовокзала",
    "airport": "ближайшего аэропорта",
    "shopping_center": "ближайшего торгового центра",
}
MODE_LABELS = {
    "walk": "пешком",
    "public_transport": "на общественном транспорте",
    "private_car": "на автомобиле",
    "road": "по дороге",
    "rail": "по железной дороге",
    "air": "самолётом",
    "water": "по воде",
    "mixed": "смешанным транспортом",
    "pipeline": "по трубопроводу",
}
MEASURE_LABELS = {
    "urban_h_seconds": "опорное время города",
    "minimum_journey_seconds": "время до ближайшего объекта",
    "accessible_object_count": "число доступных объектов",
    "category_index": "индекс категории",
    "point_index": "индекс точки",
    "territory_index": "индекс территории",
    "observed_route_accessibility": "оценка по найденному маршруту",
    "best_observed_time": "время найденного пути",
    "best_observed_journey_seconds": "время найденного пути",
    "urban_accessibility_index": "индекс доступности",
    "private_car_path_seconds": "время на автомобиле",
    "private_car_index": "индекс автомобильной связи",
    "path_seconds": "время в пути",
    "initial_wait_seconds": "ожидание отправления",
    "total_seconds": "полное время пути",
    "path_index": "индекс времени в пути",
    "waiting_index": "индекс ожидания",
    "passenger_index": "индекс пассажирской связи",
    "unique_physical_departures": "число отправлений",
    "accepted_dated_service_runs": "число рейсов",
    "minimum_truck_access_seconds": "время грузового доступа",
    "observed_truck_network_seconds": "время грузовика по сети",
    "cargo_site_index": "индекс грузового доступа площадки",
    "cargo_access_index": "индекс грузового доступа",
    "complete_delivery_seconds": "полное время доставки",
    "movement_seconds": "время движения груза",
    "known_movement_seconds": "известное время движения груза",
    "delivery_index": "индекс доставки",
}
KIND_LABELS = {
    "urban_passenger": "доступность по городу",
    "intercity_passenger": "пассажирская связь между городами",
    "urban_cargo": "грузовая доступность в городе",
    "intercity_cargo": "грузовая связь между городами",
    "address": "пешая доступность по адресу",
    "atlas": "атлас до расчёта, есть только районные индексы",
}


def whole(value: float) -> int:
    """Half up, as people expect: 82.5 -> 83."""
    return int(math.floor(value + 0.5))


def minutes(seconds: float | None) -> float | None:
    return None if seconds is None else seconds / 60


def fmt_minutes(value: float | None) -> str:
    if value is None:
        return "нет данных"
    if value < 1:
        return "меньше минуты"
    if value >= 90:
        hours, rest = divmod(whole(value), 60)
        return f"{hours} ч {rest} мин" if rest else f"{hours} ч"
    return f"{whole(value)} мин"


def fmt_index(value: float | None) -> str:
    return "нет данных" if value is None else f"{whole(value)}"


def band(value: float | None) -> str:
    if value is None:
        return "неизвестна"
    if value >= 80:
        return "отличная"
    if value >= 65:
        return "хорошая"
    if value >= 50:
        return "средняя"
    if value >= 30:
        return "низкая"
    return "очень низкая"


@dataclass
class CategoryFact:
    category: str
    minutes: float | None = None
    count: float | None = None
    index: float | None = None
    destination: str | None = None
    time_id: str | None = None
    route_mode: str | None = None
    count_id: str | None = None
    score_id: str | None = None

    @property
    def label(self) -> str:
        return CATEGORY_LABELS.get(self.category, self.category)


@dataclass
class PointFact:
    point_id: str
    label: str
    mode: str | None
    population: float | None = None
    index: float | None = None
    score_id: str | None = None
    location: tuple[float, float] | None = None
    categories: dict[str, CategoryFact] = field(default_factory=dict)


@dataclass
class CategorySummary:
    category: str
    index: float | None
    median_minutes: float | None
    best_minutes: float | None
    worst_minutes: float | None
    points: int

    @property
    def label(self) -> str:
        return CATEGORY_LABELS.get(self.category, self.category)


@dataclass
class Facts:
    kind: str
    place: str
    city: str
    area: str | None
    mode: str | None
    categories: list[str]
    departure: str | None
    territory_index: float | None
    territory_score_id: str | None
    h_minutes: float | None
    points: list[PointFact]
    summaries: dict[str, CategorySummary]
    selected: PointFact | None
    selected_destination: str | None
    destination_names: dict[str, str]
    other: list[tuple[str, str, str]]  # label, value, id for non-urban products

    @property
    def mode_label(self) -> str:
        return MODE_LABELS.get(self.mode or "", self.mode or "")

    def ranked_points(self) -> list[PointFact]:
        return sorted(
            (point for point in self.points if point.index is not None),
            key=lambda point: point.index or 0,
            reverse=True,
        )


def _value(item: Metric | Score) -> float | None:
    return item.value if item.status == "available" else None


def _names(service: Any, result: AnalysisResult) -> tuple[str, str | None, dict[str, str]]:
    request: Any = result.request
    registry = getattr(service, "registry", None)
    snapshots = getattr(registry, "snapshots", {}) or {}
    snapshot = snapshots.get(result.snapshot_id)
    territories = {t.territory_id: t.name for t in getattr(snapshot, "territories", []) or []}
    city_id = getattr(request, "city_id", None) or getattr(request, "origin_id", None) or ""
    city = territories.get(city_id) or city_id
    area_id = getattr(request, "area_id", None)
    area = None
    if area_id:
        option = (getattr(registry, "areas", {}) or {}).get(area_id)
        area = getattr(option, "name", None) or territories.get(area_id) or area_id
    destinations = {
        feature.entity_id: feature.name
        for feature in result.map_data.features
        if feature.entity_kind == "destination" and feature.name
    }
    return city, area, destinations


def _place(result: AnalysisResult, city: str, area: str | None, service: Any) -> str:
    request: Any = result.request
    destination_id = getattr(request, "destination_id", None)
    if destination_id:
        registry = getattr(service, "registry", None)
        snapshot = (getattr(registry, "snapshots", {}) or {}).get(result.snapshot_id)
        names = {t.territory_id: t.name for t in getattr(snapshot, "territories", []) or []}
        return f"{city} → {names.get(destination_id, destination_id)}"
    return f"{area}, {city}" if area else city


def build_facts(
    service: Any, result: AnalysisResult, selection: ExplanationSelection | None
) -> Facts:
    city, area, destination_names = _names(service, result)
    request: Any = result.request
    journeys = {journey.journey_id: journey for journey in result.journeys}
    features = result.map_data.features
    order: dict[str, int] = {}
    locations: dict[str, tuple[float, float]] = {}
    point_names: dict[str, str] = {}
    for feature in features:
        if feature.entity_kind == "sample_point":
            order.setdefault(feature.entity_id, len(order) + 1)
            geometry = feature.geometry
            if geometry is not None and geometry.type == "Point":
                lon, lat = geometry.coordinates[0], geometry.coordinates[1]
                locations[feature.entity_id] = (lat, lon)
            if feature.name:
                point_names[feature.entity_id] = feature.name

    points: dict[tuple[str, str | None], PointFact] = {}

    def point(point_id: str, mode: str | None) -> PointFact:
        key = (point_id, mode)
        if key not in points:
            number = order.setdefault(point_id, len(order) + 1)
            points[key] = PointFact(
                point_id=point_id,
                label=point_names.get(point_id) or f"точка {number}",
                mode=mode,
                location=locations.get(point_id),
            )
        return points[key]

    territory_index = None
    territory_score_id = None
    h_minutes = None
    other: list[tuple[str, str, str]] = []
    for metric in result.metrics:
        subject = metric.subject
        value = _value(metric)
        if metric.name == "urban_h_seconds":
            h_minutes = minutes(value)
            continue
        if subject.kind == "sample_point" and subject.point_id and subject.category:
            fact = point(subject.point_id, subject.mode).categories.setdefault(
                subject.category, CategoryFact(subject.category)
            )
            journey = journeys.get(metric.journey_id or "")
            observed = journey is not None and metric.unit == "seconds" and value is not None
            if metric.name == "minimum_journey_seconds" or (observed and fact.minutes is None):
                fact.minutes = minutes(value)
                fact.time_id = metric.metric_id
                if journey is not None:
                    fact.destination = destination_names.get(journey.destination_object_id or "")
                    if journey.legs and all(leg.kind == "walk" for leg in journey.legs):
                        fact.route_mode = MODE_LABELS["walk"]
                    else:
                        fact.route_mode = MODE_LABELS.get(subject.mode or "")
            elif metric.name == "accessible_object_count":
                fact.count = value
                fact.count_id = metric.metric_id
            continue
        label = MEASURE_LABELS.get(metric.name, metric.name)
        if value is None:
            text = "нет данных"
        elif metric.unit == "seconds":
            text = fmt_minutes(minutes(value))
        else:
            text = (
                f"{value:g} {metric.unit}"
                if metric.unit not in {"objects", "count"}
                else f"{value:g}"
            )
        other.append((label, text, metric.metric_id))
    for score in result.scores:
        subject = score.subject
        value = _value(score)
        if score.name == "territory_index":
            if territory_index is None or subject.mode == getattr(request, "passenger_mode", None):
                territory_index = value
                territory_score_id = score.score_id
            continue
        if subject.kind == "sample_point" and subject.point_id:
            item = point(subject.point_id, subject.mode)
            if score.name == "category_index" and subject.category:
                fact = item.categories.setdefault(subject.category, CategoryFact(subject.category))
                fact.index = value
                fact.score_id = score.score_id
            elif score.name == "point_index":
                item.index = value
                item.score_id = score.score_id
                basis = score.aggregation_basis
                item.population = getattr(basis, "population", None)
            continue
        if score.name == "observed_route_accessibility":
            continue
        label = MEASURE_LABELS.get(score.name, score.name)
        other.append((label, fmt_index(value) + " из 100", score.score_id))
    # A point's population is stored on its feature when the score has no basis.
    population = {f.entity_id: f.population for f in features if f.entity_kind == "sample_point"}
    for item in points.values():
        if item.population is None:
            item.population = population.get(item.point_id)

    categories = list(getattr(request, "categories", []) or [])
    for item in points.values():
        for category in item.categories:
            if category not in categories:
                categories.append(category)
    summaries = {}
    for category in categories:
        facts = [p.categories[category] for p in points.values() if category in p.categories]
        times = [f.minutes for f in facts if f.minutes is not None]
        weighted = [
            (f.index, p.population or 1)
            for p in points.values()
            if (f := p.categories.get(category)) is not None and f.index is not None
        ]
        index = (
            sum(value * weight for value, weight in weighted) / sum(w for _, w in weighted)
            if weighted
            else None
        )
        summaries[category] = CategorySummary(
            category=category,
            index=index,
            median_minutes=statistics.median(times) if times else None,
            best_minutes=min(times) if times else None,
            worst_minutes=max(times) if times else None,
            points=len(facts),
        )

    selected = None
    selected_destination = None
    if selection is not None:
        wanted = selection.entity_id
        if selection.entity_kind == "cell":
            wanted = next(
                (
                    f.point_id or f.entity_id
                    for f in features
                    if f.entity_kind == "cell" and f.entity_id == selection.entity_id
                ),
                wanted,
            )
        if selection.entity_kind in {"sample_point", "cell"}:
            matches = [p for (pid, _), p in points.items() if pid == wanted]
            mode = getattr(request, "passenger_mode", None)
            selected = next((p for p in matches if p.mode == mode), matches[0] if matches else None)
        elif selection.entity_kind == "destination":
            selected_destination = destination_names.get(selection.entity_id, selection.entity_id)

    mode = getattr(request, "passenger_mode", None) or getattr(request, "mode", None)
    departure = getattr(request, "departure_at", None)
    return Facts(
        kind=result.request.analysis_kind,
        place=_place(result, city, area, service),
        city=city,
        area=area,
        mode=mode,
        categories=categories,
        departure=departure.strftime("%d.%m.%Y %H:%M") if departure is not None else None,
        territory_index=territory_index,
        territory_score_id=territory_score_id,
        h_minutes=h_minutes,
        points=[p for p in points.values() if p.mode in {None, mode} or mode is None],
        summaries=summaries,
        selected=selected,
        selected_destination=selected_destination,
        destination_names=destination_names,
        other=other,
    )


def empty_facts(place: str, city: str = "Москва", kind: str = "address") -> Facts:
    """Context without a saved analysis: the address page or the atlas before a run."""
    return Facts(
        kind=kind,
        place=place,
        city=city,
        area=None,
        mode="walk",
        categories=[],
        departure=None,
        territory_index=None,
        territory_score_id=None,
        h_minutes=None,
        points=[],
        summaries={},
        selected=None,
        selected_destination=None,
        destination_names={},
        other=[],
    )


def describe(facts: Facts, districts: list[Any] | None = None, address: str | None = None) -> str:
    """Compact Russian text the model reads; ids in brackets are references."""
    lines = [
        f"Расчёт: {KIND_LABELS.get(facts.kind, facts.kind)}, территория: {facts.place}.",
    ]
    if facts.mode:
        lines.append(f"Способ передвижения: {facts.mode_label}.")
    if facts.departure:
        lines.append(f"Время отправления: {facts.departure}.")
    if facts.territory_index is not None:
        lines.append(
            f"Общий индекс территории: {fmt_index(facts.territory_index)} из 100 "
            f"({band(facts.territory_index)} доступность) [{facts.territory_score_id}]."
        )
    if facts.h_minutes is not None:
        lines.append(
            f"Опорное время города H = {fmt_minutes(facts.h_minutes)}: при времени H индекс 50."
        )
    if facts.summaries:
        lines.append("По категориям (взвешено по населению точек):")
        for summary in facts.summaries.values():
            lines.append(
                f"- {summary.label}: индекс {fmt_index(summary.index)}, обычно "
                f"{fmt_minutes(summary.median_minutes)} (от {fmt_minutes(summary.best_minutes)} "
                f"до {fmt_minutes(summary.worst_minutes)}), точек {summary.points}."
            )
    ranked = facts.ranked_points()
    if ranked:
        shown = ranked[:8] + [p for p in ranked[-8:] if p not in ranked[:8]]
        lines.append(f"Точки анализа ({len(ranked)} с индексом), лучшие и худшие:")
        for item in shown:
            lines.append("- " + _point_line(item))
    if facts.selected is not None:
        lines.append("Выбранная на карте точка: " + _point_line(facts.selected))
    if facts.selected_destination:
        lines.append(f"Выбранный на карте объект: {facts.selected_destination}.")
    if facts.other:
        lines.append("Показатели:")
        for label, value, identity in facts.other[:40]:
            lines.append(f"- {label}: {value} [{identity}]")
    if districts:
        lines.append(
            "Пешая доступность по районам и округам (индекс 0–100, по каталогу адресов; "
            "категории: больницы, школы, детсады, колледжи, вузы, метро, МЦД, МЦК):"
        )
        for item in districts:
            lines.append("- " + item.line())
    if address:
        lines.append(address)
    return "\n".join(lines)


def _point_line(item: PointFact) -> str:
    parts = [f"{item.label}: индекс {fmt_index(item.index)}"]
    if item.population:
        parts.append(f"жителей ≈ {whole(item.population)}")
    for fact in item.categories.values():
        text = f"{fact.label} — {fmt_minutes(fact.minutes)}"
        if fact.route_mode:
            text += f" {fact.route_mode}"
        if fact.destination:
            text += f" ({fact.destination})"
        if fact.count is not None:
            text += f", доступно объектов: {fact.count:g}"
        parts.append(text)
    refs = [i for i in (item.score_id,) if i]
    return "; ".join(parts) + (f" [{refs[0]}]" if refs else "")
