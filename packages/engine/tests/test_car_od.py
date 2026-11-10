"""Planner/replay/collector controls; every transport proof here is synthetic."""

import copy
import json
import runpy
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from a2_engine import (
    CarInputs,
    RoadEndpoint,
    RoadResponse,
    UrbanContext,
    UrbanODCollector,
    UrbanODObservation,
    UrbanODPlan,
    UrbanPopulationCoverage,
    analyze_car_responses,
    assemble_urban_od_week,
    calculate_urban_tau,
    load_urban_config,
    load_urban_journey_rules,
    load_urban_tau_rules,
    normalize_road_response,
    od_road_observation,
    od_road_request,
    od_timetable_observation,
    plan_car_routes,
    plan_urban_od_week,
)
from a2_engine.routing.road import road_hash
from a2_engine.routing.timetable_models import Timetable, TimetableFootpath, TimetableScope
from a2_engine.urban.car import car_candidate
from a2_engine.urban.journeys import validate_urban_journey

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
car = runpy.run_path(str(EXAMPLES / "synthetic_car.py"))
od = runpy.run_path(str(EXAMPLES / "synthetic_od.py"))
inputs = cast(Callable[[], CarInputs], car["synthetic_inputs"])
response = cast(Callable[..., RoadResponse], car["synthetic_response"])
make_plan = cast(
    Callable[
        [],
        tuple[
            UrbanODPlan,
            dict[str, RoadEndpoint],
            dict[str, RoadEndpoint],
            UrbanContext,
            UrbanPopulationCoverage,
        ],
    ],
    od["synthetic_plan"],
)
observations = cast(Callable[..., tuple[UrbanODObservation, ...]], od["synthetic_observations"])


def test_car_full_physical_chain_and_unknown_tau() -> None:
    data = inputs()
    before = copy.deepcopy(data)
    plan = plan_car_routes(data, categories=("school",))
    req = plan.requests[0]
    assert req.payload()["date_time"]["value"].endswith("08:01")
    assert req.payload()["costing_options"]["auto"]["speed_types"] == ["freeflow"]
    result = analyze_car_responses(
        plan,
        (response(req),),
        selected_categories={"school": "high"},
        config=load_urban_config(),
        rules=load_urban_journey_rules(),
    )
    category = result.analysis.modes[0].points[0].categories[0]
    assert category.index.value == 50
    assert category.count.value is None
    assert category.best is not None
    timing = category.best.journey
    assert (
        timing.total_seconds,
        timing.walking_seconds,
        timing.riding_seconds,
        timing.total_wait_seconds,
    ) == (1800, 180, 1620, 0)
    assert timing.candidate.geometry is None
    assert data == before


@pytest.mark.parametrize("proven", [False, True])
def test_minimum_requires_independent_proof_not_cost(proven: bool) -> None:
    plan = plan_car_routes(inputs(), categories=("school",))
    req = plan.requests[0]
    env = response(req, 700, proven=proven)
    body = json.loads(cast(str, env.response_json))
    body["trip"]["summary"]["cost"] = 700  # Another path may have time=600,cost=900.
    env = replace(env, response_json=json.dumps(body))
    result = analyze_car_responses(
        plan,
        (env,),
        selected_categories={"school": "high"},
        config=load_urban_config(),
        rules=load_urban_journey_rules(),
    )
    category = result.analysis.modes[0].points[0].categories[0]
    assert result.routes[0].network_seconds == 700
    assert category.best is not None
    assert category.minimum_proven == proven
    assert (category.index.value is not None) == proven


@pytest.mark.parametrize("seconds", [1620.123456, 0.000001, 0.00000001])
def test_fractional_chain(seconds: float) -> None:
    data = inputs()
    req = plan_car_routes(data, categories=("school",)).requests[0]
    if seconds < 0.0000005:
        with pytest.raises(ValueError, match="RESOLUTION"):
            normalize_road_response(req, response(req, seconds))
        return
    route = normalize_road_response(req, response(req, seconds))
    candidate = car_candidate(route, data.context, "school")
    timing = validate_urban_journey(candidate, rules=load_urban_journey_rules())
    assert timing.total_seconds == pytest.approx(180 + seconds, abs=1e-6)
    assert all(
        (leg.end_at - leg.start_at).total_seconds() == leg.duration_seconds
        for leg in candidate.legs
    )


@pytest.mark.parametrize("change", ["reverse", "pedestrian", "graph", "physical", "traffic"])
def test_bad_car_bindings(change: str) -> None:
    data = inputs()
    endpoint = data.entries[0][1]
    with pytest.raises(ValueError):
        if change == "reverse":
            endpoint = replace(endpoint, direction="exit")
        if change == "pedestrian":
            endpoint = replace(
                endpoint,
                build=replace(
                    endpoint.build, costing="pedestrian", options_json='{"walking_speed":5}'
                ),
            )
        if change == "graph":
            endpoint = replace(endpoint, build=replace(endpoint.build, graph_version="other"))
        if change == "physical":
            endpoint = replace(endpoint, physical_node_id="foreign")
        if change == "traffic":
            replace(data, context=replace(data.context, traffic_mode="typical"))
        else:
            replace(data, entries=(("A", endpoint),))


def test_missing_connector_and_subminute_not_invented() -> None:
    data = inputs()
    assert not plan_car_routes(replace(data, entries=()), categories=("school",)).requests
    endpoint = replace(data.entries[0][1], seconds=60.125)
    plan = plan_car_routes(replace(data, entries=(("A", endpoint),)), categories=("school",))
    assert not plan.requests
    assert plan.issues[0][2] == "VALHALLA_SUBMINUTE_ENTRY_UNSUPPORTED"


@pytest.mark.parametrize("change", ["id", "hash", "profile", "endpoint", "missing_endpoint"])
def test_road_response_binding(change: str) -> None:
    req = plan_car_routes(inputs(), categories=("school",)).requests[0]
    env = response(req)
    if change == "id":
        env = replace(env, request_id="other")
    if change == "hash":
        env = replace(env, payload_hash="other")
    if change == "profile":
        env = replace(env, profile_hash="other")
    if change == "endpoint":
        env = replace(env, matched_origin=req.destination.coordinate)
    if change == "missing_endpoint":
        result = normalize_road_response(req, replace(env, endpoint_evidence_refs=()))
        assert result.state == "unknown"
    else:
        with pytest.raises(ValueError):
            normalize_road_response(req, env)


@pytest.mark.parametrize("mode", ["typical", "live", "historical"])
def test_dynamic_profile_never_static(mode: Any) -> None:
    build = inputs().entries[0][1].build
    with pytest.raises(ValueError):
        replace(build, traffic_mode=mode)
    with pytest.raises(ValueError):
        replace(build, options_json='{"speed_types":["current","predicted"]}')


@pytest.mark.parametrize("two_stage,expected", [(False, 1200), (True, 2250)])
def test_planned_directed_tau_two_stage(two_stage: bool, expected: float) -> None:
    plan, entries, exits, context, population = make_plan()
    rows = observations(plan, entries, exits, two_stage=two_stage)
    assert len(rows) == 56 and len(plan.departures) == 28
    assert [len(batch) for batch in plan.batches(20)] == [20, 20, 16]
    before = copy.deepcopy(plan)
    collector = UrbanODCollector(plan)
    collector.add(reversed(rows))
    collector.add(rows)  # Identical replay.
    week = collector.to_week()
    assert week == assemble_urban_od_week(plan, rows)
    tau = calculate_urban_tau(
        context,
        plan.points,
        population,
        (week,),
        passenger_mode="private_car",
        timezone="UTC",
        reference_at=plan.reference_at,
        rules=load_urban_tau_rules(),
    )
    assert tau.tau_seconds.value == expected
    assert tau.to_threshold().versions == context.versions
    assert plan == before


def test_missing_positive_pair_blocks_tau_keeps_other_pairs() -> None:
    plan, entries, exits, context, population = make_plan()
    rows = observations(plan, entries, exits)
    week = assemble_urban_od_week(plan, rows[1:])
    assert len(week.slices) == 28 and sum(len(s.pairs) for s in week.slices) == 55
    tau = calculate_urban_tau(
        context,
        plan.points,
        population,
        (week,),
        passenger_mode="private_car",
        timezone="UTC",
        reference_at=plan.reference_at,
        rules=load_urban_tau_rules(),
    )
    assert tau.tau_seconds.value is None
    assert "UNKNOWN_POSITIVE_OD_WEIGHT" in tau.reasons


def test_conflicting_and_foreign_retry_rejected() -> None:
    plan, entries, exits, _, _ = make_plan()
    row = observations(plan, entries, exits)[0]
    collector = UrbanODCollector(plan)
    collector.add((row,))
    with pytest.raises(ValueError, match="CONFLICT"):
        collector.add((replace(row, pair=replace(row.pair, seconds=999)),))
    with pytest.raises(ValueError, match="FOREIGN"):
        collector.add((replace(row, task=replace(row.task, profile_hash="other")),))
    with pytest.raises(ValueError, match="FOREIGN"):
        collector.add(
            (
                replace(
                    row,
                    task=replace(
                        row.task, departure_at=row.task.departure_at + timedelta(minutes=1)
                    ),
                ),
            )
        )


@pytest.mark.parametrize("weight,expected", [(0, 0), (None, 56)])
def test_zero_and_unknown_population_roster(weight: float | None, expected: int) -> None:
    plan, _, _, _, _ = make_plan()
    points = (replace(plan.points[0], population=weight), plan.points[1])
    changed = plan_urban_od_week(
        plan.scope,
        points,
        week_start=plan.week_start,
        reference_at=plan.reference_at,
        calendar=plan.calendar,
        rules=load_urban_tau_rules(),
        profile_hash=plan.profile_hash,
        evidence_refs=plan.evidence_refs,
    )
    assert sum(1 for _ in changed.tasks()) == expected
    assert len(changed.points) == 2


def test_shared_access_not_diagonal_and_not_reused_across_time() -> None:
    plan, entries, exits, _, _ = make_plan()
    requests = [od_road_request(task, entries, exits) for task in plan.tasks()]
    assert len({r.request_id for r in requests}) == 56
    assert len({r.payload()["date_time"]["value"] for r in requests}) == 28
    assert requests[0].origin.coordinate == requests[0].destination.coordinate
    assert requests[0].origin_id != requests[0].destination_id
    with pytest.raises(ValueError):
        od_road_observation(
            next(plan.tasks()), normalize_road_response(requests[1], response(requests[1]))
        )


@pytest.mark.parametrize("mode", ["walk", "public_transport"])
def test_other_od_modes_use_real_route_boundaries(mode: Any) -> None:
    plan, entries, exits, context, population = make_plan()
    scope = replace(plan.scope, passenger_mode=mode)
    if mode == "walk":
        build = replace(
            entries["A"].build, costing="pedestrian", options_json='{"walking_speed":5}'
        )
        entries = {k: replace(v, build=build) for k, v in entries.items()}
        exits = {k: replace(v, build=build) for k, v in exits.items()}
        fingerprint = build.profile_hash
    else:
        ts = TimetableScope(
            scope.versions.snapshot_id,
            scope.versions.graph_version,
            scope.versions.calendar_version,
            "table",
            "foot",
            "transfer",
            "UTC",
            plan.departures[0],
            plan.departures[-1] + timedelta(days=1),
            frozenset({"A", "B"}),
            True,
            True,
            True,
            True,
            True,
            ("synthetic-timetable",),
        )
        timetable = Timetable(
            ts,
            (),
            (
                TimetableFootpath("ab", "A", "B", 600, True, ("synthetic-foot",)),
                TimetableFootpath("ba", "B", "A", 1800, True, ("synthetic-foot",)),
            ),
            (),
        )
        fingerprint = road_hash(timetable)
    plan = plan_urban_od_week(
        scope,
        plan.points,
        week_start=plan.week_start,
        reference_at=plan.reference_at,
        calendar=plan.calendar,
        rules=load_urban_tau_rules(),
        profile_hash=fingerprint,
        evidence_refs=plan.evidence_refs,
    )
    rows = []
    for task in plan.tasks():
        if mode == "walk":
            req = od_road_request(task, entries, exits)
            rows.append(
                od_road_observation(
                    task,
                    normalize_road_response(
                        req, response(req, 600 if task.origin.point_id == "A" else 1800)
                    ),
                )
            )
        else:
            rows.append(od_timetable_observation(task, timetable))
    week = assemble_urban_od_week(plan, rows)
    tau = calculate_urban_tau(
        context,
        plan.points,
        population,
        (week,),
        passenger_mode=mode,
        timezone="UTC",
        reference_at=plan.reference_at,
        rules=load_urban_tau_rules(),
    )
    assert tau.tau_seconds.value == 1200


def test_interrupted_road_preserves_best_known_without_tau_value() -> None:
    plan, entries, exits, _, _ = make_plan()
    task = next(plan.tasks())
    req = od_road_request(task, entries, exits)
    row = od_road_observation(task, normalize_road_response(req, response(req, 700, proven=False)))
    assert row.pair.state == "unknown" and row.pair.seconds is None
    assert row.best_known_seconds == 700
    collector = UrbanODCollector(plan)
    collector.add((row,))
    assert collector.to_week().slices[0].pairs[0].state == "unknown"


@pytest.mark.parametrize("outcome", ["timeout", "cancelled", "limit", "http_error"])
def test_missing_road_never_no_route(outcome: Any) -> None:
    req = plan_car_routes(inputs(), categories=("school",)).requests[0]
    result = normalize_road_response(
        req, replace(response(req), outcome=outcome, response_json=None)
    )
    assert result.state == "unknown" and result.network_seconds is None


@pytest.mark.parametrize("value", [None, -1, float("nan"), True])
def test_invalid_network_cell_is_unknown(value: Any) -> None:
    req = plan_car_routes(inputs(), categories=("school",)).requests[0]
    env = response(req)
    body = json.loads(cast(str, env.response_json))
    body["trip"]["summary"]["time"] = value
    result = normalize_road_response(req, replace(env, response_json=json.dumps(body)))
    assert result.state == "unknown" and result.total_seconds is None


def test_car_catalog_complete_roster_and_retry() -> None:
    data = inputs()
    other = replace(data.destinations[0], destination_id="other", object_identity="other")
    data = replace(
        data,
        destinations=(*data.destinations, other),
        exits=(*data.exits, ("other", data.exits[0][1])),
    )
    plan = plan_car_routes(data, categories=("school",))
    assert len(plan.requests) == 2
    env = response(plan.requests[0])
    result = analyze_car_responses(
        plan,
        (env, env),
        selected_categories={"school": "high"},
        config=load_urban_config(),
        rules=load_urban_journey_rules(),
    )
    assert result.analysis.modes[0].points[0].categories[0].index.value is None
    with pytest.raises(ValueError, match="CONFLICT"):
        analyze_car_responses(
            plan,
            (env, response(plan.requests[0], 999)),
            selected_categories={"school": "high"},
            config=load_urban_config(),
            rules=load_urban_journey_rules(),
        )


def test_same_access_different_point_ids_still_directed() -> None:
    plan, _, _, _, _ = make_plan()
    points = tuple(replace(p, access_node_id="shared") for p in plan.points)
    changed = plan_urban_od_week(
        plan.scope,
        points,
        week_start=plan.week_start,
        reference_at=plan.reference_at,
        calendar=plan.calendar,
        rules=load_urban_tau_rules(),
        profile_hash=plan.profile_hash,
        evidence_refs=plan.evidence_refs,
    )
    assert len(tuple(changed.tasks())) == 56


def test_od_threshold_drives_car_count_without_changing_h() -> None:
    data = inputs()
    # Same baseline versions/profile as car, independent of institutions/categories.
    old_plan, _, _, _, _ = make_plan()
    a = data.points[0]
    b = replace(
        a,
        point_id="B",
        cell_id="cell-B",
        origin_node_id="physical-B",
        access_node_id="access-B",
        population=10,
    )
    build = data.entries[0][1].build
    scope = replace(
        old_plan.scope,
        city_id=data.context.city_id,
        versions=data.context.versions,
        search_parameters_id=data.context.search_parameters_id,
    )
    plan = plan_urban_od_week(
        scope,
        (a, b),
        week_start=old_plan.week_start,
        reference_at=old_plan.reference_at,
        calendar=old_plan.calendar,
        rules=load_urban_tau_rules(),
        profile_hash=build.profile_hash,
        evidence_refs=("test-scope",),
    )
    entries = {
        p.point_id: replace(
            data.entries[0][1],
            physical_node_id=p.origin_node_id,
            access_node_id=p.access_node_id,
            correlation_id=p.point_id,
        )
        for p in (a, b)
    }
    exits = {key: replace(e, direction="exit", seconds=120) for key, e in entries.items()}
    rows = []
    for task in plan.tasks():
        req = od_road_request(task, entries, exits)
        network = 420 if task.origin.point_id == "A" else 1620
        rows.append(od_road_observation(task, normalize_road_response(req, response(req, network))))
    population = replace(data.population, point_ids=frozenset({"A", "B"}))
    tau = calculate_urban_tau(
        data.context,
        (a, b),
        population,
        (assemble_urban_od_week(plan, rows),),
        passenger_mode="private_car",
        timezone="UTC",
        reference_at=plan.reference_at,
        rules=load_urban_tau_rules(),
    )
    assert tau.tau_seconds.value == 1200
    car_plan = plan_car_routes(data, categories=("school",))
    result = analyze_car_responses(
        car_plan,
        (response(car_plan.requests[0]),),
        selected_categories={"school": "high"},
        config=load_urban_config(),
        rules=load_urban_journey_rules(),
        thresholds=(tau.to_threshold(),),
    )
    category = result.analysis.modes[0].points[0].categories[0]
    assert category.index.value == 50 and category.count.value == 0


def test_pt_actual_departures_and_interrupted_search() -> None:
    from a2_engine.routing.timetable_models import TimetableEvent, TimetableRun

    plan, _, _, _, _ = make_plan()
    scope = replace(plan.scope, passenger_mode="public_transport")
    ts = TimetableScope(
        scope.versions.snapshot_id,
        scope.versions.graph_version,
        scope.versions.calendar_version,
        "table",
        "foot",
        "transfer",
        "UTC",
        plan.departures[0],
        plan.departures[-1] + timedelta(days=1),
        frozenset({"A", "B"}),
        True,
        True,
        True,
        True,
        True,
        ("synthetic-timetable",),
    )
    departure = plan.departures[0]
    run = TimetableRun(
        ts.snapshot_id,
        "source",
        "trip",
        departure.date(),
        "UTC",
        "bus",
        (
            TimetableEvent(
                0,
                "A",
                departure + timedelta(seconds=300),
                departure + timedelta(seconds=300),
                True,
                True,
                ("test-run",),
            ),
            TimetableEvent(
                1,
                "B",
                departure + timedelta(seconds=900),
                departure + timedelta(seconds=900),
                True,
                True,
                ("test-run",),
            ),
        ),
        True,
        ("test-run",),
    )
    table = Timetable(ts, (run,), (TimetableFootpath("walk", "A", "B", 1800, True, ("foot",)),), ())
    plan = plan_urban_od_week(
        scope,
        plan.points,
        week_start=plan.week_start,
        reference_at=plan.reference_at,
        calendar=plan.calendar,
        rules=load_urban_tau_rules(),
        profile_hash=road_hash(table),
        evidence_refs=plan.evidence_refs,
    )
    forward = [t for t in plan.tasks() if t.origin.point_id == "A"]
    assert od_timetable_observation(forward[0], table).pair.seconds == 900
    assert od_timetable_observation(forward[1], table).pair.seconds == 1800
    interrupted = od_timetable_observation(forward[0], table, resource_limit=1)
    assert not interrupted.pair.search_complete and interrupted.pair.seconds is None


def test_canonical_task_identity_across_hash_seeds() -> None:
    import os
    import subprocess
    import sys

    source = """import runpy
x=runpy.run_path("packages/engine/examples/synthetic_od.py")
p,*_=x["synthetic_plan"]()
print(p.plan_id,next(p.tasks()).task_id)
"""
    results = [
        subprocess.check_output(
            [sys.executable, "-c", source],
            cwd=EXAMPLES.parents[2],
            env=os.environ | {"PYTHONHASHSEED": seed},
            text=True,
        )
        for seed in ("1", "7", "123")
    ]
    assert len(set(results)) == 1


@pytest.mark.parametrize("excluded", [None, 1.0])
def test_lost_population_never_redistributed(excluded: float | None) -> None:
    plan, entries, exits, context, population = make_plan()
    week = assemble_urban_od_week(plan, observations(plan, entries, exits))
    population = replace(population, complete=False, excluded_population=excluded)
    tau = calculate_urban_tau(
        context,
        plan.points,
        population,
        (week,),
        passenger_mode="private_car",
        timezone="UTC",
        reference_at=plan.reference_at,
        rules=load_urban_tau_rules(),
    )
    assert tau.tau_seconds.value is None
    assert [p.population for p in tau.points] == [90, 10]
