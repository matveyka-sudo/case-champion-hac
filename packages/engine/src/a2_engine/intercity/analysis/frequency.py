"""First physical departures admitting a complete boundary-to-boundary chain."""

from datetime import datetime

from a2_engine.intercity.analysis.models import IntercityFrequency
from a2_engine.routing.scheduled import ScheduledSearchResult


def departure_frequencies(
    search: ScheduledSearchResult,
    physical_ids: dict[tuple[str, int], str],
    categories: tuple[str, ...],
    start_at: datetime,
    end_at: datetime,
    *,
    modes_complete: bool,
) -> tuple[IntercityFrequency, ...]:
    results = []
    for category in categories + ("public",):
        selected = tuple(
            c
            for c in search.candidates
            if (category == "public" or (c.modes[0] if len(c.modes) == 1 else "mixed") == category)
            and start_at <= c.path.boardings[0][2] < end_at
        )
        ids = [physical_ids.get((c.path.boardings[0][0], c.path.boardings[0][1])) for c in selected]
        known = tuple(sorted({value for value in ids if value is not None}))
        reasons = set()
        if search.reasons or not modes_complete:
            reasons.add("FREQUENCY_SEARCH_INCOMPLETE")
        if "NONPOSITIVE_PASSENGER_PATH" in search.reasons:
            reasons.add("NONPOSITIVE_PASSENGER_PATH")
        if any(value is None for value in ids):
            reasons.add("PHYSICAL_DEPARTURE_IDENTITY_UNKNOWN")
        complete = not reasons
        results.append(
            IntercityFrequency(
                start_at,
                end_at,
                category,
                len(known) if complete else None,
                known,
                complete,
                tuple(sorted(reasons)),
            )
        )
    return tuple(results)
