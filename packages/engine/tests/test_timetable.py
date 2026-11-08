"""Exact timetable controls, independent tiny exhaustive oracle, urban integration."""

import random
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from a2_engine.routing.timetable import solve_timetable
from a2_engine.routing.timetable_models import (
    Timetable,
    TimetableEvent,
    TimetableFootpath,
    TimetableRun,
    TimetableScope,
    TimetableTransfer,
)
from a2_engine.shared.config import load_urban_config, load_urban_journey_rules
from a2_engine.urban.analysis import analyze_urban_candidates
from a2_engine.urban.last_return import search_urban_last_return
from a2_engine.urban.models import (
    UrbanArea,
    UrbanContext,
    UrbanCoverage,
    UrbanDestination,
    UrbanPoint,
    UrbanPopulationCoverage,
    UrbanSearchScope,
    UrbanVersions,
)
from a2_engine.urban.transit import UrbanTransitNetwork, search_urban_transit

DAY = date(2026, 11, 8)
ZONE = ZoneInfo("Europe/Kaliningrad")
PROOF = ("synthetic-proof",)


def at(hour: int, minute: int = 0, *, day: int = 8, second: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, second, tzinfo=ZONE).astimezone(UTC)


def event(
    node: str,
    arrival: datetime,
    departure: datetime | None = None,
    *,
    seq: int = 0,
    pickup: bool = True,
    dropoff: bool = True,
) -> TimetableEvent:
    return TimetableEvent(seq, node, arrival, departure or arrival, pickup, dropoff, PROOF)


def run(
    name: str, origin: str, start: datetime, target: str, end: datetime, **kwargs: Any
) -> TimetableRun:
    result = TimetableRun(
        "snapshot",
        "source",
        name,
        DAY,
        ZONE.key,
        "bus",
        (event(origin, start, seq=10), event(target, end, seq=20)),
        True,
        PROOF,
    )
    return replace(result, **kwargs)


def foot(origin: str, target: str, seconds: float, *, name: str | None = None) -> TimetableFootpath:
    return TimetableFootpath(name or origin + "-" + target, origin, target, seconds, True, PROOF)


def transfer(origin: str, target: str, minimum: float = 0, **kwargs: Any) -> TimetableTransfer:
    return TimetableTransfer(origin + "-" + target, origin, target, minimum, True, PROOF, **kwargs)


def table(
    runs: tuple[TimetableRun, ...] = (),
    paths: tuple[TimetableFootpath, ...] = (),
    transfers: tuple[TimetableTransfer, ...] = (),
    nodes: frozenset[str] = frozenset(),
    **scope_changes: Any,
) -> Timetable:
    node_ids = set(nodes)
    node_ids.update(e.node_id for r in runs for e in r.events)
    node_ids.update(n for p in paths for n in (p.from_node_id, p.to_node_id))
    scope = TimetableScope(
        "snapshot",
        "graph",
        "calendar",
        "timetable-v1",
        "footpaths-v1",
        "transfers-v1",
        ZONE.key,
        at(0),
        at(0, day=10),
        frozenset(node_ids),
        True,
        True,
        True,
        True,
        True,
        PROOF,
    )
    return Timetable(replace(scope, **scope_changes), runs, paths, transfers)


def endpoints(
    timetable: Timetable, departure: datetime | None = None
) -> tuple[UrbanTransitNetwork, UrbanPoint, UrbanDestination]:
    versions = UrbanVersions(
        "snapshot",
        "graph",
        "calendar",
        "population",
        "geometry",
        "a2-methodology-v3",
        "config",
        "static",
    )
    context = UrbanContext("city", "city", departure or at(8), "static", versions, "search")
    network = UrbanTransitNetwork(context, timetable, PROOF)
    point = UrbanPoint("point", "cell", "O", "O", 0, "city", 100, 2026, "synthetic", PROOF)
    destination = UrbanDestination(
        "destination", "object", frozenset({"airport"}), "D", "city", True, True, PROOF
    )
    return network, point, destination


def forward(timetable: Timetable, **kwargs: Any) -> Any:
    network, point, dest = endpoints(timetable)
    return search_urban_transit(
        network, point, dest, category="airport", rules=load_urban_journey_rules(), **kwargs
    )


def direct_table(walk_minutes: int | None = None) -> Timetable:
    paths: tuple[TimetableFootpath, ...] = (foot("O", "S", 300), foot("T", "D", 300))
    if walk_minutes is not None:
        paths += (foot("O", "D", walk_minutes * 60),)
    return table((run("direct", "S", at(8, 12), "T", at(8, 27)),), paths)


def test_direct_components_and_existing_analysis() -> None:
    t = direct_table()
    result = forward(t)
    best = result.best_known
    assert best is not None and result.search_complete and result.seconds.value == 1920
    assert (best.walking_seconds, best.riding_seconds, best.total_wait_seconds) == (600, 900, 420)
    assert best.waiting_share.value == 0.21875
    assert result.search.best_known and result.search.best_known.transfers == 0
    network, point, dest = endpoints(t)
    coverage = UrbanCoverage(
        UrbanSearchScope(network.context, "point", "airport", "public_transport", 14400),
        True,
        result.search_complete,
        PROOF,
        frozenset({"destination"}),
    )
    config = load_urban_config()
    analysis = analyze_urban_candidates(
        network.context,
        (point,),
        (dest,),
        (best.candidate,),
        (coverage,),
        area=UrbanArea("city", 200, "geometry", PROOF),
        selected_categories={"airport": "high"},
        modes=("public_transport",),
        population_coverage=UrbanPopulationCoverage(
            network.context, True, 0, PROOF, frozenset({"point"})
        ),
        config=config,
        rules=load_urban_journey_rules(),
    )
    assert analysis.modes[0].points[0].index.status == "available"
    assert analysis.modes[0].points[0].categories[0].count.reason_code == "TAU_UNAVAILABLE"


@pytest.mark.parametrize("minutes,seconds,boardings", [(40, 1920, 1), (28, 1680, 0)])
def test_public_transport_compares_full_walk_alternative(
    minutes: int, seconds: int, boardings: int
) -> None:
    result = forward(direct_table(minutes))
    assert result.seconds.value == seconds and result.search.best_known
    assert len(result.search.best_known.boardings) == boardings


def test_partial_transit_does_not_certify_walk_minimum() -> None:
    t = direct_table(28)
    t = replace(t, scope=replace(t.scope, schedule_complete=False))
    result = forward(t)
    assert result.best_known and result.best_known.total_seconds == 1680
    assert result.seconds.status == "unknown" and not result.search_complete
    assert forward(t, passenger_mode="walk").seconds.value == 1680


def test_transfer_walking_and_minimum_are_not_double_counted() -> None:
    trips = (run("one", "O", at(8), "X", at(8, 10)), run("two", "Y", at(8, 20), "D", at(8, 30)))
    t = table(trips, (foot("X", "Y", 300),), (transfer("X", "Y", 600),))
    result = forward(t)
    assert result.best_known and result.best_known.total_seconds == 1800
    assert (
        result.best_known.walking_seconds == 300 and result.best_known.transfer_wait_seconds == 300
    )
    assert result.search.best_known and result.search.best_known.transfers == 1
    assert (
        forward(replace(t, transfers=(transfer("X", "Y", 601),))).search.state
        == "confirmed_unreachable"
    )


@pytest.mark.parametrize("extra,known", [(0, True), (1, False)])
def test_initial_wait_four_hours_boundary(extra: int, known: bool) -> None:
    start = at(12) + timedelta(seconds=extra)
    result = forward(table((run("one", "O", start, "D", start + timedelta(minutes=10)),)))
    assert (result.best_known is not None) == known


def test_movement_longer_than_four_hours_is_allowed() -> None:
    result = forward(table((run("long", "O", at(8), "D", at(14)),)))
    assert result.best_known and result.best_known.riding_seconds == 21600
    assert result.best_known.total_wait_seconds == 0 and result.best_known.admissible


def test_wait_budget_counterexample_preserves_later_arrival() -> None:
    trips = (
        run("A", "O", at(8), "X", at(8, 10)),
        run("B", "O", at(8, 5), "X", at(8, 20)),
        run("connection", "X", at(12, 15), "D", at(12, 30)),
    )
    result = forward(table(trips, transfers=(transfer("X", "X"),)))
    assert result.search.best_known and result.best_known
    assert result.search.best_known.boardings[0][0] == trips[1].run_id
    assert (
        result.best_known.total_wait_seconds == 14400
        and result.best_known.candidate.arrival_at == at(12, 30)
    )


def test_dominance_also_preserves_later_walk_with_less_remaining_wait() -> None:
    # Same off-vehicle state/transfer anchor, not just two distinct arriving vehicles.
    t = table(
        (run("late", "X", at(12, 15), "D", at(12, 30)),),
        (foot("O", "X", 600), foot("O", "Y", 600), foot("Y", "X", 600)),
    )
    result = forward(t)
    assert result.best_known and result.best_known.total_wait_seconds == 14100
    assert result.best_known.walking_seconds == 1200


def test_staying_on_run_dwell_gapped_sequence_repeated_node() -> None:
    r = run("loop", "O", at(8), "D", at(8, 30))
    r = replace(
        r,
        events=(
            event("O", at(8), seq=1),
            event("X", at(8, 10), at(8, 15), seq=10, pickup=False, dropoff=False),
            event("O", at(8, 20), at(8, 22), seq=30, pickup=False, dropoff=False),
            event("D", at(8, 30), seq=90),
        ),
    )
    result = forward(table((r,)))
    assert result.best_known and result.best_known.riding_seconds == 1800
    assert [leg.duration_seconds for leg in result.best_known.candidate.legs] == [600, 600, 600]
    assert result.search.best_known and len(result.search.best_known.boardings) == 1
    assert result.search.best_known.transfers == 0


@pytest.mark.parametrize("pickup,dropoff", [(False, True), (True, False)])
def test_boarding_and_alighting_permissions(pickup: bool, dropoff: bool) -> None:
    r = run("one", "O", at(8), "D", at(8, 10))
    r = replace(
        r, events=(replace(r.events[0], pickup=pickup), replace(r.events[1], dropoff=dropoff))
    )
    assert forward(table((r,))).search.state == "confirmed_unreachable"


def test_same_template_in_different_service_dates_is_not_one_execution() -> None:
    first = run("template", "O", at(8), "X", at(8, 10), service_date=DAY - timedelta(days=1))
    first = replace(first, events=(first.events[0], replace(first.events[1], dropoff=False)))
    second = run("template", "X", at(8, 11), "D", at(8, 20))
    assert first.run_id != second.run_id
    assert forward(table((first, second))).search.state == "confirmed_unreachable"


def test_calendar_exclusions_and_previous_service_day() -> None:
    canceled = run("fast", "O", at(8), "D", at(8, 5), active=False)
    previous = run(
        "previous-service-day", "O", at(8, 10), "D", at(8, 30), service_date=DAY - timedelta(days=1)
    )
    result = forward(table((canceled, previous)))
    assert result.seconds.value == 1800 and result.search.best_known
    assert result.search.best_known.boardings[0][0] == previous.run_id
    partial = forward(table((replace(canceled, active=None), previous)))
    assert (
        partial.seconds.status == "unknown"
        and partial.best_known
        and partial.best_known.total_seconds == 1800
    )


def return_table() -> Timetable:
    trips = (
        run("A", "A", at(23, 55), "G", at(0, 15, day=9)),
        run("B", "B", at(23, 50), "G", at(0, 10, day=9)),
        run("24:05", "C", at(0, 5, day=9), "G", at(0, 6, day=9)),
    )
    return table(
        trips, (foot("D", "A", 1200), foot("D", "B", 300), foot("D", "C", 0), foot("G", "O", 300))
    )


def last(t: Timetable, **kwargs: Any) -> Any:
    network, point, dest = endpoints(t)
    return search_urban_last_return(
        network, point, dest, local_day=DAY, rules=load_urban_journey_rules(), **kwargs
    )


def test_latest_leave_not_latest_bus_and_arrival_after_midnight() -> None:
    result = last(return_table())
    assert result.status == "available" and result.proven and result.best_known
    assert (result.leave_at, result.first_departure_at, result.arrival_at) == (
        at(23, 45),
        at(23, 50),
        at(0, 15, day=9),
    )
    assert result.best_known.totals.walking_seconds == 600
    assert result.best_known.totals.total_wait_seconds == 0
    assert result.point_id == "point" and result.destination_id == "destination"
    assert all(leg.start_at.tzinfo == UTC for leg in result.best_known.legs)


def test_lower_leave_bound_and_selected_object_are_preserved() -> None:
    assert last(return_table(), lower_leave_at=at(23, 46)).best_known is None
    t = return_table()
    network, point, dest = endpoints(t)
    other = replace(dest, destination_id="other", access_node_id="Q")
    t = replace(t, scope=replace(t.scope, node_ids=t.scope.node_ids | {"Q"}))
    result = search_urban_last_return(
        replace(network, timetable=t), point, other, local_day=DAY, rules=load_urban_journey_rules()
    )
    assert result.destination_id == "other" and result.best_known is None


def test_directed_walk_is_not_reversed_and_return_does_not_change_day_index() -> None:
    t = table((run("out", "O", at(8), "D", at(8, 10)),), (foot("O", "D", 300),))
    outbound = forward(t)
    result = last(t)
    assert outbound.seconds.value == 300
    assert result.state == "confirmed_unreachable" and result.proven
    assert outbound.seconds.value == 300
    assert last(t, passenger_mode="walk").status == "not_applicable"


def test_walk_alternative_does_not_cancel_transport_return_search() -> None:
    t = return_table()
    t = replace(t, footpaths=t.footpaths + (foot("D", "O", 60),))
    result = last(t)
    assert result.status == "available" and result.leave_at == at(23, 45)
    walking = last(table(paths=(foot("D", "O", 60),)))
    assert walking.status == "not_applicable" and walking.state == "walk_only" and walking.proven


def test_partial_return_keeps_best_known_and_midnight_truncation_is_unknown() -> None:
    t = return_table()
    result = last(replace(t, scope=replace(t.scope, continuations_complete=False)))
    assert result.status == "unknown" and result.leave_at is None
    assert result.best_known and result.best_known.leave_at == at(23, 45)
    clipped = last(replace(t, scope=replace(t.scope, end_at=at(0, day=9))))
    assert clipped.status == "unknown" and not clipped.proven
    assert "CONTINUATION_OUTSIDE_HORIZON" in clipped.reasons


def test_unknown_transfers_preserve_walk_best_known_without_false_no_route() -> None:
    t = table(
        (run("one", "O", at(8), "X", at(8, 10)), run("two", "X", at(8, 15), "D", at(8, 20))),
        (foot("O", "D", 1800),),
    )
    result = forward(t)
    assert result.best_known and result.best_known.total_seconds == 1800
    assert not result.search_complete and result.search.state == "unknown"
    assert "TRANSFER_RULE_UNAVAILABLE" in result.search.reasons


def test_resource_limits_and_zero_time_cycles() -> None:
    t = direct_table(40)
    result = forward(t, resource_limit=1)
    assert result.search.interrupted and result.best_known and result.seconds.status == "unknown"
    t = replace(t, footpaths=t.footpaths + (foot("O", "O", 0, name="zero-cycle"),))
    assert forward(t).seconds.value == 1920
    assert last(return_table(), resource_limit=1).interrupted
    looping = table(paths=(foot("O", "O", 1),), nodes=frozenset({"D"}))
    result = forward(looping, resource_limit=10)
    assert result.search.state == "unknown" and result.search.interrupted


def test_duplicate_ids_bad_times_and_replay_immutability() -> None:
    t = direct_table()
    snapshot = repr(t)
    assert forward(t) == forward(
        replace(t, runs=tuple(reversed(t.runs)), footpaths=tuple(reversed(t.footpaths)))
    )
    assert repr(t) == snapshot
    with pytest.raises(ValueError):
        replace(t, runs=t.runs * 2)
    with pytest.raises(ValueError):
        replace(t, footpaths=t.footpaths * 2)
    with pytest.raises(ValueError):
        replace(t.runs[0], events=(t.runs[0].events[0], t.runs[0].events[0]))
    with pytest.raises(ValueError):
        replace(t.runs[0].events[0], arrival_at=datetime(2026, 11, 8, 8))
    with pytest.raises(ValueError):
        foot("O", "D", 1e-7)
    assert forward(
        replace(t, footpaths=tuple(replace(p, seconds=60.123456789) for p in t.footpaths))
    ).best_known


def brute_arrival(
    t: Timetable, origin: str, target: str, start: datetime, max_wait: int
) -> datetime | None:
    """Independent exhaustive off-vehicle DFS: enumerate every boarding/alighting pair.

    Tiny cases have strictly advancing rides/walks and finite timestamps. No label
    dominance, no production solver, no arbitrary depth bound.
    """
    answers = []

    def visit(node: str, now: datetime, wait: float, used: bool) -> None:
        if now > t.scope.end_at or wait > max_wait:
            return
        if node == target:
            answers.append(now)
            return
        for p in t.footpaths:
            if p.usable and p.from_node_id == node:
                assert p.seconds is not None
                visit(p.to_node_id, now + timedelta(seconds=p.seconds), wait, used)
        for r in t.runs:
            if r.active is not True:
                continue
            for i, e in enumerate(r.events[:-1]):
                if e.node_id != node or not e.pickup or e.departure_at < now:
                    continue
                for end in r.events[i + 1 :]:
                    if end.dropoff:
                        visit(
                            end.node_id,
                            end.arrival_at,
                            wait + (e.departure_at - now).total_seconds(),
                            True,
                        )

    visit(origin, start, 0, False)
    return min(answers) if answers else None


def test_matches_independent_exhaustive_search_on_small_finite_networks() -> None:
    rng = random.Random(20261108)
    nodes = ("A", "B", "C")
    for case in range(80):
        runs = []
        for i in range(6):
            origin, target = rng.sample(nodes, 2)
            start = at(8) + timedelta(minutes=rng.randrange(7))
            runs.append(
                run(str(i), origin, start, target, start + timedelta(minutes=rng.randrange(1, 3)))
            )
        paths = tuple(
            foot(a, b, 60 * rng.randrange(1, 3))
            for a, b in [("A", "B"), ("B", "C")]
            if rng.randrange(2)
        )
        rules = tuple(transfer(a, b) for a in nodes for b in nodes)
        t = table(tuple(runs), paths, rules, nodes=frozenset(nodes), end_at=at(8, 8))
        # All continuations end within this tiny bounded window.
        budget = rng.randrange(4) * 60
        actual = solve_timetable(t, "A", "C", at(8), max_wait_seconds=budget)
        expected = brute_arrival(t, "A", "C", at(8), budget)
        assert actual.proven, (case, actual.reasons)
        assert (actual.best_known.arrival_at if actual.best_known else None) == expected, case


def test_explicit_dst_offsets_use_elapsed_utc_not_repeated_wall_clock() -> None:
    zone = ZoneInfo("America/New_York")
    first = datetime(2026, 11, 1, 1, 50, tzinfo=zone, fold=0)
    finish = datetime(2026, 11, 1, 1, 10, tzinfo=zone, fold=1)
    r = run("DST", "O", first, "D", finish, service_date=date(2026, 11, 1), timezone=zone.key)
    t = table(
        (r,),
        timezone=zone.key,
        start_at=datetime(2026, 11, 1, 0, tzinfo=zone),
        end_at=datetime(2026, 11, 2, 0, tzinfo=zone),
    )
    network, point, dest = endpoints(t, first)
    result = search_urban_transit(
        network, point, dest, category="airport", rules=load_urban_journey_rules()
    )
    assert result.seconds.value == 1200
    assert result.best_known and result.best_known.riding_seconds == 1200


@pytest.mark.parametrize(
    "flag",
    [
        "schedule_complete",
        "calendar_complete",
        "footpaths_complete",
        "transfers_complete",
        "continuations_complete",
    ],
)
def test_coverage_flags_never_promote_best_known_to_proven(flag: str) -> None:
    t = direct_table()
    changes: dict[str, Any] = {flag: False}
    result = forward(replace(t, scope=replace(t.scope, **changes)))
    assert result.best_known and result.best_known.total_seconds == 1920
    assert not result.search.proven and result.seconds.status == "unknown"


def test_unknown_footpath_is_not_walk_or_no_route() -> None:
    t = direct_table()
    paths = tuple(
        replace(p, seconds=None, confirmed=False) if p.from_node_id == "T" else p
        for p in t.footpaths
    )
    result = forward(replace(t, footpaths=paths))
    assert result.best_known is None and result.search.state == "unknown"


def test_conflicting_specific_transfer_rules_are_rejected() -> None:
    a = run("a", "O", at(8), "X", at(8, 10))
    b = run("b", "X", at(8, 15), "D", at(8, 20))
    r1 = transfer("X", "X", from_run_id=a.run_id)
    r2 = replace(transfer("X", "X", to_run_id=b.run_id), rule_id="other")
    with pytest.raises(ValueError, match="AMBIGUOUS_TRANSFER_RULE"):
        forward(table((a, b), transfers=(r1, r2)))
    with pytest.raises(ValueError, match="CONFLICTING_TRANSFER_RULE"):
        table((a, b), transfers=(r1, replace(r1, rule_id="copy")))


def test_no_implicit_two_transfer_limit_and_vehicle_types_are_preserved() -> None:
    runs = tuple(
        run(
            str(i),
            str(i),
            at(8, i * 10),
            str(i + 1),
            at(8, i * 10 + 5),
            transport_type="rail" if i % 2 else "bus",
        )
        for i in range(4)
    )
    t = table(runs, transfers=tuple(transfer(str(i), str(i)) for i in range(1, 4)))
    network, point, dest = endpoints(t)
    point = replace(point, origin_node_id="0", access_node_id="0")
    dest = replace(dest, access_node_id="4")
    result = search_urban_transit(
        network, point, dest, category="airport", rules=load_urban_journey_rules()
    )
    assert result.search.best_known and result.search.best_known.transfers == 3
    assert result.best_known and {leg.mode for leg in result.best_known.candidate.legs} == {
        "public_transport",
        "rail",
    }
    assert {
        step.transport_type for step in result.search.best_known.steps if step.kind == "ride"
    } == {"bus", "rail"}


def test_same_node_minimum_transfer_is_a_constraint_not_fictitious_walk() -> None:
    a = run("a", "O", at(8), "X", at(8, 10))
    b = run("b", "X", at(8, 13), "D", at(8, 20))
    result = forward(table((a, b), transfers=(transfer("X", "X", 180),)))
    assert (
        result.best_known
        and result.best_known.walking_seconds == 0
        and result.best_known.transfer_wait_seconds == 180
    )
    assert all(step.kind != "walk" for step in result.search.best_known.steps)


def test_truncated_first_boarding_day_and_computation_keep_known_times_separate() -> None:
    t = return_table()
    partial = last(replace(t, scope=replace(t.scope, start_at=at(12))))
    assert (
        partial.status == "unknown"
        and partial.best_known
        and partial.best_known.leave_at == at(23, 45)
    )
    assert partial.leave_at is None and "FIRST_BOARDING_DAY_NOT_FULLY_COVERED" in partial.reasons
    for budget in range(1, 60):
        limited = last(t, resource_limit=budget)
        if limited.interrupted and limited.best_known:
            assert limited.status == "unknown" and limited.leave_at is None
            break
    else:
        pytest.fail("Expected a resource cutoff with a best-known return")


def test_source_profile_versions_and_fractional_provenance() -> None:
    t = direct_table()
    network, point, dest = endpoints(t)
    with pytest.raises(ValueError, match="VERSION_MISMATCH"):
        replace(network, timetable=replace(t, scope=replace(t.scope, graph_version="other")))
    with pytest.raises(ValueError, match="METHODOLOGY_MISMATCH"):
        search_urban_transit(
            network,
            point,
            dest,
            category="airport",
            rules=replace(load_urban_journey_rules(), methodology_version="other"),
        )
    paths = tuple(replace(p, seconds=60.123456789) for p in t.footpaths)
    result = forward(replace(t, footpaths=paths))
    assert result.search.best_known
    walks = [s for s in result.search.best_known.steps if s.kind == "walk"]
    assert all(
        s.duration_seconds == 60.123457 and s.original_seconds == 60.123456789 and s.footpath_id
        for s in walks
    )


def test_route_identity_is_stable_between_python_hash_seeds() -> None:
    import os
    import subprocess
    import sys
    from pathlib import Path

    engine = Path(__file__).resolve().parents[1]
    script = (
        "import runpy; print(runpy.run_path("
        + repr(str(engine / "examples" / "synthetic_transit.py"))
        + ')["run_demo"]()[0].best_known.candidate.journey_id)'
    )
    identities = [
        subprocess.check_output(
            [sys.executable, "-c", script],
            text=True,
            env={**os.environ, "PYTHONHASHSEED": str(seed), "PYTHONPATH": str(engine / "src")},
        ).strip()
        for seed in (0, 17)
    ]
    assert identities[0] == identities[1]


def test_physical_approaches_integrate_with_existing_endpoint_checks() -> None:
    from a2_engine.urban.journeys import validate_urban_journey

    t = direct_table()
    network, point, dest = endpoints(t)
    point = replace(point, access_node_id="S", access_distance_m=100)
    dest = replace(dest, access_node_id="T", physical_node_id="D")
    result = search_urban_transit(
        network, point, dest, category="airport", rules=load_urban_journey_rules()
    )
    assert result.best_known and result.best_known.total_seconds == 1920
    assert result.best_known.candidate.destination_node_id == "D"
    bad = replace(
        result.best_known.candidate,
        legs=(
            replace(result.best_known.candidate.legs[0], duration_seconds=299),
            *result.best_known.candidate.legs[1:],
        ),
    )
    with pytest.raises(ValueError, match="LEG_DURATION_MISMATCH"):
        validate_urban_journey(bad, rules=load_urban_journey_rules())
    coverage = UrbanCoverage(
        UrbanSearchScope(network.context, "point", "airport", "public_transport", 14400),
        True,
        result.search_complete,
        PROOF,
        frozenset({"destination"}),
    )
    analysis = analyze_urban_candidates(
        network.context,
        (point,),
        (dest,),
        (result.best_known.candidate,),
        (coverage,),
        area=UrbanArea("city", 224.668, "geometry", PROOF),
        selected_categories={"airport": "high"},
        modes=("public_transport",),
        population_coverage=UrbanPopulationCoverage(
            network.context, True, 0, PROOF, frozenset({"point"})
        ),
        config=load_urban_config(),
        rules=load_urban_journey_rules(),
    )
    assert analysis.modes[0].index.status == "available"


def test_unknown_minimum_retains_candidate_through_existing_analysis() -> None:
    t = direct_table(28)
    t = replace(t, scope=replace(t.scope, schedule_complete=False))
    network, point, dest = endpoints(t)
    result = forward(t)
    assert result.best_known
    coverage = UrbanCoverage(
        UrbanSearchScope(network.context, "point", "airport", "public_transport", 14400),
        True,
        result.search_complete,
        PROOF,
        frozenset({"destination"}),
    )
    analysis = analyze_urban_candidates(
        network.context,
        (point,),
        (dest,),
        (result.best_known.candidate,),
        (coverage,),
        area=UrbanArea("city", 200, "geometry", PROOF),
        selected_categories={"airport": "high"},
        modes=("public_transport",),
        population_coverage=UrbanPopulationCoverage(
            network.context, True, 0, PROOF, frozenset({"point"})
        ),
        config=load_urban_config(),
        rules=load_urban_journey_rules(),
    )
    category = analysis.modes[0].points[0].categories[0]
    assert category.best and category.best.journey.total_seconds == 1680
    assert category.index.status == "unknown" and analysis.h_seconds.value == 1800


def test_empty_runs_require_explicit_coverage_and_direction_of_transfer() -> None:
    empty = table(nodes=frozenset({"O", "D"}), schedule_complete=False, calendar_complete=False)
    assert forward(empty).search.state == "unknown"
    assert (
        forward(
            replace(
                empty, scope=replace(empty.scope, schedule_complete=True, calendar_complete=True)
            )
        ).search.state
        == "confirmed_unreachable"
    )
    t = table(
        (run("one", "O", at(8), "X", at(8, 10)), run("two", "Y", at(8, 20), "D", at(8, 30))),
        (foot("Y", "X", 300),),
        (transfer("X", "Y"),),
    )
    assert forward(t).best_known is None


@pytest.mark.parametrize("bad", [-1, float("nan"), float("inf"), True, "60"])
def test_invalid_seconds_and_resource_configuration(bad: Any) -> None:
    with pytest.raises(ValueError):
        foot("O", "D", bad)
    with pytest.raises(ValueError):
        transfer("O", "D", bad)
    with pytest.raises(ValueError):
        solve_timetable(direct_table(), "O", "D", at(8), max_wait_seconds=bad)
    with pytest.raises(ValueError):
        forward(direct_table(), resource_limit=-1)


def test_return_replay_deterministic_and_partial_result_does_not_touch_forward_score() -> None:
    import runpy
    from pathlib import Path

    outbound, analysis, result = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "examples" / "synthetic_transit.py")
    )["run_demo"]()
    t = result.network.timetable
    network = replace(
        result.network,
        timetable=replace(t, runs=tuple(reversed(t.runs)), footpaths=tuple(reversed(t.footpaths))),
    )
    point = analysis.modes[0].points[0].point
    chosen = analysis.modes[0].points[0].categories[0].best
    assert chosen is not None
    dest = chosen.destination
    original = analysis.modes[0].index.value
    replay = search_urban_last_return(
        network,
        replace(point, point_id="p"),
        replace(dest, destination_id="d"),
        local_day=DAY,
        rules=load_urban_journey_rules(),
    )
    assert replay.leave_at == result.leave_at and replay.best_known and result.best_known
    assert replay.best_known.source_path == result.best_known.source_path
    partial = replace(
        network, timetable=replace(t, scope=replace(t.scope, continuations_complete=False))
    )
    assert (
        search_urban_last_return(
            partial, point, dest, local_day=DAY, rules=load_urban_journey_rules()
        ).status
        == "unknown"
    )
    assert analysis.modes[0].index.value == original and outbound.seconds.value == 1920
