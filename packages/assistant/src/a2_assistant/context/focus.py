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


def context_facts(
    saved: AnalysisResult, context: ExplanationRequest | None
) -> tuple[list[Metric], list[Score]]:
    """Prioritize explicit subjects, then real component edges; never infer edges."""
    metrics, scores = focused_metrics(saved, context), focused_scores(saved, context)
    question = (context.question or "") if context is not None else ""
    compare = any(word in question.casefold() for word in ("сравн", "compare", "сопостав"))
    if compare:
        # The public request has one selection. A second entity must be named
        # explicitly by its saved ID; substring matches must not broaden scope.
        tokens = set(re.findall(r"[\w:.-]+", question))

        def add_explicit(collection: list[SavedFact], selected: list[SavedFact]) -> None:
            for item in collection:
                identifiers = item.subject.model_dump(exclude_none=True)
                if any(
                    str(value) in tokens
                    for key, value in identifiers.items()
                    if key.endswith("_id")
                ):
                    if item not in selected:
                        selected.insert(1 if selected else 0, item)

        add_explicit(saved.metrics, metrics)
        add_explicit(saved.scores, scores)
    metric_by_id = {item.metric_id: item for item in saved.metrics}
    score_by_id = {item.score_id: item for item in saved.scores}
    if any(word in question.casefold() for word in ("почему", "причин", "индекс", "оценк")):
        related_metrics: list[Metric] = []
        related_scores: list[Score] = []
        queue = list(scores[:3])
        seen: set[str] = set()
        while queue and len(seen) < 20:
            score = queue.pop(0)
            if score.score_id in seen:
                continue
            seen.add(score.score_id)
            for component in score.components:
                if component.kind == "metric" and component.id in metric_by_id:
                    item = metric_by_id[component.id]
                    if item not in related_metrics:
                        related_metrics.append(item)
                elif component.kind == "score" and component.id in score_by_id:
                    child = score_by_id[component.id]
                    if child not in related_scores:
                        related_scores.append(child)
                        queue.append(child)
        metrics = related_metrics + [item for item in metrics if item not in related_metrics]
        scores = (
            scores[:3]
            + [item for item in related_scores if item not in scores[:3]]
            + [item for item in scores[3:] if item not in related_scores]
        )
    if any(word in question.casefold() for word in ("маршрут", "путь", "участ", "цепоч")):
        metrics.sort(key=lambda item: item.journey_id is None)
    if any(word in question.casefold() for word in ("τ", "порог", "количеств", "объектов")):
        count_ids = {item.metric_id for item in saved.counts}
        counts = [item for item in saved.metrics if item.metric_id in count_ids]
        metrics = counts + [item for item in metrics if item not in counts]
    return metrics[:20], scores[:20]


def answer_intent(question: str | None) -> str:
    """Internal question plan; never a new tool, fact or public response field."""
    text = (question or "").casefold()
    if any(word in text for word in ("источник", "основан", "откуда", "документ", "методик")):
        return "sources"
    if any(word in text for word in ("улучш", "изменить", "ускор", "проверить дальше")):
        return "improvement_hypotheses"
    if any(word in text for word in ("разные", "различ", "ниже", "выше", "сравн", "сопостав")):
        return "explain_difference"
    if any(word in text for word in ("unknown", "partial", "неизвест", "нет данных", "покрыт")):
        return "clarify_unknown"
    if any(word in text for word in ("район", "рассказать", "обзор", "территори")):
        return "area_overview"
    return "explain_score"


def planned_facts(
    metrics: list[Metric], scores: list[Score], question: str | None
) -> tuple[list[Metric], list[Score]]:
    """At most three recommended atoms, drawn only from the admitted context.

    A general difference question can use two saved points only when every
    subject dimension other than point_id and the score semantics coincide.
    Explicit selections are kept first. No index or numerical delta is computed.
    """
    intent = answer_intent(question)
    if intent == "explain_difference":
        for first in scores:
            for second in scores:
                if (
                    first.score_id != second.score_id
                    and first.subject.point_id is not None
                    and second.subject.point_id is not None
                    and first.subject.point_id != second.subject.point_id
                    and first.subject.model_dump(exclude={"point_id"})
                    == second.subject.model_dump(exclude={"point_id"})
                    and first.name == second.name
                    and first.methodology_version == second.methodology_version
                    and first.calculation_kind == second.calculation_kind
                ):
                    return [], [first, second]
        for first_metric in metrics:
            for second_metric in metrics:
                if (
                    first_metric.metric_id != second_metric.metric_id
                    and first_metric.subject.point_id is not None
                    and second_metric.subject.point_id is not None
                    and first_metric.subject.point_id != second_metric.subject.point_id
                    and first_metric.subject.model_dump(exclude={"point_id"})
                    == second_metric.subject.model_dump(exclude={"point_id"})
                    and first_metric.name == second_metric.name
                    and first_metric.unit == second_metric.unit
                    and first_metric.time_kind == second_metric.time_kind
                ):
                    return [first_metric, second_metric], []
        # Do not invent a comparator, even when the user presupposes a difference.
        return ([], scores[:1]) if scores else (metrics[:1], [])
    if scores:
        first = scores[0]
        component_ids = {c.id for c in first.components if c.kind == "metric"}
        components = [m for m in metrics if m.metric_id in component_ids]
        return components[:2], [first]
    return metrics[:3], []
