"""First physical departures admitting a complete boundary-to-boundary chain."""

from datetime import datetime

from a2_engine.intercity.analysis.models import IntercityFrequency
from a2_engine.routing.scheduled import _ScheduledSearchWithDiagnostics


def _category(modes: tuple[str, ...]) -> str:
    return modes[0] if len(modes) == 1 else "mixed"


def departure_frequencies(
    search: _ScheduledSearchWithDiagnostics,
    physical_ids: dict[tuple[str, int], str],
    categories: tuple[str, ...],
    start_at: datetime,
    end_at: datetime,
    *,
    modes_complete: bool,
) -> tuple[IntercityFrequency, ...]:
    nonpositive_categories = {
        _category(modes)
        for first_at, modes in search.nonpositive_completions
        if start_at <= first_at < end_at
    }
    if nonpositive_categories:
        nonpositive_categories.add("public")
    global_nonpositive = "NONPOSITIVE_PASSENGER_PATH" in search.global_reasons
    globally_incomplete = bool(search.global_reasons) or not modes_complete
    results = []
    for category in categories + ("public",):
        selected = tuple(
            c
            for c in search.result.candidates
            if (category == "public" or _category(c.modes) == category)
            and start_at <= c.path.boardings[0][2] < end_at
        )
        ids = [physical_ids.get((c.path.boardings[0][0], c.path.boardings[0][1])) for c in selected]
        known = tuple(sorted({value for value in ids if value is not None}))
        reasons = set()
        nonpositive = global_nonpositive or category in nonpositive_categories
        if globally_incomplete or nonpositive:
            reasons.add("FREQUENCY_SEARCH_INCOMPLETE")
        if nonpositive:
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
