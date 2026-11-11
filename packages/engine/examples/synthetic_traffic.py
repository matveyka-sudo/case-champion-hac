"""Synthetic traffic evidence/receipts ONLY; no production executor or real city."""

from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal

from a2_engine import (
    TrafficCalendarDay,
    TrafficCarInputs,
    TrafficEdge,
    TrafficEndpoint,
    TrafficGroup,
    TrafficMinimumReceipt,
    TrafficObservation,
    TrafficPath,
    TrafficPathResult,
    TrafficProvenance,
    TrafficScope,
    TrafficSnapshot,
    TrafficTransition,
    TypicalODCollector,
    UrbanArea,
    UrbanContext,
    UrbanDestination,
    UrbanODScope,
    UrbanPoint,
    UrbanPopulationCoverage,
    UrbanTauDay,
    UrbanVersions,
    analyze_traffic_car,
    build_typical_profile,
    calculate_urban_tau,
    evaluate_traffic_path,
    load_urban_config,
    load_urban_journey_rules,
    load_urban_tau_rules,
    plan_typical_od_week,
    traffic_car_requests,
    traffic_od_request,
    verify_traffic_minimum,
)
from a2_engine.routing.road import road_hash

PROOF = ("SYNTHETIC-not-production",)
START = date(2026, 11, 2)
REFERENCE = datetime(2026, 11, 9, 12, tzinfo=UTC)


def synthetic_snapshot() -> TrafficSnapshot:
    scope = TrafficScope(
        "synthetic-base",
        "synthetic-source-graph",
        "synthetic-graph",
        "synthetic-build",
        "synthetic-typical-v1",
        "city",
        "UTC",
        "typical",
        road_hash(
            [
                "synthetic-executor-v1",
                {"costing": "auto", "speed_source": "accepted_snapshot"},
                "entry_speed_v1",
            ]
        ),
        "entry_speed_v1",
        PROOF,
        "synthetic-executor-v1",
        '{"costing":"auto","speed_source":"accepted_snapshot"}',
        "synthetic-calendar",
    )
    source = TrafficProvenance(
        "synthetic-source", "synthetic-measured-speed", "synthetic-license", "accepted", PROOF
    )
    sample_dates = tuple(date(2026, 10, 19) + timedelta(days=i) for i in range(5))
    application_dates = tuple(START + timedelta(days=i) for i in range(9))
    calendar = tuple(
        TrafficCalendarDay(d, "ordinary", "autumn", PROOF)
        for d in (*sample_dates, *application_dates)
    )
    edges = (
        TrafficEdge("ab", "access-A", "access-B", 4200),
        TrafficEdge("ba", "access-B", "access-A", 16200),
        TrafficEdge("as", "access-A", "access-school", 16200),
        TrafficEdge("bs", "access-B", "access-school", 16200),
    )
    profiles = []
    for edge in edges:
        for hour in (8, 12, 18, 22):
            group = TrafficGroup(scope, edge, "ordinary", "autumn", hour * 4)
            samples = tuple(
                TrafficObservation(
                    f"{edge.edge_id}-{hour}-{d}",
                    group,
                    datetime.combine(d, time(hour), UTC),
                    datetime.combine(d, time(hour), UTC) + timedelta(seconds=1),
                    float(speed),
                    False,
                    source,
                )
                for d, speed in zip(sample_dates, (16, 26, 36, 46, 56), strict=True)
            )
            profiles.append(
                build_typical_profile(
                    edge.edge_id + "-" + str(hour),
                    group,
                    samples,
                    valid_from=datetime(2026, 11, 2, tzinfo=UTC),
                    valid_until=datetime(2026, 11, 11, tzinfo=UTC),
                    calendar=calendar,
                    provenance=source,
                )
            )
    return TrafficSnapshot(scope, tuple(profiles), calendar, PROOF)


def synthetic_inputs(snapshot: TrafficSnapshot) -> TrafficCarInputs:
    scope = snapshot.scope
    versions = UrbanVersions(
        scope.snapshot_id,
        scope.graph_version,
        "synthetic-calendar",
        "synthetic-population",
        "synthetic-geometry",
        "a2-methodology-v3",
        "synthetic-config",
        snapshot.version_key,
    )
    context = UrbanContext(
        "city",
        "city",
        datetime(2026, 11, 9, 8, tzinfo=UTC),
        scope.mode,
        versions,
        "synthetic-search",
    )
    points = tuple(
        UrbanPoint(
            p,
            "cell-" + p,
            "physical-" + p,
            "access-" + p,
            20,
            "city",
            population,
            2026,
            "synthetic",
            PROOF,
        )
        for p, population in (("A", 90), ("B", 10))
    )
    school = UrbanDestination(
        "school",
        "school",
        frozenset({"school"}),
        "access-school",
        "city",
        True,
        True,
        PROOF,
        physical_node_id="physical-school",
    )
    entries = tuple(
        (
            p.point_id,
            TrafficEndpoint(
                scope,
                p.origin_node_id,
                p.access_node_id,
                "correlation-" + p.point_id,
                "entry",
                60,
                PROOF,
            ),
        )
        for p in points
    )
    exit = TrafficEndpoint(
        scope, "physical-school", "access-school", "correlation-school", "exit", 120, PROOF
    )
    return TrafficCarInputs(
        context,
        snapshot,
        REFERENCE,
        "SYNTHETIC-exhaustive-city",
        points,
        (school,),
        entries,
        (("school", exit),),
        UrbanArea("city", 200, "synthetic-geometry", PROOF),
        UrbanPopulationCoverage(context, True, 0, PROOF, frozenset({"A", "B"})),
        (("school", True, PROOF),),
    )


def synthetic_receipt(result: TrafficPathResult) -> TrafficMinimumReceipt:
    # Only this test/example creates fake trusted search attestations.
    return TrafficMinimumReceipt(
        result.request.request_id,
        result.request.snapshot.content_hash,
        result.path.path_hash,
        result.evaluation_hash,
        True,
        True,
        "exhaustive",
        PROOF,
        PROOF,
    )


def synthetic_time_of_entry() -> TrafficPathResult:
    base = synthetic_snapshot()
    first = TrafficEdge("first", "access-A", "middle", 600)
    second = TrafficEdge("second", "middle", "access-school", 600)
    p = base.profiles[0]
    profiles = tuple(
        replace(
            p,
            profile_id=name,
            group=replace(p.group, edge=edge, local_15min_bin=slot),
            speed_kph=speed,
            aggregation="accepted_aggregate",
            samples_hash=None,
        )
        for name, edge, slot, speed in (
            ("first-32", first, 32, 36),
            ("second-32", second, 32, 36),
            ("second-33", second, 33, 3.6),
        )
    )
    snapshot = replace(base, profiles=profiles)
    inputs = synthetic_inputs(snapshot)
    request = replace(
        traffic_car_requests(inputs, categories=("school",))[0],
        departure_at=datetime(2026, 11, 9, 8, 13, tzinfo=UTC),
        destination=replace(inputs.exits[0][1], seconds=60),
    )
    return evaluate_traffic_path(
        request,
        TrafficPath(
            (first, second),
            (TrafficTransition("first", "second", 0, False, PROOF),),
            PROOF,
            scope=request.snapshot.scope,
        ),
    )


def synthetic_observed_snapshot(mode: Literal["live", "historical"]) -> TrafficSnapshot:
    base = synthetic_snapshot()
    scope = replace(base.scope, mode=mode, traffic_version="synthetic-" + mode + "-v1")
    p = next(
        p for p in base.profiles if p.group.edge.edge_id == "as" and p.group.local_15min_bin == 32
    )
    start, end = datetime(2026, 11, 9, 8, tzinfo=UTC), datetime(2026, 11, 9, 9, tzinfo=UTC)
    p = replace(
        p,
        group=replace(p.group, scope=scope),
        aggregation="observation",
        samples_hash=None,
        observation_days=(),
        valid_from=start,
        valid_until=end,
        observed_from=start.date(),
        observed_to=start.date(),
        observed_at=start,
        fetched_at=(end if mode == "historical" else start) + timedelta(seconds=1),
        observation_start_at=start,
        observation_end_at=end,
    )
    return TrafficSnapshot(scope, (p,), base.calendar, PROOF)


def main() -> None:
    entry = synthetic_time_of_entry()
    assert entry.known_seconds == 780
    print("SYNTHETIC entry-time: 08:13 + walk60 + edge60 + edge600 + walk60 = 780s")
    for mode in ("live", "historical"):
        observed = synthetic_observed_snapshot(mode)
        inputs_observed = replace(
            synthetic_inputs(observed),
            checked_at=REFERENCE
            if mode == "historical"
            else datetime(2026, 11, 9, 8, 1, tzinfo=UTC),
        )
        query = traffic_car_requests(inputs_observed, categories=("school",))[0]
        known = evaluate_traffic_path(
            query,
            TrafficPath((observed.profiles[0].group.edge,), (), PROOF, scope=query.snapshot.scope),
        )
        assert known.known_seconds == 1800
        proven = verify_traffic_minimum(known, synthetic_receipt(known))
        observed_result = analyze_traffic_car(
            inputs_observed,
            [proven],
            selected_categories={"school": "high"},
            config=load_urban_config(),
            rules=load_urban_journey_rules(),
        )
        category = observed_result.analysis.modes[0].points[0].categories[0]
        assert category.index.value == 50 and category.count.value is None
        print(f"SYNTHETIC {mode}: time=1800s H-index=50 count=unknown")
    snapshot = synthetic_snapshot()
    inputs = synthetic_inputs(snapshot)
    results = []
    for request in traffic_car_requests(inputs, categories=("school",)):
        edge = next(
            p.group.edge
            for p in snapshot.profiles
            if p.group.edge.edge_id == ("as" if request.origin_id == "A" else "bs")
        )
        known = evaluate_traffic_path(
            request, TrafficPath((edge,), (), PROOF, scope=request.snapshot.scope)
        )
        assert known.known_seconds == 1800 and not known.minimum_proven
        results.append(verify_traffic_minimum(known, synthetic_receipt(known)))
    rules = load_urban_journey_rules()
    without_receipt = analyze_traffic_car(
        inputs,
        [evaluate_traffic_path(r.request, r.path) for r in results],
        selected_categories={"school": "high"},
        config=load_urban_config(),
        rules=rules,
    )
    assert without_receipt.analysis.modes[0].points[0].index.value is None
    assert without_receipt.analysis.modes[0].points[0].categories[0].best is not None
    scope = UrbanODScope(
        "city",
        "private_car",
        inputs.context.versions,
        "typical",
        "synthetic-search",
        "UTC",
        frozenset({"A", "B"}),
        rules.max_total_wait_seconds,
    )
    calendar = tuple(
        UrbanTauDay(START + timedelta(days=i), True, False, True, True, PROOF) for i in range(7)
    )
    plan = plan_typical_od_week(
        scope,
        inputs.points,
        snapshot,
        week_start=START,
        reference_at=REFERENCE,
        calendar=calendar,
        rules=load_urban_tau_rules(),
        evidence_refs=PROOF,
        search_scope_id=inputs.search_scope_id,
    )
    entries = dict(inputs.entries)
    exits = {
        p.point_id: TrafficEndpoint(
            snapshot.scope,
            p.origin_node_id,
            p.access_node_id,
            "correlation-" + p.point_id,
            "exit",
            120,
            PROOF,
        )
        for p in inputs.points
    }
    collector = TypicalODCollector(plan, snapshot, search_scope_id=inputs.search_scope_id)
    for batch in plan.batches(9):
        rows = []
        for task in batch:
            request = traffic_od_request(
                task,
                snapshot,
                entries,
                exits,
                checked_at=REFERENCE,
                search_scope_id=inputs.search_scope_id,
            )
            edge = next(
                p.group.edge
                for p in snapshot.profiles
                if p.group.edge.edge_id == ("ab" if task.origin.point_id == "A" else "ba")
            )
            known = evaluate_traffic_path(
                request, TrafficPath((edge,), (), PROOF, scope=request.snapshot.scope)
            )
            rows.append((task, verify_traffic_minimum(known, synthetic_receipt(known))))
        collector.add(reversed(rows))
    week = collector.to_week()
    tau = calculate_urban_tau(
        inputs.context,
        inputs.points,
        inputs.population,
        (week,),
        passenger_mode="private_car",
        timezone="UTC",
        reference_at=REFERENCE,
        rules=load_urban_tau_rules(),
    )
    assert tau.tau_seconds.value == 1200, tau.reasons
    output = analyze_traffic_car(
        inputs,
        results,
        selected_categories={"school": "high"},
        config=load_urban_config(),
        rules=rules,
        thresholds=(tau.to_threshold(),),
    )
    assert output.analysis.modes[0].index.value == 50
    assert output.analysis.modes[0].points[0].categories[0].count.value == 0
    print(
        "SYNTHETIC typical: car=1800s H-index=50; no receipt => best-known/unknown index; tau=1200s count=0"
    )


if __name__ == "__main__":
    main()
