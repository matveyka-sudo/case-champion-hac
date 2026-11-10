"""Bounded facts selected from saved subjects, never from model-generated IDs."""

from typing import TypeVar

from a2_contracts.v2.assistant import ExplanationRequest
from a2_contracts.v2.results import AnalysisResult, Metric, Score

SavedFact = TypeVar("SavedFact", Metric, Score)


def focused_metrics(
    saved: AnalysisResult, context: ExplanationRequest | None = None
) -> list[Metric]:
    return _focused(saved.metrics, saved, context)


def focused_scores(saved: AnalysisResult, context: ExplanationRequest | None = None) -> list[Score]:
    return _focused(saved.scores, saved, context)


def _focused(
    items: list[SavedFact], saved: AnalysisResult, context: ExplanationRequest | None
) -> list[SavedFact]:
    selection = context.selection if context is not None else None
    if selection is None:
        return items[:20]
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

    # A selected entity with no saved metric must not silently explain another entity.
    result = []
    for metric in items:
        if matches(metric):
            result.append(metric)
            if len(result) == 20:
                break
    return result
