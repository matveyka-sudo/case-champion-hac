"""Frequency completeness follows the first boarding period and chain mode set."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta

import pytest
from a2_engine import (
    IntercityPassengerAnalysis,
    PhysicalService,
    Timetable,
    TimetableTransfer,
    analyze_intercity_passenger,
    build_reference,
    load_intercity_passenger_rules,
    load_passenger_config,
)
from test_intercity_analysis import BINDINGS, PROOF, START, _demo, event, run, services, table

NONPOSITIVE = "NONPOSITIVE_PASSENGER_PATH"
INCOMPLETE = "FREQUENCY_SEARCH_INCOMPLETE"


def analyze_period(
    timetable: Timetable,
    start: int,
    end: int,
    *,
    physical: tuple[PhysicalService, ...] | None = None,
    resource_limit: int | None = None,
    reverse: bool = False,
) -> IntercityPassengerAnalysis:
    scope, _ = _demo.demo_inputs()
    graph = _demo.demo_reference(scope)
    if reverse:
        context = scope.comparison_scope
        scope = replace(
            scope,
            comparison_scope=replace(
                context,
                origin_territory_id=context.destination_territory_id,
                destination_territory_id=context.origin_territory_id,
                origin_node_ids=context.destination_node_ids,
                destination_node_ids=context.origin_node_ids,
            ),
        )
        graph = replace(
            graph,
            states=tuple(
                replace(state, node_id={"A": "B", "B": "A"}[state.node_id])
                for state in graph.states
            ),
        )
    return analyze_intercity_passenger(
        scope,
        START,
        timetable,
        BINDINGS,
        services(timetable) if physical is None else physical,
        build_reference(graph, scope.comparison_scope),
        config=load_passenger_config(),
        rules=load_intercity_passenger_rules(),
        frequency_start_at=START + timedelta(seconds=start),
        frequency_end_at=START + timedelta(seconds=end),
        resource_limit=resource_limit,
    )


@pytest.mark.parametrize(
    ("start", "end", "air_ids", "rail_count", "public_count"),
    [
        (3600, 14400, ("physical:valid-air:0",), 0, 1),
        (600, 14400, ("physical:valid-air:0",), None, None),
        (0, 600, (), 0, 0),
    ],
)
def test_frequency_reuses_the_frontier_with_half_open_period_and_mode(
    start: int,
    end: int,
    air_ids: tuple[str, ...],
    rail_count: int | None,
    public_count: int | None,
) -> None:
    timetable = table(
        (run("zero-rail", "A", 600, "B", 600), run("valid-air", "A", 7200, "B", 10800, "flight"))
    )
    result = analyze_period(timetable, start, end)
    assert result.frequency_search is result.search
    assert result.search.reasons == (NONPOSITIVE,)
    assert result.reasons == (NONPOSITIVE,)
    assert not result.public.optimum_proven and not result.timetable_complete
    assert [route.path_seconds for route in result.candidates] == [3600]
    expected = {
        "air": (len(air_ids), air_ids),
        "rail": (rail_count, ()),
        "road": (0, ()),
        "mixed": (0, ()),
        "public": (public_count, air_ids),
    }
    for row in result.frequency:
        assert (row.count, row.known_departure_ids) == expected[row.category]
        assert row.complete is (row.count is not None)
        assert row.reasons == ((INCOMPLETE, NONPOSITIVE) if row.count is None else ())


def test_malformed_mixed_completion_keeps_later_valid_departure_identity() -> None:
    first = run("first", "A", 600, "X", 600)
    onward = run("onward", "X", 600, "B", 600, "flight")
    onward = replace(
        onward,
        events=(onward.events[0], event("B", 600, seq=1, pickup=False), event("B", 1200, seq=2)),
    )
    transfer = TimetableTransfer("minimum", "X", "X", 0, True, PROOF)
    result = analyze_period(table((first, onward), transfers=(transfer,)), 600, 601)

    assert [(route.category, route.path_seconds) for route in result.candidates] == [("mixed", 600)]
    for row in result.frequency:
        if row.category in {"mixed", "public"}:
            assert row.known_departure_ids == ("physical:first:0",)
            assert not row.complete and row.count is None
            assert row.reasons == (INCOMPLETE, NONPOSITIVE)
        else:
            assert row.complete and row.count == 0 and not row.reasons


def test_independent_frequency_search_uses_its_own_malformed_completions() -> None:
    late = 8 * 86400
    timetable = table(
        (
            run("zero-rail", "A", late, "B", late),
            run("late-air", "A", late, "B", late + 3600, "flight"),
        )
    )
    result = analyze_period(timetable, late, late + 3600)
    assert result.connection_state == "confirmed_absent" and not result.reasons
    assert result.frequency_search is not result.search
    assert result.frequency_search.reasons == (NONPOSITIVE,)
    rows = {row.category: row for row in result.frequency}
    assert rows["air"].count == 1 and rows["air"].known_departure_ids == ("physical:late-air:0",)
    assert rows["rail"].count is None and rows["rail"].reasons == (INCOMPLETE, NONPOSITIVE)
    assert rows["public"].count is None and rows["public"].reasons == (INCOMPLETE, NONPOSITIVE)


def test_global_incompleteness_and_unknown_identity_stay_conservative() -> None:
    bad = run("zero-rail", "A", 600, "B", 600)
    good = run("valid-air", "A", 7200, "B", 10800, "flight")
    timetable = table((bad, good))
    for global_limit in ("SOURCE_INCOMPLETE", NONPOSITIVE):
        limited_table = replace(
            timetable, scope=replace(timetable.scope, limitations=(global_limit,))
        )
        limited = analyze_period(limited_table, 3600, 14400)
        assert all(row.count is None for row in limited.frequency)
        expected_reasons = (
            (INCOMPLETE, NONPOSITIVE) if global_limit == NONPOSITIVE else (INCOMPLETE,)
        )
        assert all(row.reasons == expected_reasons for row in limited.frequency)

    interrupted = analyze_period(timetable, 3600, 14400, resource_limit=3)
    assert interrupted.search.interrupted
    assert all(row.count is None and row.reasons == (INCOMPLETE,) for row in interrupted.frequency)

    unknown = analyze_period(timetable, 3600, 14400, physical=services(table((bad,))))
    for row in unknown.frequency:
        if row.category in {"air", "public"}:
            assert row.count is None and row.reasons == ("PHYSICAL_DEPARTURE_IDENTITY_UNKNOWN",)
        else:
            assert row.count == 0 and row.complete


def test_concurrent_directions_use_their_own_completion_diagnostics() -> None:
    timetable = table(
        (run("zero-rail", "A", 600, "B", 600), run("reverse-air", "B", 600, "A", 1200, "flight"))
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        forward = executor.submit(analyze_period, timetable, 600, 601)
        reverse = executor.submit(analyze_period, timetable, 600, 601, reverse=True)
        forward_rows = {row.category: row for row in forward.result().frequency}
        reverse_rows = {row.category: row for row in reverse.result().frequency}
    assert forward_rows["public"].count is None
    assert forward_rows["public"].reasons == (INCOMPLETE, NONPOSITIVE)
    assert reverse_rows["public"].count == 1 and reverse_rows["public"].complete
    assert reverse_rows["public"].known_departure_ids == ("physical:reverse-air:0",)
