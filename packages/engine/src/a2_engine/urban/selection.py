"""Minimum supplied admissible time per physical institution and category."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from a2_engine.urban.journeys import UrbanJourneyTiming
from a2_engine.urban.models import UrbanDestination


@dataclass(frozen=True)
class SelectedUrbanObject:
    destination: UrbanDestination
    journey: UrbanJourneyTiming


def _selection_key(item: SelectedUrbanObject) -> tuple[float, str, str, str]:
    return (
        item.journey.total_seconds,
        item.destination.object_identity,
        item.destination.destination_id,
        item.journey.candidate.journey_id,
    )


def select_urban_objects(
    journeys: Sequence[UrbanJourneyTiming],
    destinations: Mapping[str, UrbanDestination],
    *,
    point_id: str,
    category: str,
    passenger_mode: str,
) -> tuple[SelectedUrbanObject, ...]:
    best: dict[str, SelectedUrbanObject] = {}
    for journey in journeys:
        candidate = journey.candidate
        if (candidate.point_id, candidate.category, candidate.passenger_mode) != (
            point_id,
            category,
            passenger_mode,
        ) or not journey.admissible:
            continue
        destination = destinations[candidate.destination_id]
        if not destination.in_service_scope or destination.eligible is not True:
            continue
        item = SelectedUrbanObject(destination, journey)
        previous = best.get(destination.object_identity)
        if previous is None or _selection_key(item) < _selection_key(previous):
            best[destination.object_identity] = item
    return tuple(sorted(best.values(), key=_selection_key))
