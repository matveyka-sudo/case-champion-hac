"""Full-delivery completion controls and exhaustive raw-action oracle."""

import importlib.util
import random
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from a2_engine.freight.delivery.models import CargoState, CargoWaitPolicy
from a2_engine.freight.delivery.search import analyze_intercity_cargo
from a2_engine.shared.config import (
    cargo_delivery_rules_from_mapping,
    load_cargo_delivery_rules,
    load_freight_config,
)

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/synthetic_delivery.py"
spec = importlib.util.spec_from_file_location("delivery_demo", EXAMPLE)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def analyze(*, actions: Any = None, scope: Any = None, profile: Any = None, **kwargs: Any) -> Any:
    original_scope, states, original_actions, ready = m.demo_inputs()
    return analyze_intercity_cargo(
        scope or original_scope,
        m.START,
        profile or m.PROFILE,
        states,
        original_actions if actions is None else actions,
        ready,
        config=load_freight_config(),
        rules=load_cargo_delivery_rules(),
        **kwargs,
    )


def test_control_nonoverlapping_full_day_delivery() -> None:
    result = m.run_demo()
    best = result.overall.best_known
    assert best.evaluation.total_seconds.value == 86400 and best.evaluation.index.value == 50
    assert best.known_movement_seconds == 57600
    assert best.category == "mixed"
    assert best.action_ids == ("load", "road", "transfer", "rail", "unload")
    assert all(a.end_at == b.start_at for a, b in zip(best.intervals, best.intervals[1:]))


def test_later_vehicle_arrival_wins_with_faster_final_processing() -> None:
    scope, states, actions, ready = m.demo_inputs()
    origin, loaded = states[:2]
    states = (
        origin,
        loaded,
        CargoState("late-prepared", "A", "prepared", "later", "ambient"),
        CargoState("arrival-fast", "B", "in_transit", "road-service", "ambient"),
        CargoState("arrival-slow", "B", "in_transit", "later", "ambient"),
        CargoState("done", "B", "completed", None, "delivered"),
    )
    load = replace(actions[0], seconds=0, scheduled_at=m.START, wait_policy=None)
    load_later = replace(
        load,
        action_id="load-later",
        to_state_id="late-prepared",
        service_id="later",
        compatibility=m.receipt("A", "A", "later"),
    )
    fast = replace(actions[1], to_state_id="arrival-fast", seconds=10 * 3600, scheduled_at=m.START)
    slow = replace(
        fast,
        action_id="later",
        from_state_id="late-prepared",
        to_state_id="arrival-slow",
        service_id="later",
        seconds=12 * 3600,
        compatibility=m.receipt("A", "B", "later"),
    )
    fast = replace(fast, compatibility=m.receipt("A", "B", "road-service"))
    unload_fast = replace(
        actions[-1],
        from_state_id="arrival-fast",
        seconds=8 * 3600,
        service_id="road-service",
        compatibility=m.receipt("B", "B", "road-service"),
    )
    unload_slow = replace(
        unload_fast,
        action_id="unload-later",
        from_state_id="arrival-slow",
        seconds=3600,
        service_id="later",
        compatibility=m.receipt("B", "B", "later"),
    )
    result = analyze_intercity_cargo(
        scope,
        m.START,
        m.PROFILE,
        states,
        (load, load_later, fast, slow, unload_fast, unload_slow),
        ready,
        config=load_freight_config(),
        rules=load_cargo_delivery_rules(),
    )
    assert result.overall.best_known is not None
    assert result.overall.best_known.completed_at == m.START + timedelta(hours=13)
    assert "later" in result.overall.best_known.action_ids


def test_unknown_final_handling_keeps_movement_without_total() -> None:
    _, _, actions, _ = m.demo_inputs()
    result = analyze(
        actions=tuple(replace(a, seconds=None) if a.action_id == "unload" else a for a in actions)
    )
    assert result.overall.best_known is None and result.connection_state == "unknown"
    path = result.incomplete_paths[0]
    assert path.known_movement_seconds == path.evaluation.movement_seconds.value == 57600
    assert path.evaluation.total_seconds.value is path.evaluation.index.value is None
    assert path.reason_code == "REQUIRED_CARGO_COMPONENT_UNKNOWN"


@pytest.mark.parametrize(
    "state,expected", [("forbidden", "confirmed_absent"), ("unknown", "unknown")]
)
def test_compatibility_forbidden_distinct_from_unknown(state: str, expected: str) -> None:
    _, _, actions, _ = m.demo_inputs()
    actions = tuple(
        replace(a, compatibility=replace(a.compatibility, state=cast(Any, state)))
        if a.action_id == "road"
        else a
        for a in actions
    )
    result = analyze(actions=actions)
    assert result.connection_state == expected
    if state == "forbidden":
        assert result.excluded_actions and not result.incomplete_paths
    else:
        assert result.incomplete_paths


def test_loading_finishes_before_departure_and_initial_wait_excludes_it() -> None:
    _, _, actions, _ = m.demo_inputs()
    actions = tuple(
        replace(a, scheduled_at=m.START) if a.action_id == "load" else a for a in actions
    )
    road = next(a for a in actions if a.action_id == "road")
    early = replace(road, action_id="too-early", scheduled_at=m.START + timedelta(hours=1))
    road = replace(
        road, wait_policy=CargoWaitPolicy("initial_wait", m.receipt("A", "A", "road-service"))
    )
    actions = tuple(road if a.action_id == "road" else a for a in actions) + (early,)
    result = analyze(actions=actions)
    assert result.overall.best_known is not None
    assert "too-early" not in result.overall.best_known.action_ids
    assert (
        sum(i.seconds for i in result.overall.best_known.intervals if i.kind == "initial_wait")
        == 3600
    )


@pytest.mark.parametrize("urgency,hours", [("urgent", 24), ("ordinary", 72), ("nonurgent", 744)])
@pytest.mark.parametrize("multiple,expected", [(1, 50), (2, 25)])
def test_configured_half_life_and_31_day_window(
    urgency: str, hours: int, multiple: int, expected: int
) -> None:
    scope, _, actions, _ = m.demo_inputs()
    # Original chain before final operation lasts 22h; change terminal handling to reach D/2D.
    actions = tuple(
        replace(
            a,
            seconds=hours * multiple * 3600 - 22 * 3600,
            compatibility=replace(a.compatibility, valid_to=m.START + timedelta(days=100)),
        )
        if a.action_id == "unload"
        else a
        for a in actions
    )
    scope = replace(scope, end_at=m.START + timedelta(days=100))
    result = analyze(actions=actions, scope=scope, profile=replace(m.PROFILE, urgency=urgency))
    assert result.overall.best_known.evaluation.index.value == expected
    assert result.first_departures_end_at == m.START + timedelta(days=31)


def test_first_window_excludes_right_boundary_and_allows_later_completion() -> None:
    _, _, actions, _ = m.demo_inputs()
    for delta, allowed in (
        (timedelta(days=31) - timedelta(microseconds=1), True),
        (timedelta(days=31), False),
    ):
        load = replace(actions[0], scheduled_at=m.START, seconds=0, wait_policy=None)
        road = replace(
            actions[1],
            scheduled_at=m.START + delta,
            wait_policy=CargoWaitPolicy("initial_wait", m.receipt("A", "A", "road-service")),
        )
        # Rail continues later than the first-departure window.
        rail = replace(actions[3], scheduled_at=m.START + delta + timedelta(hours=10))
        data = (load, road, actions[2], rail, actions[4])
        result = analyze(actions=data)
        assert (result.overall.best_known is not None) == allowed
        if allowed:
            assert result.overall.best_known.completed_at > m.START + timedelta(days=31)


def test_unknown_wait_coverage_partial_search_and_resource_limits() -> None:
    scope, _, actions, _ = m.demo_inputs()
    unknown_wait = tuple(
        replace(a, wait_policy=None) if a.action_id == "rail" else a for a in actions
    )
    assert analyze(actions=unknown_wait).overall.best_known is None
    partial = analyze(scope=replace(scope, services_complete=False))
    assert partial.overall.best_known.evaluation.index.value == 50
    assert not partial.overall.optimum_proven
    interrupted = analyze(resource_limit=0)
    assert interrupted.interrupted and interrupted.connection_state == "unknown"
    assert not interrupted.search_complete and not interrupted.full_ranking_eligible
    known = analyze(resource_limit=5)
    assert known.overall.best_known is not None and known.interrupted
    assert not known.overall.optimum_proven


def test_profile_content_version_and_execution_bindings_rejected() -> None:
    _, _, actions, _ = m.demo_inputs()
    for field, value in (
        ("requirements_hash", "other"),
        ("profile_version", "old"),
        ("from_id", "other"),
    ):
        changed = replace(
            actions[0], compatibility=replace(actions[0].compatibility, **{field: value})
        )
        with pytest.raises(ValueError, match="BINDING_MISMATCH"):
            analyze(actions=(changed,) + actions[1:])
    with pytest.raises(ValueError, match="PREPARED_EXECUTION"):
        analyze(
            actions=tuple(
                replace(a, from_state_id="origin") if a.action_id == "road" else a for a in actions
            )
        )
    with pytest.raises(ValueError):
        replace(actions[0], seconds=float("nan"))
    with pytest.raises(ValueError):
        replace(actions[0], mode=cast(Any, "road"))


def test_confirmed_absent_zero_operation_and_permutation() -> None:
    _, _, actions, _ = m.demo_inputs()
    actions = tuple(
        replace(a, seconds=0, necessity="not_required") if a.action_id == "unload" else a
        for a in actions
    )
    result = analyze(actions=actions)
    assert result.overall.best_known.completed_at == m.START + timedelta(hours=22)
    assert result == analyze(actions=tuple(reversed(actions)))
    assert (
        result.by_mode
        and next(r for r in result.by_mode if r.category == "mixed").best_known is not None
    )


def test_rules_loader_and_missing_departure_days(product: dict[str, Any]) -> None:
    assert cargo_delivery_rules_from_mapping(product).departure_window_days == 31
    for value in (True, 0, -1, "31", float("nan")):
        product["methodologies"]["intercity_cargo"]["parameters"]["departure_window_days"] = value
        with pytest.raises(ValueError):
            cargo_delivery_rules_from_mapping(product)


def test_independent_exhaustive_completion_oracle() -> None:
    rng = random.Random(2264)
    for case in range(25):
        scope, states, actions, ready = m.demo_inputs()
        actions = tuple(
            replace(
                a,
                seconds=rng.randrange(1, 600),
                scheduled_at=m.START + timedelta(seconds=2000)
                if a.action_id == "road"
                else m.START + timedelta(seconds=5000)
                if a.action_id == "rail"
                else m.START
                if a.action_id == "load"
                else None,
                wait_policy=None
                if a.action_id == "load"
                else CargoWaitPolicy(
                    "initial_wait" if a.action_id == "road" else "transfer_wait",
                    m.receipt(
                        "A" if a.action_id == "road" else "X",
                        "A" if a.action_id == "road" else "X",
                        a.service_id,
                    ),
                )
                if a.action_id in ("road", "rail")
                else None,
            )
            for a in actions
        )
        alternatives = tuple(
            replace(
                a,
                action_id=a.action_id + f":alt:{case}",
                seconds=float(a.seconds) + rng.randrange(1, 600),
            )
            for a in actions
            if a.kind == "handling"
        )
        all_actions = actions + alternatives
        # Raw DFS, no engine labels/queue/hash or scalar summation used.
        found: list[tuple[object, tuple[str, ...], dict[str, float]]] = []
        by_state = {s.state_id: s for s in states}

        def enumerate_paths(
            state: str,
            at: Any,
            used: frozenset[str],
            sequence: tuple[str, ...],
            totals: dict[str, float],
            moved: bool,
        ) -> None:
            if by_state[state].stage == "completed":
                found.append((at, sequence, totals))
                return
            for action in all_actions:
                if action.from_state_id != state or action.action_id in used:
                    continue
                departure = action.scheduled_at or at
                if departure < at:
                    continue
                assert action.seconds is not None
                next_totals = dict(totals)
                wait_kind = "transfer_wait" if moved else "initial_wait"
                next_totals[wait_kind] += (departure - at).total_seconds()
                next_totals[action.kind] += action.seconds
                enumerate_paths(
                    action.to_state_id,
                    departure + timedelta(seconds=action.seconds),
                    used | {action.action_id},
                    sequence + (action.action_id,),
                    next_totals,
                    moved or action.kind == "movement",
                )

        enumerate_paths(
            "origin",
            m.START,
            frozenset(),
            (),
            dict(initial_wait=0, movement=0, handling=0, transfer_wait=0),
            False,
        )
        expected = min(found, key=lambda p: (p[0], len(p[1]), p[1]))
        result = analyze(actions=all_actions)
        best = result.overall.best_known
        assert best.completed_at == expected[0] and best.action_ids == expected[1]
        assert {
            kind: sum(i.seconds for i in best.intervals if i.kind == kind) for kind in expected[2]
        } == expected[2]
        assert best.evaluation.total_seconds.value == sum(expected[2].values())


def test_zero_handling_cycle_fractional_seconds_and_independent_movement_modes() -> None:
    _, _, actions, _ = m.demo_inputs()
    load = replace(actions[0], seconds=0.125, scheduled_at=m.START, wait_policy=None)
    road = replace(
        actions[1], wait_policy=CargoWaitPolicy("initial_wait", m.receipt("A", "A", "road-service"))
    )
    loop = replace(
        load,
        action_id="zero-cycle",
        from_state_id="loaded-road",
        to_state_id="loaded-road",
        seconds=0,
        scheduled_at=None,
        necessity="not_required",
    )
    result = analyze(actions=(load, road) + actions[2:] + (loop,))
    assert result.overall.best_known is not None and result.search_complete
    assert "zero-cycle" not in result.overall.best_known.action_ids
    assert result.overall.best_known.intervals[0].seconds == 0.125
    assert result.overall.best_known.evaluation.total_seconds.value == 86400
    # Several candidates of the same physical mode and a mixed alternative coexist.
    scope, states, data, ready = m.demo_inputs()
    direct_loaded = CargoState("direct-loaded", "A", "prepared", "direct", "ambient")
    direct_arrival = CargoState("direct-arrival", "B", "in_transit", "direct", "ambient")
    dload = replace(
        data[0],
        action_id="dload",
        to_state_id="direct-loaded",
        service_id="direct",
        compatibility=m.receipt("A", "A", "direct"),
        wait_policy=CargoWaitPolicy("initial_wait", m.receipt("A", "A", "direct")),
    )
    move = replace(
        data[1],
        action_id="direct",
        from_state_id="direct-loaded",
        to_state_id="direct-arrival",
        service_id="direct",
        seconds=30 * 3600,
        compatibility=m.receipt("A", "B", "direct"),
    )
    unload = replace(
        data[-1],
        action_id="dunload",
        from_state_id="direct-arrival",
        service_id="direct",
        compatibility=m.receipt("B", "B", "direct"),
    )
    result = analyze_intercity_cargo(
        scope,
        m.START,
        m.PROFILE,
        states + (direct_loaded, direct_arrival),
        data + (dload, move, unload),
        ready,
        config=load_freight_config(),
        rules=load_cargo_delivery_rules(),
    )
    assert {r.category for r in result.by_mode if r.best_known is not None} == {"road", "mixed"}
    assert result.overall.best_known is not None and result.overall.best_known.category == "mixed"


def test_utc_dst_and_exact_timestamp_resolution() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from a2_engine.shared.normalized import duration, utc

    zone = ZoneInfo("America/New_York")
    a = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=0)
    b = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=1)
    assert (utc(b) - utc(a)).total_seconds() == 3600
    _, _, actions, _ = m.demo_inputs()
    localized = tuple(
        replace(
            action,
            scheduled_at=action.scheduled_at.astimezone(zone) if action.scheduled_at else None,
        )
        for action in actions
    )
    assert analyze(actions=localized) == analyze(actions=actions)
    with pytest.raises(ValueError, match="MICROSECOND"):
        duration(0.0000015)
    with pytest.raises(ValueError):
        analyze(actions=actions + (actions[0],))


def test_unknown_initial_readiness_and_service_calendar() -> None:
    scope, states, actions, ready = m.demo_inputs()
    unknown = replace(ready[0], compatibility=replace(ready[0].compatibility, state="unknown"))
    result = analyze_intercity_cargo(
        scope,
        m.START,
        m.PROFILE,
        states,
        actions,
        (unknown,),
        config=load_freight_config(),
        rules=load_cargo_delivery_rules(),
    )
    assert result.connection_state == "unknown" and result.overall.best_known is None
    inactive = tuple(replace(a, active=False) if a.action_id == "road" else a for a in actions)
    assert analyze(actions=inactive).connection_state == "confirmed_absent"
    unknown_actions = tuple(
        replace(a, active=None) if a.action_id == "road" else a for a in actions
    )
    assert analyze(actions=unknown_actions).connection_state == "unknown"
