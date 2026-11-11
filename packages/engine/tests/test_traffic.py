"""Normalized synthetic traffic: observable numerical controls and negative boundaries."""

import copy
import runpy
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from a2_engine import (
    TrafficCalendarDay,
    TrafficCarInputs,
    TrafficEdge,
    TrafficObservation,
    TrafficPath,
    TrafficPathResult,
    TrafficProfile,
    TrafficRequest,
    TrafficSnapshot,
    TrafficTransition,
    TypicalODCollector,
    UrbanODPlan,
    UrbanODScope,
    UrbanTauDay,
    analyze_traffic_car,
    build_typical_profile,
    calculate_urban_tau,
    evaluate_traffic_path,
    load_urban_config,
    load_urban_journey_rules,
    load_urban_tau_rules,
    plan_typical_od_week,
    select_traffic_profile,
    traffic_car_requests,
    traffic_od_request,
    verify_traffic_minimum,
)
from a2_engine.routing.traffic.evaluation import TrafficMinimumReceipt

DEMO = runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples/synthetic_traffic.py"))
make_snapshot = cast(Callable[[], TrafficSnapshot], DEMO["synthetic_snapshot"])
make_inputs = cast(Callable[[TrafficSnapshot], TrafficCarInputs], DEMO["synthetic_inputs"])
receipt = cast(Callable[[TrafficPathResult], TrafficMinimumReceipt], DEMO["synthetic_receipt"])
PROOF = ("SYNTHETIC-only",)
REFERENCE = datetime(2026, 11, 9, 12, tzinfo=UTC)


def profile(
    snapshot: TrafficSnapshot, edge: TrafficEdge, speed: float, bin: int, **changes: Any
) -> TrafficProfile:
    base = snapshot.profiles[0]
    return replace(
        base,
        profile_id=edge.edge_id + "-" + str(bin),
        group=replace(base.group, edge=edge, local_15min_bin=bin),
        speed_kph=speed,
        aggregation="accepted_aggregate",
        samples_hash=None,
        **changes,
    )


def request(snapshot: TrafficSnapshot, **changes: Any) -> TrafficRequest:
    original = traffic_car_requests(make_inputs(snapshot), categories=("school",))[0]
    return replace(original, **changes)


def evaluation(*, proven: bool = False) -> TrafficPathResult:
    snapshot = make_snapshot()
    req = request(snapshot)
    edge = next(p.group.edge for p in snapshot.profiles if p.group.edge.edge_id == "as")
    result = evaluate_traffic_path(req, TrafficPath((edge,), (), PROOF, scope=req.snapshot.scope))
    return verify_traffic_minimum(result, receipt(result)) if proven else result


def typical_plan(snapshot: TrafficSnapshot) -> tuple[UrbanODPlan, TrafficCarInputs]:
    inputs = make_inputs(snapshot)
    rules = load_urban_tau_rules()
    scope = UrbanODScope(
        "city",
        "private_car",
        inputs.context.versions,
        "typical",
        inputs.context.search_parameters_id,
        "UTC",
        frozenset({"A", "B"}),
        rules.max_total_wait_seconds,
    )
    start = date(2026, 11, 2)
    calendar = tuple(
        UrbanTauDay(start + timedelta(days=i), True, False, True, True, PROOF) for i in range(7)
    )
    plan = plan_typical_od_week(
        scope,
        inputs.points,
        snapshot,
        week_start=start,
        reference_at=REFERENCE,
        calendar=calendar,
        rules=rules,
        evidence_refs=PROOF,
        search_scope_id=inputs.search_scope_id,
    )
    return plan, inputs


def od_results(
    plan: UrbanODPlan, inputs: TrafficCarInputs, *, prove: bool = True
) -> list[tuple[Any, TrafficPathResult]]:
    entries = dict(inputs.entries)
    exits = {
        p.point_id: replace(entries[p.point_id], direction="exit", seconds=120)
        for p in inputs.points
    }
    results = []
    for task in plan.tasks():
        req = traffic_od_request(
            task,
            inputs.snapshot,
            entries,
            exits,
            checked_at=REFERENCE,
            search_scope_id=inputs.search_scope_id,
        )
        edge = next(
            p.group.edge
            for p in inputs.snapshot.profiles
            if p.group.edge.edge_id == ("ab" if task.origin.point_id == "A" else "ba")
        )
        result = evaluate_traffic_path(
            req, TrafficPath((edge,), (), PROOF, scope=req.snapshot.scope)
        )
        results.append((task, verify_traffic_minimum(result, receipt(result)) if prove else result))
    return results


@pytest.mark.parametrize(
    "speeds,expected", [([20, 30, 40, 50, 60], 40), ([10, 20, 30, 40, 50, 60], 35)]
)
def test_typical_median_distinct_days(speeds: list[float], expected: float) -> None:
    snap = make_snapshot()
    group = snap.profiles[0].group
    days = [date(2026, 10, 19) + timedelta(days=i) for i in range(len(speeds))]
    calendar = tuple(TrafficCalendarDay(d, group.day_type, group.season, PROOF) for d in days)
    samples = [
        TrafficObservation(
            str(i),
            group,
            datetime.combine(d, datetime.min.time(), UTC)
            + timedelta(minutes=group.local_15min_bin * 15),
            datetime.combine(d, datetime.min.time(), UTC)
            + timedelta(minutes=group.local_15min_bin * 15, seconds=1),
            speed,
            False,
            snap.profiles[0].provenance,
        )
        for i, (d, speed) in enumerate(zip(days, speeds, strict=True))
    ]
    before = copy.deepcopy(samples)
    result = build_typical_profile(
        "median",
        group,
        samples,
        valid_from=snap.profiles[0].valid_from,
        valid_until=snap.profiles[0].valid_until,
        calendar=calendar,
        provenance=snap.profiles[0].provenance,
    )
    assert result.speed_kph == expected and result.qa_reason is None
    assert result.aggregation == "median_recomputed_v1" and result.samples_hash
    assert samples == before
    aggregate = replace(result, aggregation="accepted_aggregate", samples_hash=None)
    assert aggregate.qa_reason is None and aggregate.aggregation == "accepted_aggregate"


@pytest.mark.parametrize("different_days", [1, 4])
def test_five_samples_do_not_mean_five_days(different_days: int) -> None:
    snap = make_snapshot()
    base = snap.profiles[0]
    group = base.group
    dates = [date(2026, 10, 19) + timedelta(days=i % different_days) for i in range(5)]
    calendar = tuple(
        TrafficCalendarDay(d, group.day_type, group.season, PROOF) for d in sorted(set(dates))
    )
    samples = [
        TrafficObservation(
            str(i),
            group,
            datetime.combine(d, datetime.min.time(), UTC)
            + timedelta(minutes=group.local_15min_bin * 15, seconds=i),
            datetime.combine(d, datetime.min.time(), UTC)
            + timedelta(minutes=group.local_15min_bin * 15, seconds=i + 1),
            40,
            False,
            base.provenance,
        )
        for i, d in enumerate(dates)
    ]
    result = build_typical_profile(
        "short",
        group,
        samples,
        valid_from=base.valid_from,
        valid_until=base.valid_until,
        calendar=calendar,
        provenance=base.provenance,
    )
    assert result.qa_reason == "INSUFFICIENT_DISTINCT_OBSERVATION_DAYS"
    assert result.speed_kph is None


@pytest.mark.parametrize("change", ["season", "bin", "direction", "graph", "closure", "quality"])
def test_typical_incomparable_samples_rejected(change: str) -> None:
    snap = make_snapshot()
    base = snap.profiles[0]
    group = base.group
    at = datetime(2026, 10, 19, tzinfo=UTC) + timedelta(minutes=group.local_15min_bin * 15)
    original = TrafficObservation(
        "sample", group, at, at + timedelta(seconds=1), 40, False, base.provenance
    )
    changed = original
    if change == "season":
        changed = replace(original, group=replace(group, season="winter"))
    if change == "bin":
        changed = replace(original, group=replace(group, local_15min_bin=33))
    if change == "direction":
        changed = replace(
            original,
            group=replace(
                group,
                edge=replace(
                    group.edge,
                    from_node_id=group.edge.to_node_id,
                    to_node_id=group.edge.from_node_id,
                ),
            ),
        )
    if change == "graph":
        changed = replace(
            original, group=replace(group, scope=replace(group.scope, graph_version="other"))
        )
    if change == "closure":
        changed = replace(original, closed=True, speed_kph=None)
    if change == "quality":
        changed = replace(original, provenance=replace(base.provenance, quality="unverified"))
    changed = replace(changed, observation_id="changed")
    with pytest.raises(ValueError):
        build_typical_profile(
            "bad",
            group,
            [original, changed],
            valid_from=base.valid_from,
            valid_until=base.valid_until,
            calendar=snap.calendar,
            provenance=base.provenance,
        )


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan"), True])
def test_invalid_speed_cannot_encode_closure(value: Any) -> None:
    with pytest.raises(ValueError):
        replace(make_snapshot().profiles[0], speed_kph=value)


def test_duplicate_and_conflicting_profiles_and_content_hash() -> None:
    snap = make_snapshot()
    original = snap.profiles[0]
    assert replace(snap, profiles=(*snap.profiles, original)).content_hash == snap.content_hash
    with pytest.raises(ValueError, match="CONFLICT"):
        replace(snap, profiles=(*snap.profiles, replace(original, speed_kph=99)))
    with pytest.raises(ValueError, match="CONTENT_HASH"):
        replace(snap, declared_content_hash="other")
    assert replace(snap, declared_content_hash=snap.content_hash).content_hash == snap.content_hash
    with pytest.raises(ValueError, match="PROFILE_SCOPE"):
        replace(snap, scope=replace(snap.scope, graph_version="other"))
    with pytest.raises(ValueError, match="OBSERVATION_DAYS"):
        replace(
            original, observation_days=(*original.observation_days, original.observation_days[0])
        )


def test_per_edge_entry_time_780_not_global_departure() -> None:
    snap = make_snapshot()
    first = TrafficEdge("one", "access-A", "middle", 600)
    second = TrafficEdge("two", "middle", "access-school", 600)
    snap = replace(
        snap,
        profiles=(
            profile(snap, first, 36, 32),
            profile(snap, second, 36, 32),
            profile(snap, second, 3.6, 33),
        ),
    )
    req = request(
        snap,
        departure_at=datetime(2026, 11, 9, 8, 13, tzinfo=UTC),
        destination=replace(request(snap).destination, seconds=60),
    )
    turn = TrafficTransition("one", "two", 0, False, PROOF)
    result = evaluate_traffic_path(
        req, TrafficPath((first, second), (turn,), PROOF, scope=req.snapshot.scope)
    )
    assert result.known_seconds == 780
    assert [(s.start_at.hour, s.start_at.minute) for s in result.steps if s.kind == "edge"] == [
        (8, 14),
        (8, 15),
    ]
    assert [s.raw_seconds for s in result.steps if s.kind == "edge"] == [60, 600]


def test_initial_walk_changes_bin_and_long_edge_uses_entry_policy() -> None:
    snap = make_snapshot()
    edge = TrafficEdge("long", "access-A", "access-school", 1200)
    snap = replace(snap, profiles=(profile(snap, edge, 36, 32), profile(snap, edge, 3.6, 33)))
    req = request(
        snap,
        departure_at=datetime(2026, 11, 9, 8, 14, tzinfo=UTC),
        destination=replace(request(snap).destination, seconds=0),
    )
    result = evaluate_traffic_path(req, TrafficPath((edge,), (), PROOF, scope=req.snapshot.scope))
    assert result.known_seconds == 1260  # 60 approach + 1200 at the entry speed, crosses 08:30.
    assert result.steps[1].start_at.minute == 15 and result.steps[1].end_at.minute == 35


@pytest.mark.parametrize("included,expected", [(True, 300), (False, 330)])
def test_turn_cost_accounted_once(included: bool, expected: float) -> None:
    snap = make_snapshot()
    a = TrafficEdge("a", "access-A", "mid", 600)
    b = TrafficEdge("b", "mid", "access-school", 600)
    snap = replace(snap, profiles=(profile(snap, a, 36, 32), profile(snap, b, 36, 32)))
    result = evaluate_traffic_path(
        request(snap),
        TrafficPath(
            (a, b),
            (TrafficTransition("a", "b", 30, included, PROOF),),
            PROOF,
            scope=request(snap).snapshot.scope,
        ),
    )
    assert result.known_seconds == expected
    turn = result.steps[2]
    assert turn.raw_seconds == 30 and turn.duration_seconds == (0 if included else 30)
    with pytest.raises(ValueError, match="TRANSITION_ROSTER"):
        TrafficPath((a, b), (), PROOF, scope=snap.scope)


@pytest.mark.parametrize("seconds", [0.123456, 0.000001, 0.00000001])
def test_fractional_exact_evaluator_not_valhalla_minute_protocol(seconds: float) -> None:
    snap = make_snapshot()
    req = request(snap, origin=replace(request(snap).origin, seconds=seconds))
    edge = next(p.group.edge for p in snap.profiles if p.group.edge.edge_id == "as")
    if seconds < 0.0000005:
        with pytest.raises(ValueError, match="RESOLUTION"):
            evaluate_traffic_path(req, TrafficPath((edge,), (), PROOF, scope=req.snapshot.scope))
    else:
        result = evaluate_traffic_path(
            req, TrafficPath((edge,), (), PROOF, scope=req.snapshot.scope)
        )
        assert result.known_seconds == pytest.approx(1620 + 120 + seconds, abs=1e-6)
        assert result.steps[0].raw_seconds == seconds
        assert all(
            (s.end_at - s.start_at).total_seconds() == s.duration_seconds for s in result.steps
        )


@pytest.mark.parametrize(
    "field", ["request_id", "traffic_content_hash", "path_hash", "evaluation_hash"]
)
def test_minimum_receipt_binding(field: str) -> None:
    result = evaluation()
    with pytest.raises(ValueError, match="BINDING"):
        verify_traffic_minimum(
            result, replace(receipt(result), **cast(dict[str, Any], {field: "other"}))
        )


def test_known_path_requires_own_dynamic_minimum_and_preserves_inputs() -> None:
    result = evaluation()
    before = copy.deepcopy(result)
    inputs = make_inputs(result.request.snapshot)
    output = analyze_traffic_car(
        inputs,
        [result],
        selected_categories={"school": "high"},
        config=load_urban_config(),
        rules=load_urban_journey_rules(),
    )
    c = output.analysis.modes[0].points[0].categories[0]
    assert c.best is not None and c.index.value is None and c.best.journey.total_seconds == 1800
    proven = verify_traffic_minimum(result, receipt(result))
    output = analyze_traffic_car(
        inputs,
        [proven],
        selected_categories={"school": "high"},
        config=load_urban_config(),
        rules=load_urban_journey_rules(),
    )
    assert output.analysis.modes[0].points[0].categories[0].index.value == 50
    assert result == before
    with pytest.raises(ValueError, match="DYNAMIC_MINIMUM"):
        verify_traffic_minimum(result, cast(Any, object()))
    with pytest.raises(ValueError, match="RESULT_DIFFERS"):
        analyze_traffic_car(
            inputs,
            [replace(result, minimum_proven=True)],
            selected_categories={"school": "high"},
            config=load_urban_config(),
            rules=load_urban_journey_rules(),
        )
    later = evaluate_traffic_path(
        replace(result.request, departure_at=result.request.departure_at + timedelta(seconds=1)),
        result.path,
    )
    with pytest.raises(ValueError, match="BINDING"):
        verify_traffic_minimum(later, receipt(result))


@pytest.mark.parametrize("case", ["closed", "missing", "ambiguous", "unverified"])
def test_incomplete_path_never_pair_unreachable(case: str) -> None:
    result = evaluation()
    snap = result.request.snapshot
    profiles = list(snap.profiles)
    pos = next(
        i
        for i, p in enumerate(profiles)
        if p.group.edge.edge_id == "as" and p.group.local_15min_bin == 32
    )
    p = profiles[pos]
    if case == "closed":
        profiles[pos] = replace(p, closed=True, speed_kph=None)
    if case == "missing":
        profiles.pop(pos)
    if case == "unverified":
        profiles[pos] = replace(p, provenance=replace(p.provenance, quality="unverified"))
    if case == "ambiguous":
        profiles.append(replace(p, profile_id="duplicate-group"))
    snap = replace(snap, profiles=tuple(profiles))
    result = evaluate_traffic_path(request(snap), result.path)
    assert result.known_seconds is None and not result.minimum_proven
    assert result.state == ("blocked" if case == "closed" else "unknown")


def observation_snapshot(mode: Any = "live", **changes: Any) -> TrafficSnapshot:
    base = make_snapshot()
    scope = replace(base.scope, mode=mode)
    edge = next(p.group.edge for p in base.profiles if p.group.edge.edge_id == "as")
    p = profile(base, edge, 36, 32)
    p = replace(
        p,
        group=replace(p.group, scope=scope),
        aggregation="observation",
        observation_days=(),
        observed_from=date(2026, 11, 9),
        observed_to=date(2026, 11, 9),
        observed_at=datetime(2026, 11, 9, 8, tzinfo=UTC),
        fetched_at=datetime(2026, 11, 9, 9, 0, 1, tzinfo=UTC)
        if mode == "historical"
        else datetime(2026, 11, 9, 8, 0, 1, tzinfo=UTC),
        valid_from=datetime(2026, 11, 9, 8, tzinfo=UTC),
        valid_until=datetime(2026, 11, 9, 9, tzinfo=UTC),
        observation_start_at=datetime(2026, 11, 9, 8, tzinfo=UTC),
        observation_end_at=datetime(2026, 11, 9, 9, tzinfo=UTC),
        **changes,
    )
    return TrafficSnapshot(scope, (p,), base.calendar, PROOF)


@pytest.mark.parametrize("mode", ["live", "historical"])
def test_fresh_observation_whole_path(mode: Any) -> None:
    snap = observation_snapshot(mode)
    req = request(
        snap,
        checked_at=REFERENCE if mode == "historical" else datetime(2026, 11, 9, 8, 1, tzinfo=UTC),
    )
    result = evaluate_traffic_path(
        req, TrafficPath((snap.profiles[0].group.edge,), (), PROOF, scope=req.snapshot.scope)
    )
    assert result.known_seconds == 1800 and not result.minimum_proven
    result = verify_traffic_minimum(result, receipt(result))
    inputs = replace(make_inputs(snap), checked_at=req.checked_at)
    output = analyze_traffic_car(
        inputs,
        [result],
        selected_categories={"school": "high"},
        config=load_urban_config(),
        rules=load_urban_journey_rules(),
    )
    category = output.analysis.modes[0].points[0].categories[0]
    assert category.index.value == 50 and category.count.value is None


@pytest.mark.parametrize(
    "case",
    [
        "expired_check",
        "future_entry",
        "unknown_observed",
        "no_fetch",
        "expires_during_edge",
        "missing_historical",
    ],
)
def test_live_historical_applicability_fail_closed(case: str) -> None:
    snap = observation_snapshot("historical" if case == "missing_historical" else "live")
    req = request(snap, checked_at=datetime(2026, 11, 9, 8, 1, tzinfo=UTC))
    p = snap.profiles[0]
    if case == "expired_check":
        req = replace(req, checked_at=datetime(2026, 11, 9, 12, tzinfo=UTC))
    if case == "future_entry":
        req = replace(req, departure_at=req.departure_at + timedelta(days=1))
    if case == "unknown_observed":
        snap = replace(snap, profiles=(replace(p, observed_at=None),))
    if case == "no_fetch":
        snap = replace(snap, profiles=(replace(p, fetched_at=None),))
    if case == "expires_during_edge":
        snap = replace(
            snap, profiles=(replace(p, valid_until=datetime(2026, 11, 9, 8, 5, tzinfo=UTC)),)
        )
    if case == "missing_historical":
        snap = replace(snap, profiles=())
    req = replace(req, snapshot=snap)
    edge = p.group.edge
    result = evaluate_traffic_path(req, TrafficPath((edge,), (), PROOF, scope=req.snapshot.scope))
    assert result.known_seconds is None and result.reasons


def test_typical_week_partial_replay_and_threshold() -> None:
    snap = make_snapshot()
    plan, inputs = typical_plan(snap)
    rows = od_results(plan, inputs)
    before = copy.deepcopy(plan)
    collector = TypicalODCollector(plan, snap, search_scope_id=inputs.search_scope_id)
    collector.add(reversed(rows))
    collector.add(rows)
    week = collector.to_week()
    assert len(week.slices) == 28 and week.typical_profile_accepted and week.traffic_evidence_refs
    tau = calculate_urban_tau(
        inputs.context,
        inputs.points,
        inputs.population,
        [week],
        passenger_mode="private_car",
        timezone="UTC",
        reference_at=REFERENCE,
        rules=load_urban_tau_rules(),
    )
    assert tau.tau_seconds.value == 1200 and tau.to_threshold().traffic_mode == "typical"
    result = evaluation(proven=True)
    output = analyze_traffic_car(
        inputs,
        [result],
        selected_categories={"school": "high"},
        config=load_urban_config(),
        rules=load_urban_journey_rules(),
        thresholds=(tau.to_threshold(),),
    )
    category = output.analysis.modes[0].points[0].categories[0]
    assert category.index.value == 50 and category.count.value == 0
    assert plan == before
    conflict = replace(rows[0][1], minimum_proven=False)
    with pytest.raises(ValueError):
        collector.add(((rows[0][0], conflict),))
    with pytest.raises(ValueError):
        collector.add(
            (
                (
                    rows[0][0],
                    replace(
                        rows[0][1],
                        request=replace(
                            rows[0][1].request, checked_at=REFERENCE + timedelta(seconds=1)
                        ),
                    ),
                ),
            )
        )


@pytest.mark.parametrize("case", ["missing", "unproven", "excluded", "profile_qa"])
def test_unknown_typical_tau_preserves_h_index(case: str) -> None:
    snap = make_snapshot()
    if case == "profile_qa":
        snap = replace(
            snap,
            profiles=tuple(
                replace(p, observation_days=p.observation_days[:4]) for p in snap.profiles
            ),
        )
    plan, inputs = typical_plan(snap)
    rows = od_results(plan, inputs, prove=case != "unproven")
    collector = TypicalODCollector(plan, snap, search_scope_id=inputs.search_scope_id)
    collector.add(rows[1:] if case == "missing" else rows)
    population = (
        replace(inputs.population, complete=False, excluded_population=1)
        if case == "excluded"
        else inputs.population
    )
    tau = calculate_urban_tau(
        inputs.context,
        inputs.points,
        population,
        [collector.to_week()],
        passenger_mode="private_car",
        timezone="UTC",
        reference_at=REFERENCE,
        rules=load_urban_tau_rules(),
    )
    assert tau.tau_seconds.value is None
    if case != "profile_qa":
        result = evaluation(proven=True)
        output = analyze_traffic_car(
            inputs,
            [result],
            selected_categories={"school": "high"},
            config=load_urban_config(),
            rules=load_urban_journey_rules(),
            thresholds=(tau.to_threshold(),),
        )
        c = output.analysis.modes[0].points[0].categories[0]
        assert c.index.value == 50 and c.count.value is None


def test_raw_observation_duplicate_and_conflict_without_last_wins() -> None:
    snap = make_snapshot()
    base = snap.profiles[0]
    group = base.group
    samples = []
    for i, day in enumerate(base.observation_days):
        instant = datetime.combine(day, datetime.min.time(), UTC) + timedelta(
            minutes=group.local_15min_bin * 15
        )
        samples.append(
            TrafficObservation(
                str(i), group, instant, instant + timedelta(seconds=1), 40, False, base.provenance
            )
        )

    def build(rows: list[TrafficObservation]) -> TrafficProfile:
        return build_typical_profile(
            "duplicate-check",
            group,
            rows,
            valid_from=base.valid_from,
            valid_until=base.valid_until,
            calendar=snap.calendar,
            provenance=base.provenance,
        )

    assert build(samples) == build([*reversed(samples), samples[0]])
    with pytest.raises(ValueError, match="CONFLICT"):
        build([*samples, replace(samples[0], observation_id="alias", speed_kph=99)])


def test_utc_midnight_selects_new_calendar_and_interval() -> None:
    snap = make_snapshot()
    a = TrafficEdge("a", "access-A", "mid", 600)
    b = TrafficEdge("b", "mid", "access-school", 600)
    next_day = date(2026, 11, 9)
    profiles = (
        profile(snap, a, 36, 95),
        replace(
            profile(snap, b, 3.6, 0),
            group=replace(profile(snap, b, 3.6, 0).group, day_type="holiday"),
        ),
    )
    calendar = tuple(
        replace(c, day_type="holiday") if c.local_date == next_day else c for c in snap.calendar
    )
    holiday_days = tuple(date(2026, 10, 12) + timedelta(days=i) for i in range(5))
    profiles = (
        profiles[0],
        replace(
            profiles[1],
            observation_days=holiday_days,
            observed_from=holiday_days[0],
            observed_to=holiday_days[-1],
        ),
    )
    calendar = (
        *calendar,
        *(TrafficCalendarDay(d, "holiday", profiles[1].group.season, PROOF) for d in holiday_days),
    )
    snap = replace(snap, profiles=profiles, calendar=calendar)
    req = request(snap, departure_at=datetime(2026, 11, 8, 23, 58, tzinfo=UTC))
    result = evaluate_traffic_path(
        req,
        TrafficPath(
            (a, b), (TrafficTransition("a", "b", 0, False, PROOF),), PROOF, scope=req.snapshot.scope
        ),
    )
    edges = [s for s in result.steps if s.kind == "edge"]
    assert edges[0].start_at.date() == date(2026, 11, 8)
    assert edges[1].start_at == datetime(2026, 11, 9, tzinfo=UTC)
    assert result.known_seconds == 840  # 60+60+600+120


def test_dst_folds_are_distinct_utc_queries_without_rounding() -> None:
    base = make_snapshot()
    scope = replace(base.scope, timezone="Europe/Berlin")
    day = date(2026, 10, 25)
    edge = TrafficEdge("fold", "access-A", "access-school", 600)
    p = profile(base, edge, 36, 10)
    p = replace(
        p,
        group=replace(p.group, scope=scope),
        valid_from=datetime(2026, 10, 24, tzinfo=UTC),
        valid_until=datetime(2026, 10, 27, tzinfo=UTC),
    )
    snap = TrafficSnapshot(
        scope,
        (p,),
        tuple(
            TrafficCalendarDay(d, p.group.day_type, p.group.season, PROOF)
            for d in (*p.observation_days, day)
        ),
        PROOF,
    )
    first = datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
    second = first + timedelta(hours=1)
    assert select_traffic_profile(snap, "fold", first, checked_at=REFERENCE)[0] == p
    assert select_traffic_profile(snap, "fold", second, checked_at=REFERENCE)[0] == p
    q1 = request(snap, departure_at=first)
    q2 = request(snap, departure_at=second)
    assert q1.request_id != q2.request_id
    for query in (q1, q2):
        assert (
            evaluate_traffic_path(
                query, TrafficPath((edge,), (), PROOF, scope=query.snapshot.scope)
            ).known_seconds
            == 240
        )
    with pytest.raises(ValueError, match="AWARE"):
        replace(q1, departure_at=datetime(2026, 10, 25, 2, 30))


@pytest.mark.parametrize("mode", ["live", "historical"])
def test_explicit_source_time_cannot_be_replaced_by_fetch_or_similar_day(mode: Any) -> None:
    snap = observation_snapshot(mode)
    profile0 = snap.profiles[0]
    # Broad applicability metadata must not extend actual historical source coverage.
    if mode == "historical":
        snap = replace(
            snap, profiles=(replace(profile0, valid_until=datetime(2026, 11, 11, tzinfo=UTC)),)
        )
        req = request(
            snap, departure_at=datetime(2026, 11, 10, 8, tzinfo=UTC), checked_at=REFERENCE
        )
    else:
        snap = replace(snap, profiles=(replace(profile0, observed_at=None),))
        req = request(snap, checked_at=datetime(2026, 11, 9, 8, 1, tzinfo=UTC))
    result = evaluate_traffic_path(
        req, TrafficPath((profile0.group.edge,), (), PROOF, scope=req.snapshot.scope)
    )
    assert result.known_seconds is None
    assert result.reasons[0] in ("OBSERVED_AT_UNKNOWN", "HISTORICAL_OBSERVATION_NOT_IN_COVERAGE")


def test_costing_and_traffic_content_are_bound_to_receipt() -> None:
    result = evaluation()
    snap = result.request.snapshot
    with pytest.raises(ValueError, match="COSTING_CONTENT_HASH"):
        replace(
            snap.scope,
            costing_options_json='{"costing":"auto","speed_source":"accepted_snapshot","different":true}',
        )
    changed = replace(snap, profiles=tuple(replace(p, speed_kph=18) for p in snap.profiles))
    req = replace(result.request, snapshot=changed)
    different = evaluate_traffic_path(req, result.path)
    assert different.known_seconds != result.known_seconds
    with pytest.raises(ValueError, match="BINDING"):
        verify_traffic_minimum(different, receipt(result))


def test_static_minimum_object_is_not_a_dynamic_receipt() -> None:
    from a2_engine import plan_car_routes

    old = runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples/synthetic_car.py"))
    plan = plan_car_routes(old["synthetic_inputs"](), categories=("school",))
    static = old["synthetic_response"](plan.requests[0], proven=True)
    assert static.search_complete and static.minimum_time_evidence_refs
    with pytest.raises(ValueError, match="DYNAMIC_MINIMUM"):
        verify_traffic_minimum(evaluation(), static)


def test_known_faster_alternative_contradicts_slow_minimum_receipt() -> None:
    base = make_snapshot()
    fast = TrafficEdge("fast", "access-A", "access-school", 6000)
    base = replace(base, profiles=(*base.profiles, profile(base, fast, 36, 32)))
    inputs = make_inputs(base)
    req = traffic_car_requests(inputs, categories=("school",))[0]
    slow = next(p.group.edge for p in base.profiles if p.group.edge.edge_id == "as")
    slow_result = evaluate_traffic_path(
        req, TrafficPath((slow,), (), PROOF, scope=req.snapshot.scope)
    )
    slow_result = verify_traffic_minimum(slow_result, receipt(slow_result))
    fast_result = evaluate_traffic_path(
        req, TrafficPath((fast,), (), PROOF, scope=req.snapshot.scope)
    )
    assert fast_result.known_seconds == 780 and not fast_result.minimum_proven
    with pytest.raises(ValueError, match="CONTRADICTS"):
        analyze_traffic_car(
            inputs,
            [slow_result, fast_result],
            selected_categories={"school": "high"},
            config=load_urban_config(),
            rules=load_urban_journey_rules(),
        )


def test_aggregate_qa_calendar_is_checked_without_claiming_median_recompute() -> None:
    snap = make_snapshot()
    profile0 = snap.profiles[0]
    altered = tuple(
        replace(day, season="winter") if day.local_date == profile0.observation_days[0] else day
        for day in snap.calendar
    )
    snap = replace(snap, calendar=altered)
    assert not snap.typical_accepted
    assert snap.profile_reason(profile0) == "OBSERVATION_CALENDAR_GROUP_MISMATCH"
    missing = replace(
        snap,
        calendar=tuple(d for d in snap.calendar if d.local_date != profile0.observation_days[0]),
    )
    assert missing.profile_reason(profile0) == "OBSERVATION_CALENDAR_UNAVAILABLE"


def test_historical_source_interval_covers_entire_edge() -> None:
    snap = observation_snapshot("historical")
    p = replace(snap.profiles[0], observation_end_at=datetime(2026, 11, 9, 8, 5, tzinfo=UTC))
    snap = replace(snap, profiles=(p,))
    req = request(snap, checked_at=REFERENCE)
    result = evaluate_traffic_path(req, TrafficPath((p.group.edge,), (), PROOF, scope=snap.scope))
    assert result.reasons == ("HISTORICAL_SOURCE_COVERAGE_ENDS_DURING_EDGE",)
    p = replace(p, observation_start_at=None, observation_end_at=None)
    snap = replace(snap, profiles=(p,))
    result = evaluate_traffic_path(
        request(snap), TrafficPath((p.group.edge,), (), PROOF, scope=snap.scope)
    )
    assert result.reasons == ("HISTORICAL_SOURCE_COVERAGE_UNKNOWN",)


def test_traffic_path_must_bind_graph_build_and_temporal_policy() -> None:
    result = evaluation()
    path = replace(result.path, scope=replace(result.path.scope, graph_version="wrong-graph"))
    with pytest.raises(ValueError, match="GRAPH_OR_POLICY"):
        evaluate_traffic_path(result.request, path)
    with pytest.raises(ValueError, match="ASSUMPTIONS"):
        replace(receipt(result), assumptions_evidence_refs=())
    unproven = verify_traffic_minimum(result, replace(receipt(result), search_complete=False))
    assert (
        unproven.profile_complete and unproven.known_seconds == 1800 and not unproven.minimum_proven
    )


def test_generalized_cost_penalty_is_not_elapsed_time() -> None:
    import json

    from a2_engine.routing.road import road_hash

    base = make_snapshot()
    options = json.loads(base.scope.costing_options_json)
    options["generalized_penalty"] = 900000
    scope = replace(
        base.scope,
        costing_options_json=json.dumps(options),
        costing_profile_hash=road_hash(
            [base.scope.engine_version, options, base.scope.temporal_policy]
        ),
    )
    profiles = tuple(replace(p, group=replace(p.group, scope=scope)) for p in base.profiles)
    snap = TrafficSnapshot(scope, profiles, base.calendar, PROOF)
    req = request(snap)
    edge = next(p.group.edge for p in snap.profiles if p.group.edge.edge_id == "as")
    result = evaluate_traffic_path(req, TrafficPath((edge,), (), PROOF, scope=scope))
    assert result.known_seconds == 1800 and not result.minimum_proven


def test_typical_od_two_stage_mean_not_pooling_pair_samples() -> None:
    from a2_engine.urban.od.collection import UrbanODCollector
    from a2_engine.urban.od.models import UrbanODObservation
    from a2_engine.urban.tau.models import UrbanODPair

    # The calculator's original confirmed-unreachable contract remains independent
    # of the supplied-path traffic evaluator, which never invents this state.
    snap = make_snapshot()
    plan, inputs = typical_plan(snap)
    collector = UrbanODCollector(plan)
    for task in plan.tasks():
        late = task.departure_at in plan.departures[14:]
        seconds = (3600 if late else 600) if task.origin.point_id == "A" else 1200
        unreachable = late and task.origin.point_id == "B"
        pair = UrbanODPair(
            task.origin.point_id,
            task.destination.point_id,
            "confirmed_unreachable" if unreachable else "reachable",
            None if unreachable else seconds,
            True,
            True,
            PROOF,
        )
        collector.add((UrbanODObservation(task, pair),))
    week = replace(
        collector.to_week(),
        typical_profile_accepted=snap.typical_accepted,
        traffic_evidence_refs=snap.acceptance_evidence_refs,
    )
    result = calculate_urban_tau(
        inputs.context,
        inputs.points,
        inputs.population,
        [week],
        passenger_mode="private_car",
        timezone="UTC",
        reference_at=REFERENCE,
        rules=load_urban_tau_rules(),
    )
    assert result.tau_seconds.value == 2250


def test_typical_od_search_scope_cannot_be_substituted() -> None:
    snap = make_snapshot()
    plan, inputs = typical_plan(snap)
    task = next(plan.tasks())
    entries = dict(inputs.entries)
    exits = {
        p.point_id: replace(entries[p.point_id], direction="exit", seconds=120)
        for p in inputs.points
    }
    with pytest.raises(ValueError, match="PROFILE_MISMATCH"):
        traffic_od_request(
            task, snap, entries, exits, checked_at=REFERENCE, search_scope_id="other-search-area"
        )
    with pytest.raises(ValueError, match="COLLECTION_SCOPE"):
        TypicalODCollector(plan, snap, search_scope_id="other-search-area")
    with pytest.raises(ValueError, match="CONTEXT_MISMATCH"):
        replace(
            inputs,
            context=replace(
                inputs.context,
                versions=replace(inputs.context.versions, calendar_version="other-calendar"),
            ),
        )


def test_historical_does_not_claim_future_measurement_window_at_fetch() -> None:
    snap = observation_snapshot("historical")
    p = replace(snap.profiles[0], fetched_at=datetime(2026, 11, 9, 8, 0, 1, tzinfo=UTC))
    snap = replace(snap, profiles=(p,))
    result = evaluate_traffic_path(
        request(snap, checked_at=REFERENCE),
        TrafficPath((p.group.edge,), (), PROOF, scope=snap.scope),
    )
    assert result.known_seconds is None
    assert result.reasons == ("HISTORICAL_SOURCE_NOT_COMPLETE_AT_FETCH",)
