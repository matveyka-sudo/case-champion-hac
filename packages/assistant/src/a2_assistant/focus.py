"""Bounded facts selected from saved subjects, never from model-generated IDs."""

import re
import unicodedata
from typing import TypeVar

from a2_contracts.v2.assistant import ExplanationRequest
from a2_contracts.v2.results import AnalysisResult, Metric, Score

SavedFact = TypeVar("SavedFact", Metric, Score)

# Russian words a user may ask with, per saved category or subject kind.
_TOPIC_WORDS = {
    "adult_polyclinic": "поликлиника взрослая врач больница",
    "child_polyclinic": "поликлиника детская педиатр ребенок дети",
    "kindergarten": "детский сад садик дошкольный",
    "school": "школа школьный ученик",
    "college_university": "колледж вуз университет институт студент",
    "rail_terminal": "вокзал железнодорожный поезд электричка",
    "bus_terminal": "автовокзал автобус междугородний",
    "airport": "аэропорт самолет вылет",
    "shopping_center": "торговый центр магазин покупки",
    "freight_site": "площадка склад груз",
    "freight_node": "терминал порт станция груз",
    "city_pair": "межгород поездка город пара",
    "pipeline": "трубопровод нефть газ",
}
_WORD = re.compile(r"[a-zа-я]+")


def _words(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold().replace("ё", "е")
    return {word[:5] for word in _WORD.findall(normalized) if len(word) >= 4}


def _by_question(items: list[SavedFact], question: str | None) -> list[SavedFact]:
    """Facts that share words with the question first; order otherwise kept.

    Only reorders before the bounded selection, so no saved fact is hidden by
    the question and no fact is invented from it.
    """
    asked = _words(question or "")
    if not asked:
        return items

    def overlap(item: SavedFact) -> int:
        subject = item.subject
        topics = " ".join(
            _TOPIC_WORDS.get(key or "", "") for key in (subject.category, subject.kind)
        )
        return len(asked & _words(f"{item.name} {topics}"))

    scored = [(overlap(item), index, item) for index, item in enumerate(items)]
    return [item for _, _, item in sorted(scored, key=lambda row: (-row[0], row[1]))]


def _overview(items: list[SavedFact]) -> list[SavedFact]:
    """Bounded representatives, including unavailable totals and late known facts.

    Neither input order nor a long unknown prefix may hide every computed value.
    Keep at most 80 references while scanning; no numeric aggregation is done.
    """
    groups: list[list[SavedFact]] = [[], [], [], []]
    for item in items:
        total = (
            item.subject.kind in {"territory", "city_pair", "pipeline"}
            and item.subject.category is None
        ) or getattr(item, "calculation_kind", None) == "aggregate"
        known = item.status == "available" and item.value is not None
        group_index = (0 if total else 2) + int(not known)
        if len(groups[group_index]) < 20:
            groups[group_index].append(item)
    result = []
    for position in range(20):
        for group in groups:
            if position < len(group):
                result.append(group[position])
                if len(result) == 20:
                    return result
    return result


def focused_metrics(
    saved: AnalysisResult, context: ExplanationRequest | None = None
) -> list[Metric]:
    return _focused(saved.metrics, saved, context)


def focused_scores(saved: AnalysisResult, context: ExplanationRequest | None = None) -> list[Score]:
    return _focused(saved.scores, saved, context)


def _focused(
    items: list[SavedFact], saved: AnalysisResult, context: ExplanationRequest | None
) -> list[SavedFact]:
    if context is not None and context.question:
        items = _by_question(items, context.question)
    selection = context.selection if context is not None else None
    if selection is None:
        return _overview(items)
    kind, identifier = selection.entity_kind, selection.entity_id
    point_ids = {identifier} if kind == "sample_point" else set()
    for feature in saved.map_data.features:
        if feature.entity_kind == kind and feature.entity_id == identifier:
            if feature.point_id is not None:
                point_ids.add(feature.point_id)
    journey_ids = {
        journey.journey_id
        for journey in saved.journeys
        if (kind == "destination" and journey.destination_object_id == identifier)
        or (
            kind == "access_node"
            and identifier in (journey.origin_node_id, journey.destination_node_id)
        )
        or (kind in {"cell", "sample_point"} and journey.point_id in point_ids)
    }

    def matches(metric: SavedFact) -> bool:
        subject = metric.subject
        journey_id = getattr(metric, "journey_id", None)
        if journey_id is not None and journey_id in journey_ids:
            return True
        if kind in {"cell", "sample_point"}:
            return subject.point_id is not None and subject.point_id in point_ids
        if kind == "destination":
            return subject.kind not in {"connection", "city_pair", "pipeline"} and (
                subject.destination_id == identifier
            )
        if kind == "territory":
            return subject.territory_id == identifier
        if kind == "freight_site":
            return subject.site_id == identifier
        if kind == "freight_node":
            return subject.kind == "freight_node" and subject.node_id == identifier
        return False

    # A city also matches the subjects of its points. Preserve its own total and
    # unknown coverage before the bounded list is filled by the first point rows.
    if kind == "territory":
        return _overview([item for item in items if matches(item)])
    # A selected entity with no saved metric must not silently explain another entity.
    result = []
    for metric in items:
        if matches(metric):
            result.append(metric)
            if len(result) == 20:
                break
    return result
